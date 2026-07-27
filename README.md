# streetclip

Takes a long street-preaching recording and hands back upload-ready 9:16 shorts
with burned-in captions. It transcribes the whole session, reads the transcript
against a rubric (confrontation, one-liner, emotional peak, scripture +
application), proposes ranked moments, and lets you approve or trim each one in
a browser before anything is exported.

The model proposes. You decide.

## Pipeline

```
ingest → transcribe → signals → candidates → score → render
```

Each stage is a separate module with plain data between them. Transcription sits
behind a Protocol, so the local-vs-hosted decision is an env var and every
downstream stage is testable against a canned transcript with no network.

## Running with Docker

```sh
cp .env.example .env      # add at least STREETCLIP_ANTHROPIC_API_KEY
mkdir -p input data
cp /path/to/recording.mp4 input/
docker compose up -d
```

Open `http://<host>:8080`, pick the recording, wait for the queue to fill, keep
the clips you want, hit Export.

- `./input` is mounted read-only and is the only directory a job may be
  submitted from by path. Uploads through the browser land in `./data/uploads`.
- `./data` holds the job database, working files, and rendered shorts, so
  exports are reachable on the host at
  `data/job_00001/shorts/01_confrontation_*.mp4` without going through the UI.

### Transcription backend

`STREETCLIP_TRANSCRIBE_BACKEND=groq` (default) sends audio to Groq's
whisper-large-v3-turbo — roughly $0.04 per audio-hour and about 30x realtime.
Long recordings are split into 10-minute FLAC chunks and stitched back onto one
timeline, because the hosted upload cap is well below a 2-hour WAV.

`STREETCLIP_TRANSCRIBE_BACKEND=local` uses faster-whisper on the CPU instead —
free and offline, but slow. It needs the extra installed in the image:

```sh
STREETCLIP_EXTRAS=groq,reframe,local-whisper docker compose build
```

The model downloads on first use into the container's home directory, so it is
re-fetched if the container is recreated.

### Cost

A 2-hour session runs a few cents of Groq transcription plus a low-tens-of-cents
Claude call. Rendering is local and free.

## Deploying on Proxmox

**Use a VM, not an LXC container.** Docker inside LXC needs `nesting=1`,
`keyctl=1`, and an unprivileged-container storage driver that actually works;
a small Debian VM avoids all of it. Debian 13 with the convenience Docker
install is enough.

Sizing, for 1-2 hour 1080p sources:

| Resource | Suggestion | Why |
|---|---|---|
| vCPU | 4+ | x264 encoding is the wall. Local Whisper wants more. |
| RAM | 4GB (8GB for local Whisper) | MediaPipe and ffmpeg are both modest. |
| Disk | 60GB+ | Source footage, a 16kHz WAV per job (~230MB/hour), plus renders. |

No GPU passthrough is needed — nothing in the pipeline uses CUDA.

**Build for x86_64.** MediaPipe publishes no linux aarch64 wheel with working
face detection, so speaker tracking is x86-only. The renderer degrades to a
fixed center crop when tracking is unavailable rather than failing the export.
On an ARM workstation, build the deployment image with
`docker build --platform linux/amd64`.

The image downloads MediaPipe's `blaze_face_short_range` model at build time and
points `STREETCLIP_FACE_MODEL_PATH` at it: mediapipe 0.10.35 dropped the legacy
`solutions` API, whose model was bundled in the wheel, and the Tasks API that
replaced it wants the model on disk. Older wheels that still have `solutions`
are detected and used as-is, model file or not.

**There is no authentication.** Keep it on the LAN, or put it behind Tailscale or
an authenticating reverse proxy. Do not port-forward it.

Back up `data/streetclip.db` if job history matters; the shorts themselves are
reproducible from the source and the stored report.

## Running without Docker

Needs Python 3.12, plus an ffmpeg built with libass (Homebrew's default `ffmpeg`
formula is **not** — caption burn-in fails with `No such filter: 'subtitles'`).
Point `STREETCLIP_FFMPEG_BIN` at a full build, or set
`STREETCLIP_RENDER_CAPTIONS=false`.

```sh
uv pip install -e ".[groq,reframe]" --group dev
npm --prefix web ci && npm --prefix web run build
streetclip-server            # web UI on :8080
streetclip analyze video.mp4 # or CLI: writes a JSON report and raw 16:9 cuts
```

## Tests

```sh
pytest
```

Everything runs offline. The video fixture is generated with `ffmpeg -f lavfi`,
transcription and scoring are stubbed, and caption output is pinned to a golden
file. Tests needing ffmpeg or libass skip themselves when it is missing.
