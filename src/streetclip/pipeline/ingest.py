"""Probe the source video and extract audio for transcription.

Kept deliberately thin: every ffmpeg invocation in the project funnels through
`run_ffmpeg` so failures surface with the actual stderr rather than a bare
non-zero exit code.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from streetclip.config import Settings, get_settings
from streetclip.models import MediaInfo


class FFmpegError(RuntimeError):
    """An ffmpeg/ffprobe call failed. Carries the tail of stderr."""


def run_ffmpeg(args: list[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"{args[0]} failed (exit {proc.returncode}):\n{tail}")
    return proc.stdout


def _parse_fps(rate: str) -> float:
    """ffprobe reports frame rates as fractions like '30000/1001'."""
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def probe(path: Path, settings: Settings | None = None) -> MediaInfo:
    settings = settings or get_settings()
    if not path.exists():
        raise FileNotFoundError(path)

    raw = run_ffmpeg(
        [
            settings.ffprobe_bin,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    data = json.loads(raw)

    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise FFmpegError(f"no video stream found in {path}")

    # Container duration is more reliable than the stream's for long recordings.
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    if duration <= 0:
        raise FFmpegError(f"could not determine duration of {path}")

    return MediaInfo(
        path=str(path),
        duration=duration,
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=_parse_fps(str(video.get("avg_frame_rate", "0/1"))),
    )


def extract_audio(path: Path, dest: Path, settings: Settings | None = None) -> Path:
    """Write 16kHz mono PCM WAV — what both Whisper backends and `signals` want."""
    settings = settings or get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            settings.ffmpeg_bin,
            "-v", "error",
            "-y",
            "-i", str(path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(dest),
        ]
    )
    return dest


def split_audio(
    audio: Path,
    dest_dir: Path,
    chunk_seconds: float,
    settings: Settings | None = None,
) -> list[tuple[Path, float]]:
    """Split a WAV into FLAC pieces, returning (path, start_offset) pairs.

    Hosted transcription APIs cap upload size far below a 2-hour recording
    (a 2h 16kHz mono WAV is ~230MB), so long inputs must be sent in pieces and
    the returned timestamps shifted back by each piece's offset. FLAC keeps the
    audio lossless while cutting size enough to stay under the cap.
    """
    settings = settings or get_settings()
    dest_dir.mkdir(parents=True, exist_ok=True)
    pattern = dest_dir / "chunk_%05d.flac"

    run_ffmpeg(
        [
            settings.ffmpeg_bin,
            "-v", "error",
            "-y",
            "-i", str(audio),
            "-f", "segment",
            "-segment_time", f"{chunk_seconds:.3f}",
            # Force segments to start exactly on the requested interval so the
            # offset we compute below is the true offset.
            "-reset_timestamps", "1",
            "-c:a", "flac",
            str(pattern),
        ]
    )

    chunks = sorted(dest_dir.glob("chunk_*.flac"))
    if not chunks:
        raise FFmpegError(f"splitting {audio} produced no chunks")
    return [(p, i * chunk_seconds) for i, p in enumerate(chunks)]


def cut(
    source: Path,
    dest: Path,
    start: float,
    end: float,
    settings: Settings | None = None,
) -> Path:
    """Trim [start, end) losslessly-ish for Phase 1 raw review cuts.

    Re-encodes rather than stream-copying: stream copy snaps to the nearest
    keyframe, which would undo the word-boundary snapping we just did.
    """
    settings = settings or get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            settings.ffmpeg_bin,
            "-v", "error",
            "-y",
            "-ss", f"{start:.3f}",
            "-i", str(source),
            "-t", f"{max(0.0, end - start):.3f}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(dest),
        ]
    )
    return dest
