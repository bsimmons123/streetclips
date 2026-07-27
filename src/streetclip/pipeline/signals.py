"""Audio loudness as a per-second series.

This exists to give the scoring model something the transcript cannot carry:
delivery. A line reads flat on the page and lands hard when shouted. The series
is annotated into the prompt rather than used as a filter, so quiet-but-good
moments are never silently discarded.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from streetclip.models import EnergySeries

# Loudness is compared against the recording's own range, not an absolute dB
# level — street recordings vary wildly in gain between cameras and locations.
QUIET_PERCENTILE = 10.0
LOUD_PERCENTILE = 95.0

HIGH_ENERGY_THRESHOLD = 0.72
MIN_SPAN_SECONDS = 2.0


def _read_mono_samples(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"expected 16-bit PCM, got {wf.getsampwidth() * 8}-bit: {path}")
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return samples, rate


def compute_energy(audio_path: Path) -> EnergySeries:
    """Per-second RMS loudness, normalized to roughly [0, 1].

    Normalization is percentile-based so a single clipped shout or a stretch of
    dead air does not compress everything else into a narrow band.
    """
    samples, rate = _read_mono_samples(audio_path)
    if samples.size == 0:
        return EnergySeries(values=[])

    seconds = int(np.ceil(samples.size / rate))
    padded = np.zeros(seconds * rate, dtype=np.float32)
    padded[: samples.size] = samples
    frames = padded.reshape(seconds, rate)

    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    # dB compresses the huge dynamic range of speech into something linear-ish
    # that percentile scaling behaves sensibly on.
    db = 20.0 * np.log10(np.maximum(rms, 1e-6))

    quiet = float(np.percentile(db, QUIET_PERCENTILE))
    loud = float(np.percentile(db, LOUD_PERCENTILE))
    if loud - quiet < 1e-6:
        # Constant-loudness audio: nothing to distinguish, report flat mid-scale.
        return EnergySeries(values=[0.5] * seconds)

    normalized = np.clip((db - quiet) / (loud - quiet), 0.0, 1.0)
    return EnergySeries(values=[round(float(v), 4) for v in normalized])


def high_energy_spans(
    energy: EnergySeries,
    threshold: float = HIGH_ENERGY_THRESHOLD,
    min_seconds: float = MIN_SPAN_SECONDS,
) -> list[tuple[float, float]]:
    """Contiguous runs above `threshold`, discarding blips shorter than `min_seconds`."""
    spans: list[tuple[float, float]] = []
    start: int | None = None

    for i, value in enumerate(energy.values):
        if value >= threshold and start is None:
            start = i
        elif value < threshold and start is not None:
            if i - start >= min_seconds:
                spans.append((float(start), float(i)))
            start = None

    if start is not None and len(energy.values) - start >= min_seconds:
        spans.append((float(start), float(len(energy.values))))

    return spans
