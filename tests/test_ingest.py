from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from streetclip.config import get_settings
from streetclip.pipeline import ingest
from streetclip.pipeline.ingest import FFmpegError

from .conftest import needs_ffmpeg


def test_parse_fps_handles_fractions_and_plain_values():
    assert ingest._parse_fps("25/1") == 25.0
    assert round(ingest._parse_fps("30000/1001"), 3) == 29.97
    assert ingest._parse_fps("24") == 24.0


def test_parse_fps_survives_garbage():
    # ffprobe reports 0/0 for streams with no meaningful frame rate.
    assert ingest._parse_fps("0/0") == 0.0
    assert ingest._parse_fps("N/A") == 0.0


def test_probe_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ingest.probe(tmp_path / "nope.mp4")


@needs_ffmpeg
def test_probe_reads_media_info(sample_video: Path):
    info = ingest.probe(sample_video)
    assert info.width == 640
    assert info.height == 360
    assert round(info.fps) == 25
    assert 9.5 <= info.duration <= 10.5


@needs_ffmpeg
def test_probe_rejects_audio_only_input(sample_video: Path, tmp_path: Path):
    audio = ingest.extract_audio(sample_video, tmp_path / "a.wav")
    with pytest.raises(FFmpegError, match="no video stream"):
        ingest.probe(audio)


@needs_ffmpeg
def test_extract_audio_is_16k_mono_pcm(sample_video: Path, tmp_path: Path):
    dest = ingest.extract_audio(sample_video, tmp_path / "nested" / "out.wav")
    assert dest.exists()
    with wave.open(str(dest)) as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000
        assert wf.getsampwidth() == 2
        assert 9.5 <= wf.getnframes() / 16000 <= 10.5


@needs_ffmpeg
def test_cut_produces_requested_duration(sample_video: Path, tmp_path: Path):
    dest = ingest.cut(sample_video, tmp_path / "clip.mp4", start=2.0, end=5.0)
    assert 2.7 <= ingest.probe(dest).duration <= 3.3


@needs_ffmpeg
def test_poster_frame_writes_a_jpeg(sample_video: Path, tmp_path: Path):
    dest = ingest.poster_frame(sample_video, tmp_path / "poster.jpg", at=1.0)
    assert dest.is_file()
    assert dest.stat().st_size > 0
    assert dest.read_bytes()[:2] == b"\xff\xd8", "not a JPEG"


@needs_ffmpeg
def test_poster_frame_is_downscaled(sample_video: Path, tmp_path: Path):
    """The home list shows thumbnails; full-resolution frames are wasted bytes."""
    dest = ingest.poster_frame(sample_video, tmp_path / "poster.jpg", at=1.0)
    info = json.loads(
        ingest.run_ffmpeg([
            get_settings().ffprobe_bin, "-v", "error", "-print_format", "json",
            "-show_streams", str(dest),
        ])
    )
    assert info["streams"][0]["width"] == 480


@needs_ffmpeg
def test_poster_frame_past_the_end_still_produces_a_file(sample_video: Path, tmp_path: Path):
    """A seek beyond the recording must not leave a zero-byte poster."""
    dest = ingest.poster_frame(sample_video, tmp_path / "poster.jpg", at=9999.0)
    assert dest.is_file() and dest.stat().st_size > 0
