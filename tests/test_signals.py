from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from streetclip.models import EnergySeries
from streetclip.pipeline import ingest, signals

from .conftest import needs_ffmpeg


def _write_wav(path: Path, amplitudes: list[float], rate: int = 16000) -> Path:
    """One second of tone per entry in `amplitudes`."""
    t = np.arange(rate) / rate
    chunks = [(amp * np.sin(2 * np.pi * 440 * t)) for amp in amplitudes]
    data = (np.concatenate(chunks) * 32767).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data.tobytes())
    return path


def test_energy_tracks_loudness_order(tmp_path: Path):
    path = _write_wav(tmp_path / "a.wav", [0.02, 0.2, 0.9, 0.2, 0.02])
    energy = signals.compute_energy(path)

    assert len(energy.values) == 5
    assert energy.values[2] > energy.values[1] > energy.values[0]
    assert all(0.0 <= v <= 1.0 for v in energy.values)


def test_energy_of_constant_audio_is_flat(tmp_path: Path):
    path = _write_wav(tmp_path / "flat.wav", [0.3] * 5)
    assert signals.compute_energy(path).values == [0.5] * 5


def test_energy_rejects_non_16bit(tmp_path: Path):
    path = tmp_path / "8bit.wav"
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * 16000)

    with pytest.raises(ValueError, match="16-bit"):
        signals.compute_energy(path)


def test_high_energy_spans_finds_sustained_runs():
    values = [0.1] * 3 + [0.9] * 5 + [0.1] * 3
    assert signals.high_energy_spans(EnergySeries(values=values)) == [(3.0, 8.0)]


def test_high_energy_spans_ignores_brief_blips():
    # A single loud second is a door slam, not a raised voice.
    values = [0.1, 0.95, 0.1, 0.1]
    assert signals.high_energy_spans(EnergySeries(values=values)) == []


def test_high_energy_span_running_to_end_is_closed():
    values = [0.1, 0.1, 0.9, 0.9, 0.9]
    assert signals.high_energy_spans(EnergySeries(values=values)) == [(2.0, 5.0)]


def test_high_energy_spans_of_empty_series():
    assert signals.high_energy_spans(EnergySeries(values=[])) == []


def test_mean_in_range_clamps_to_bounds():
    energy = EnergySeries(values=[0.0, 1.0, 0.0, 1.0])
    assert energy.mean_in_range(0, 2) == 0.5
    # Out-of-range queries must not raise or silently return garbage.
    assert energy.mean_in_range(-5, 2) == 0.5
    assert energy.mean_in_range(3, 99) == 1.0


@needs_ffmpeg
def test_energy_from_real_extracted_audio(sample_video: Path, tmp_path: Path):
    """The fixture's amplitude envelope should survive the ffmpeg round trip."""
    audio = ingest.extract_audio(sample_video, tmp_path / "a.wav")
    energy = signals.compute_energy(audio)

    assert 9 <= len(energy.values) <= 11
    assert max(energy.values) > min(energy.values), "modulated audio should not read as flat"
