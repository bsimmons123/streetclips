# syntax=docker/dockerfile:1

# --- SPA build ---------------------------------------------------------------
# Vite writes to ../src/streetclip/static, which the Python package ships as its
# static mount, so the built app ends up inside the wheel rather than beside it.
FROM node:22-slim AS web

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


# --- runtime -----------------------------------------------------------------
# Debian's ffmpeg is built with libass, which the caption burn-in requires, and
# fonts-dejavu-core supplies the default caption font. Alpine was ruled out:
# there are no musl wheels for CTranslate2 or MediaPipe.
FROM python:3.12-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        # MediaPipe dlopens GL at detector construction, even headless.
        libgl1 \
        libgles2 \
        libegl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
COPY --from=web /src/streetclip/static ./src/streetclip/static

# Which transcription backend gets installed. `local-whisper` adds CTranslate2
# and roughly 300MB; leave it out when transcribing through Groq.
ARG EXTRAS=groq,reframe
RUN pip install --no-cache-dir ".[${EXTRAS}]"

# Face detection model. Mediapipe 0.10.35 dropped the legacy solutions API,
# whose model was bundled in the wheel; the Tasks API that replaced it wants
# the model on disk. Baked into the image so a render never waits on a download.
ADD --chmod=644 \
    https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite \
    /opt/streetclip/blaze_face_short_range.tflite

RUN useradd --system --create-home --uid 10001 streetclip \
    && chmod 755 /opt/streetclip \
    && mkdir -p /data /input /state /scratch \
    && chown streetclip:streetclip /data /input /state /scratch

ENV PYTHONUNBUFFERED=1 \
    STREETCLIP_HOST=0.0.0.0 \
    STREETCLIP_PORT=8080 \
    STREETCLIP_DATA_DIR=/data \
    STREETCLIP_DATABASE_PATH=/state/streetclip.db \
    STREETCLIP_SCRATCH_DIR=/scratch \
    STREETCLIP_INPUT_DIR=/input \
    STREETCLIP_FACE_MODEL_PATH=/opt/streetclip/blaze_face_short_range.tflite

USER streetclip
EXPOSE 8080
VOLUME ["/data", "/state", "/scratch"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/')"

CMD ["streetclip-server"]
