from __future__ import annotations

import wave
from pathlib import Path

import pytest

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
