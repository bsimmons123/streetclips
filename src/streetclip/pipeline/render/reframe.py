"""Work out where to crop a 16:9 frame to make a 9:16 short.

Detection is behind a Protocol and the path math is pure, so everything that
decides how the crop moves is testable without mediapipe or a video file.

The hard part is not finding the face — it is not chasing it. A crop that
tracks every detection looks like a nervous camera operator, and a brief
mis-detection on a bystander yanks the frame away from the speaker. Hence the
deadzone, the smoothing window, and holding position through dropouts.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from streetclip.config import Settings, get_settings
from streetclip.pipeline.ingest import run_ffmpeg


@dataclass(frozen=True)
class Detection:
    """A face box in pixels, plus the detector's confidence."""

    x: float
    y: float
    width: float
    height: float
    confidence: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2


@dataclass(frozen=True)
class Sample:
    """What the detector saw at one moment. `center_x` is None when nothing was found."""

    time: float
    center_x: float | None


@dataclass(frozen=True)
class Keyframe:
    time: float
    x: float


class FaceDetector(Protocol):
    def detect(self, image) -> list[Detection]: ...


class MediaPipeDetector:
    """Short-range face detection. Imported lazily — mediapipe is an optional dep."""

    def __init__(self, min_confidence: float = 0.5) -> None:
        import mediapipe as mp

        self._detector = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=min_confidence
        )

    def detect(self, image) -> list[Detection]:
        height, width = image.shape[:2]
        result = self._detector.process(image)
        if not result.detections:
            return []

        found = []
        for detection in result.detections:
            box = detection.location_data.relative_bounding_box
            found.append(
                Detection(
                    x=box.xmin * width,
                    y=box.ymin * height,
                    width=box.width * width,
                    height=box.height * height,
                    confidence=detection.score[0] if detection.score else 0.0,
                )
            )
        return found


def pick_subject(detections: list[Detection]) -> Detection | None:
    """Choose the speaker from everyone in frame.

    Largest face wins, not most confident: the person addressing the camera is
    nearer than the crowd behind them, and size survives motion blur better
    than confidence does.
    """
    if not detections:
        return None
    return max(detections, key=lambda d: d.width * d.height)


def sample_frames(
    source: Path,
    start: float,
    end: float,
    detector: FaceDetector,
    settings: Settings | None = None,
) -> list[Sample]:
    """Decode the clip at a low frame rate and detect a face in each frame."""
    settings = settings or get_settings()
    fps = settings.reframe_sample_fps

    with tempfile.TemporaryDirectory() as tmp:
        frame_dir = Path(tmp)
        run_ffmpeg(
            [
                settings.ffmpeg_bin,
                "-v", "error",
                "-y",
                "-ss", f"{start:.3f}",
                "-i", str(source),
                "-t", f"{max(0.0, end - start):.3f}",
                "-vf", f"fps={fps}",
                str(frame_dir / "f_%05d.png"),
            ]
        )

        import numpy as np
        from PIL import Image

        samples: list[Sample] = []
        for i, path in enumerate(sorted(frame_dir.glob("f_*.png"))):
            with Image.open(path) as img:
                array = np.asarray(img.convert("RGB"))
            subject = pick_subject(detector.detect(array))
            samples.append(
                Sample(time=i / fps, center_x=subject.center_x if subject else None)
            )

    return samples


def fill_dropouts(samples: list[Sample], fallback: float) -> list[float]:
    """Replace missed detections by holding the last known position.

    Leading gaps take the first real detection rather than the fallback, so a
    clip that opens on a turned head doesn't start centered and then jump.
    """
    values = [s.center_x for s in samples]
    first_real = next((v for v in values if v is not None), None)
    if first_real is None:
        return [fallback] * len(values)

    filled: list[float] = []
    last = first_real
    for value in values:
        if value is not None:
            last = value
        filled.append(last)
    return filled


def smooth(values: list[float], window: int) -> list[float]:
    """Centered moving average, shrinking the window at the edges."""
    if window <= 1 or not values:
        return list(values)

    half = window // 2
    out: list[float] = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        span = values[lo:hi]
        out.append(sum(span) / len(span))
    return out


def apply_deadzone(values: list[float], threshold: float) -> list[float]:
    """Hold position until the target moves more than `threshold` pixels.

    Without this the crop drifts continuously by a pixel or two, which reads as
    a slow wobble even though the subject is standing still.
    """
    if not values:
        return []

    out = [values[0]]
    for value in values[1:]:
        out.append(value if abs(value - out[-1]) > threshold else out[-1])
    return out


