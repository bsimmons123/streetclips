"""Environment-driven settings.

Backend selection lives here so the local-vs-cloud transcription decision stays
a config flag rather than an import-time commitment.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class TranscribeBackend(StrEnum):
    GROQ = "groq"
    LOCAL = "local"
    FAKE = "fake"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STREETCLIP_", env_file=".env", extra="ignore")

    transcribe_backend: TranscribeBackend = TranscribeBackend.GROQ

    groq_api_key: str = ""
    groq_model: str = "whisper-large-v3-turbo"

    # Local faster-whisper. int8 keeps CPU-only inference tractable.
    local_whisper_model: str = "small.en"
    local_whisper_compute_type: str = "int8"

    anthropic_api_key: str = ""
    scoring_model: str = "claude-opus-5"
    scoring_effort: str = "high"

    # Chunking. 15 min of speech is well under context limits; the overlap
    # stops a good moment from being cut in half at a chunk seam.
    chunk_seconds: float = 900.0
    chunk_overlap_seconds: float = 120.0

    # Clip shape.
    min_clip_seconds: float = 20.0
    max_clip_seconds: float = 60.0
    boundary_padding_seconds: float = 0.4

    # Two proposals overlapping by more than this are the same moment.
    dedup_overlap_threshold: float = 0.5

    # Output format. 1080x1920 is what every short-form platform wants.
    output_width: int = 1080
    output_height: int = 1920

    # Captions. The font must exist in the render environment — the Docker image
    # installs DejaVu; override if you ship a different one.
    caption_font: str = "DejaVu Sans"
    caption_size: int = 76
    caption_outline: int = 4
    caption_margin_v: int = 420
    caption_words_per_group: int = 4
    caption_max_gap: float = 0.8

    # Render toggles. Captions need an ffmpeg built with libass; turn them off
    # if yours lacks it rather than having every export fail.
    render_captions: bool = True
    render_track_speaker: bool = True

    # Reframe. Sampling at 2fps is enough to track a person who is standing and
    # gesturing; higher just costs decode time.
    reframe_sample_fps: float = 2.0
    reframe_smoothing_window: int = 5
    # Ignore crop moves smaller than this fraction of frame width, so the crop
    # doesn't jitter around a stationary speaker.
    reframe_deadzone: float = 0.02
    reframe_min_detection_ratio: float = 0.25
    # Face model for mediapipe's Tasks API, needed on wheels that dropped the
    # legacy solutions API (0.10.35+). The Docker image downloads it at build
    # time; leave empty when the installed wheel still has solutions.
    face_model_path: str = ""

    # Web service. `input_dir` is the mounted folder the operator picks from;
    # nothing outside it can be submitted by path.
    host: str = "0.0.0.0"
    port: int = 8080
    data_dir: str = "./data"
    input_dir: str = "./input"

    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"


@lru_cache
def get_settings() -> Settings:
    return Settings()
