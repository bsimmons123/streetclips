from __future__ import annotations

from pathlib import Path

import pytest

from streetclip.config import Settings
from streetclip.pipeline.render import reframe
from streetclip.pipeline.render.reframe import Detection, Keyframe, Sample

from .conftest import needs_ffmpeg


def _samples(centers) -> list[Sample]:
    return [Sample(time=i * 0.5, center_x=c) for i, c in enumerate(centers)]


# --- subject selection -------------------------------------------------------


def test_pick_subject_prefers_the_largest_face():
    """The speaker is nearer the camera than the crowd; size beats confidence."""
    background = Detection(x=0, y=0, width=20, height=20, confidence=0.99)
    speaker = Detection(x=500, y=100, width=120, height=140, confidence=0.71)
    assert reframe.pick_subject([background, speaker]) is speaker


def test_pick_subject_of_nothing():
    assert reframe.pick_subject([]) is None


def test_detection_center():
    assert Detection(x=100, y=0, width=50, height=50, confidence=1.0).center_x == 125


# --- dropout handling --------------------------------------------------------


def test_dropouts_hold_the_last_known_position():
    filled = reframe.fill_dropouts(_samples([100.0, None, None, 200.0]), fallback=640.0)
    assert filled == [100.0, 100.0, 100.0, 200.0]


def test_leading_dropouts_take_the_first_real_detection():
    # Otherwise the clip opens centered and then jumps once a face appears.
    filled = reframe.fill_dropouts(_samples([None, None, 300.0]), fallback=640.0)
    assert filled == [300.0, 300.0, 300.0]


def test_all_dropouts_fall_back():
    assert reframe.fill_dropouts(_samples([None, None]), fallback=640.0) == [640.0, 640.0]


def test_fill_dropouts_of_nothing():
    assert reframe.fill_dropouts([], fallback=640.0) == []


# --- smoothing ---------------------------------------------------------------


def test_smoothing_flattens_a_spike():
    values = [100.0, 100.0, 900.0, 100.0, 100.0]
    smoothed = reframe.smooth(values, window=5)
    assert smoothed[2] < 500.0, "a single bad detection should not dominate"


def test_smoothing_preserves_length_and_edges():
    values = [float(i) for i in range(10)]
    smoothed = reframe.smooth(values, window=5)
    assert len(smoothed) == 10
    # Edge windows shrink rather than reading past the ends.
    assert smoothed[0] == sum(values[0:3]) / 3


def test_smoothing_window_of_one_is_a_passthrough():
    assert reframe.smooth([1.0, 2.0, 3.0], window=1) == [1.0, 2.0, 3.0]


def test_smoothing_of_nothing():
    assert reframe.smooth([], window=5) == []


# --- deadzone ----------------------------------------------------------------


def test_deadzone_holds_through_small_moves():
    values = [500.0, 502.0, 498.0, 501.0]
    assert reframe.apply_deadzone(values, threshold=10.0) == [500.0] * 4


def test_deadzone_follows_a_real_move():
    result = reframe.apply_deadzone([500.0, 505.0, 700.0], threshold=10.0)
    assert result == [500.0, 500.0, 700.0]


def test_deadzone_accumulates_from_the_held_value():
    """Drift must be measured against where the crop is, not the previous sample."""
    values = [500.0, 505.0, 510.0, 515.0]
    assert reframe.apply_deadzone(values, threshold=8.0) == [500.0, 500.0, 510.0, 510.0]


# --- geometry ----------------------------------------------------------------


def test_crop_width_is_nine_sixteenths_and_even():
    assert reframe.crop_width_for(1080) == 608  # 1080 * 9/16 = 607.5, rounded to an even 608
    assert reframe.crop_width_for(720) == 404
    assert reframe.crop_width_for(1080) % 2 == 0


def test_clamp_keeps_the_crop_inside_the_frame():
    xs = reframe.clamp_to_frame([0.0, 1920.0, 960.0], frame_width=1920, crop_width=608)
    assert xs[0] == 0.0
    assert xs[1] == 1920 - 608
    assert xs[2] == 960 - 304


def test_clamp_when_crop_is_wider_than_frame():
    # A frame narrower than the target crop can only be pinned at zero.
    assert reframe.clamp_to_frame([100.0], frame_width=400, crop_width=600) == [0.0]


# --- keyframe simplification -------------------------------------------------


def test_a_flat_path_collapses_to_two_keyframes():
    keyframes = reframe.to_keyframes([300.0] * 50, fps=2.0)
    assert len(keyframes) == 2
    assert keyframes[0].x == 300.0


def test_a_linear_ramp_collapses_to_its_endpoints():
    xs = [float(i * 10) for i in range(20)]
    assert len(reframe.to_keyframes(xs, fps=2.0)) == 2