def crop_width_for(height: int, settings: Settings | None = None) -> int:
    """Widest 9:16 window that fits in a frame of this height.

    Rounded to an even number — h264 chroma subsampling rejects odd dimensions.
    """
    settings = settings or get_settings()
    width = round(height * settings.output_width / settings.output_height)
    return width - (width % 2)


def clamp_to_frame(centers: list[float], frame_width: int, crop_width: int) -> list[float]:
    """Turn subject centers into crop left-edges that stay inside the frame."""
    max_x = max(0, frame_width - crop_width)
    return [min(max_x, max(0.0, c - crop_width / 2)) for c in centers]


def to_keyframes(xs: list[float], fps: float, tolerance: float = 1.0) -> list[Keyframe]:
    """Drop points that a straight line through their neighbours already covers.

    The crop path is mostly flat, so this usually collapses hundreds of samples
    into a handful — which keeps the ffmpeg expression small enough to read.
    """
    if not xs:
        return []
    if len(xs) <= 2:
        return [Keyframe(i / fps, x) for i, x in enumerate(xs)]

    kept = [0]
    for i in range(1, len(xs) - 1):
        prev_i, prev_x = kept[-1], xs[kept[-1]]
        next_i, next_x = i + 1, xs[i + 1]
        span = next_i - prev_i
        interpolated = prev_x + (next_x - prev_x) * (i - prev_i) / span
        if abs(xs[i] - interpolated) > tolerance:
            kept.append(i)
    kept.append(len(xs) - 1)

    return [Keyframe(i / fps, xs[i]) for i in kept]


def crop_expression(keyframes: list[Keyframe]) -> str:
    """An ffmpeg crop `x` expression that lerps between keyframes over time.

    ffmpeg evaluates this per frame with `t` bound to presentation time, so the
    crop pans smoothly instead of stepping at each sample.
    """
    if not keyframes:
        return "0"
    if len(keyframes) == 1:
        return f"{keyframes[0].x:.2f}"

    # Built inside out: the innermost branch is the final segment, which also
    # serves as the value for any time past the last keyframe.
    expression = f"{keyframes[-1].x:.2f}"
    for start, end in reversed(list(zip(keyframes, keyframes[1:], strict=False))):
        span = end.time - start.time
        if span <= 0:
            continue
        slope = (end.x - start.x) / span
        segment = f"({start.x:.2f}+{slope:.4f}*(t-{start.time:.3f}))"
        expression = f"if(lt(t,{end.time:.3f}),{segment},{expression})"

    return expression


@dataclass(frozen=True)
class CropPlan:
    """Everything the encoder needs to turn this clip vertical."""

    crop_width: int
    crop_height: int
    x_expression: str
    tracked: bool


def plan(
    samples: list[Sample],
    frame_width: int,
    frame_height: int,
    settings: Settings | None = None,
) -> CropPlan:
    """Turn raw detections into a crop plan.

    Falls back to a fixed center crop when the subject was found in too few
    frames — a path built from three detections across a minute is noise, and a
    steady center crop looks far better than a frame lurching after ghosts.
    """
    settings = settings or get_settings()
    crop_width = crop_width_for(frame_height, settings)
    center_x = max(0.0, (frame_width - crop_width) / 2)

    hit_ratio = (
        sum(1 for s in samples if s.center_x is not None) / len(samples) if samples else 0.0
    )
    if hit_ratio < settings.reframe_min_detection_ratio:
        return CropPlan(crop_width, frame_height, f"{center_x:.2f}", tracked=False)

    centers = fill_dropouts(samples, fallback=frame_width / 2)
    centers = smooth(centers, settings.reframe_smoothing_window)
    centers = apply_deadzone(centers, settings.reframe_deadzone * frame_width)

    xs = clamp_to_frame(centers, frame_width, crop_width)
    keyframes = to_keyframes(xs, settings.reframe_sample_fps)

    return CropPlan(crop_width, frame_height, crop_expression(keyframes), tracked=True)


def plan_for_clip(
    source: Path,
    start: float,
    end: float,
    frame_width: int,
    frame_height: int,
    detector: FaceDetector | None = None,
    settings: Settings | None = None,
) -> CropPlan:
    settings = settings or get_settings()
    detector = detector or MediaPipeDetector()
    samples = sample_frames(source, start, end, detector, settings)
    return plan(samples, frame_width, frame_height, settings)