def test_a_direction_change_is_kept():
    xs = [0.0, 50.0, 100.0, 50.0, 0.0]
    keyframes = reframe.to_keyframes(xs, fps=2.0)
    assert any(abs(k.x - 100.0) < 0.01 for k in keyframes), "the turn was flattened away"


def test_keyframe_times_follow_the_sample_rate():
    keyframes = reframe.to_keyframes([0.0, 100.0], fps=2.0)
    assert keyframes[0].time == 0.0
    assert keyframes[1].time == 0.5


def test_keyframes_of_nothing():
    assert reframe.to_keyframes([], fps=2.0) == []


# --- ffmpeg expression -------------------------------------------------------


def _evaluate(expression: str, t: float) -> float:
    """Evaluate a crop expression the way ffmpeg would, to check its shape.

    A small recursive parser rather than eval(): the grammar `crop_expression`
    emits is tiny (nested `if(lt(t,N),A,B)` around linear terms), and parsing it
    explicitly keeps the test honest about what ffmpeg will actually compute.
    """
    expression = expression.strip()

    if expression.startswith("if("):
        condition, consequent, alternative = _split_args(expression[3:-1])
        # Condition is always `lt(t,N)`.
        _, threshold = _split_args(condition[3:-1])
        if t < float(threshold):
            return _evaluate(consequent, t)
        return _evaluate(alternative, t)

    if expression.startswith("("):
        # A linear term: `(BASE+SLOPE*(t-START))`
        base, rest = expression[1:-1].split("+", 1)
        slope, offset = rest.split("*", 1)
        start = offset.strip()[len("(t-") : -1]
        return float(base) + float(slope) * (t - float(start))

    return float(expression)


def _split_args(text: str) -> list[str]:
    """Split on commas at paren depth zero."""
    args, depth, current = [], 0, ""
    for char in text:
        if char == "," and depth == 0:
            args.append(current)
            current = ""
            continue
        depth += (char == "(") - (char == ")")
        current += char
    args.append(current)
    return args


def test_expression_for_a_static_crop():
    assert reframe.crop_expression([Keyframe(0.0, 250.0)]) == "250.00"


def test_expression_for_nothing():
    assert reframe.crop_expression([]) == "0"


def test_expression_interpolates_between_keyframes():
    keyframes = [Keyframe(0.0, 0.0), Keyframe(10.0, 100.0)]
    expression = reframe.crop_expression(keyframes)
    assert abs(_evaluate(expression, 0.0) - 0.0) < 0.01
    assert abs(_evaluate(expression, 5.0) - 50.0) < 0.01
    assert abs(_evaluate(expression, 10.0) - 100.0) < 0.01


def test_expression_holds_the_final_value_past_the_last_keyframe():
    expression = reframe.crop_expression([Keyframe(0.0, 0.0), Keyframe(10.0, 100.0)])
    assert abs(_evaluate(expression, 999.0) - 100.0) < 0.01


def test_expression_across_three_segments():
    keyframes = [Keyframe(0.0, 0.0), Keyframe(10.0, 100.0), Keyframe(20.0, 0.0)]
    expression = reframe.crop_expression(keyframes)
    assert abs(_evaluate(expression, 5.0) - 50.0) < 0.01
    assert abs(_evaluate(expression, 15.0) - 50.0) < 0.01


def test_expression_skips_zero_length_segments():
    keyframes = [Keyframe(0.0, 10.0), Keyframe(0.0, 20.0), Keyframe(5.0, 30.0)]
    # A duplicate timestamp would divide by zero if it were not skipped.
    assert reframe.crop_expression(keyframes)


# --- planning ----------------------------------------------------------------


def test_plan_tracks_a_reliably_detected_subject():
    plan = reframe.plan(_samples([400.0] * 20), frame_width=1920, frame_height=1080)
    assert plan.tracked
    assert plan.crop_width == 608
    assert plan.crop_height == 1080


def test_plan_falls_back_to_center_when_detection_is_sparse():
    """Three hits in twenty frames is noise — a steady center crop looks better."""
    plan = reframe.plan(
        _samples([400.0, None, None, None, None, None, None, None, 400.0, None]),
        frame_width=1920,
        frame_height=1080,
    )
    assert not plan.tracked
    assert plan.x_expression == f"{(1920 - 608) / 2:.2f}"


def test_plan_with_no_samples_at_all_centers():
    plan = reframe.plan([], frame_width=1920, frame_height=1080)
    assert not plan.tracked


def test_plan_respects_the_configured_detection_threshold():
    settings = Settings(reframe_min_detection_ratio=0.1)
    samples = _samples([400.0] + [None] * 9)
    assert reframe.plan(samples, 1920, 1080, settings).tracked


def test_plan_output_stays_within_the_frame():
    # Subject pinned at the far right edge for the whole clip.
    plan = reframe.plan(_samples([1900.0] * 20), frame_width=1920, frame_height=1080)
    assert _evaluate(plan.x_expression, 1.0) <= 1920 - plan.crop_width + 0.01


# --- frame sampling (real ffmpeg) --------------------------------------------


class _StubDetector:
    """Reports a face drifting left-to-right, and nothing on every third frame."""

    def __init__(self):
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        if self.calls % 3 == 0:
            return []
        return [
            Detection(
                x=self.calls * 10.0, y=0.0, width=100.0, height=100.0, confidence=0.9
            )
        ]


@needs_ffmpeg
def test_sample_frames_decodes_at_the_configured_rate(sample_video: Path):
    settings = Settings(reframe_sample_fps=2.0)
    detector = _StubDetector()
    samples = reframe.sample_frames(sample_video, 0.0, 5.0, detector, settings)

    # 5 seconds at 2fps, allowing one frame of slack at the boundary.
    assert 9 <= len(samples) <= 11
    assert detector.calls == len(samples)
    assert samples[0].time == 0.0
    assert samples[1].time == 0.5


@needs_ffmpeg
def test_sample_frames_records_dropouts_as_none(sample_video: Path):
    settings = Settings(reframe_sample_fps=2.0)
    samples = reframe.sample_frames(sample_video, 0.0, 5.0, _StubDetector(), settings)
    assert any(s.center_x is None for s in samples)
    assert any(s.center_x is not None for s in samples)


@needs_ffmpeg
def test_plan_for_clip_end_to_end(sample_video: Path):
    plan = reframe.plan_for_clip(
        sample_video, 0.0, 5.0, frame_width=640, frame_height=360,
        detector=_StubDetector(), settings=Settings(reframe_sample_fps=2.0),
    )
    assert plan.tracked
    assert plan.crop_width == reframe.crop_width_for(360)
    assert plan.crop_height == 360


# --- detector selection ------------------------------------------------------


def _fake_mediapipe(monkeypatch, *, solutions: bool):
    """Stand in for whichever mediapipe wheel is installed."""
    import sys
    import types

    module = types.ModuleType("mediapipe")
    if solutions:
        detection = types.SimpleNamespace(FaceDetection=lambda **kwargs: object())
        module.solutions = types.SimpleNamespace(face_detection=detection)
    monkeypatch.setitem(sys.modules, "mediapipe", module)
    return module


def test_get_detector_prefers_the_solutions_api(monkeypatch):
    _fake_mediapipe(monkeypatch, solutions=True)
    assert isinstance(reframe.get_detector(Settings()), reframe.MediaPipeDetector)


def test_get_detector_falls_back_to_tasks_with_a_model(monkeypatch, tmp_path):
    """Wheels from 0.10.35 dropped solutions; the Tasks API needs a model file."""
    _fake_mediapipe(monkeypatch, solutions=False)
    model = tmp_path / "face.tflite"
    model.write_bytes(b"not really a model")

    built = {}
    monkeypatch.setattr(
        reframe,
        "MediaPipeTasksDetector",
        lambda path, confidence=0.5: built.setdefault("path", path),
    )
    reframe.get_detector(Settings(face_model_path=str(model)))
    assert built["path"] == model


def test_get_detector_without_solutions_or_a_model(monkeypatch):
    """Unavailable tracking must read as ImportError, so rendering degrades."""
    _fake_mediapipe(monkeypatch, solutions=False)
    with pytest.raises(ImportError, match="face detection model"):
        reframe.get_detector(Settings())


def test_tasks_detector_rejects_a_missing_model(monkeypatch, tmp_path):
    _fake_mediapipe(monkeypatch, solutions=False)
    with pytest.raises(ImportError, match="not found"):
        reframe.MediaPipeTasksDetector(tmp_path / "absent.tflite")


def test_tasks_detector_rejects_an_unreadable_model(monkeypatch, tmp_path):
    """A model baked into an image with the wrong mode must degrade, not crash."""
    _fake_mediapipe(monkeypatch, solutions=False)
    model = tmp_path / "face.tflite"
    model.write_bytes(b"not really a model")
    model.chmod(0o000)
    try:
        with pytest.raises(ImportError, match="not found"):
            reframe.MediaPipeTasksDetector(model)
    finally:
        model.chmod(0o644)


def test_solutions_detector_rejects_a_build_without_solutions(monkeypatch):
    _fake_mediapipe(monkeypatch, solutions=False)
    with pytest.raises(ImportError, match="no solutions API"):
        reframe.MediaPipeDetector()
