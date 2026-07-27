from __future__ import annotations

from pathlib import Path

from streetclip.config import Settings
from streetclip.models import Candidate, Category, MediaInfo, Report, Transcript, Word
from streetclip.pipeline import ingest, run
from streetclip.pipeline.render import encode
from streetclip.pipeline.render.reframe import CropPlan, Detection

from .conftest import needs_ffmpeg, needs_libass


def _plan(expression="100.00") -> CropPlan:
    return CropPlan(crop_width=202, crop_height=360, x_expression=expression, tracked=True)


def _transcript() -> Transcript:
    return Transcript(
        duration=10.0,
        words=[Word(text=f"word{i}", start=i * 0.5, end=i * 0.5 + 0.5) for i in range(20)],
    )


def _candidate(start=1.0, end=6.0) -> Candidate:
    return Candidate(
        start=start,
        end=end,
        score=9.0,
        category=Category.ONE_LINER,
        hook_title="Repent and believe",
        reason="Lands clean.",
        excerpt="word2 word3",
    )


# --- filter construction -----------------------------------------------------


def test_filter_chains_crop_scale_and_subtitles(tmp_path: Path):
    settings = Settings(output_width=1080, output_height=1920)
    subs = tmp_path / "c.ass"
    chain = encode.build_filter(_plan(), subs, settings)

    stages = chain.split(",")
    assert stages[0].startswith("crop=")
    assert "scale=1080:1920" in chain
    assert chain.endswith("'")
    assert "subtitles=" in chain
    # Crop must come before scale, or we'd upscale then throw pixels away.
    assert chain.index("crop=") < chain.index("scale=")
    assert chain.index("scale=") < chain.index("subtitles=")


def test_filter_omits_subtitles_when_not_burning():
    chain = encode.build_filter(_plan(), None, Settings())
    assert "subtitles=" not in chain
    assert "crop=" in chain


def test_filter_embeds_the_crop_expression():
    chain = encode.build_filter(_plan("if(lt(t,5.000),(0.00+2.0*(t-0.000)),100.00)"), None)
    assert "if(lt(t,5.000)" in chain
    # The expression is quoted so ffmpeg doesn't split it on its own commas.
    assert "x='if(" in chain


def test_filter_pins_sample_aspect_ratio():
    # Without setsar, a non-square-pixel source renders stretched.
    assert "setsar=1" in encode.build_filter(_plan(), None)


# --- path escaping -----------------------------------------------------------


def test_escapes_colons_in_filter_paths():
    """A colon separates filter options; an unescaped one breaks the graph."""
    escaped = encode.escape_filter_path(Path("/tmp/odd:name/c.ass"))
    assert "\\:" in escaped
    assert ":" not in escaped.replace("\\:", "")


def test_escapes_backslashes_and_quotes():
    escaped = encode.escape_filter_path(Path("/tmp/it's/c.ass"))
    assert "\\'" in escaped


def test_ordinary_path_is_left_readable():
    assert encode.escape_filter_path(Path("/tmp/out/c.ass")) == "/tmp/out/c.ass"


# --- rendering ---------------------------------------------------------------


@needs_ffmpeg
def test_render_clip_produces_vertical_video(sample_video: Path, tmp_path: Path):
    settings = Settings(output_width=1080, output_height=1920)
    dest = encode.render_clip(
        sample_video, tmp_path / "short.mp4", start=1.0, end=5.0,
        plan=_plan(), settings=settings,
    )

    info = ingest.probe(dest)
    assert (info.width, info.height) == (1080, 1920)
    assert 3.5 <= info.duration <= 4.5


@needs_ffmpeg
def test_render_clip_accepts_a_moving_crop_expression(sample_video: Path, tmp_path: Path):
    """A time-varying x must survive ffmpeg's expression parser."""
    plan = _plan("if(lt(t,2.000),(0.00+50.0000*(t-0.000)),100.00)")
    dest = encode.render_clip(
        sample_video, tmp_path / "pan.mp4", start=0.0, end=4.0, plan=plan,
        settings=Settings(output_width=1080, output_height=1920),
    )
    assert ingest.probe(dest).width == 1080


@needs_ffmpeg
@needs_libass
def test_render_candidate_burns_captions(sample_video: Path, tmp_path: Path):
    settings = Settings(output_width=1080, output_height=1920)
    dest = encode.render_candidate(
        sample_video, tmp_path / "out" / "short.mp4", _candidate(), _transcript(),
        _plan(), work_dir=tmp_path / "work", settings=settings,
    )

    assert dest.exists()
    assert ingest.probe(dest).height == 1920
    # The subtitle file is kept in the work dir for debugging a bad burn.
    subs = tmp_path / "work" / "short.ass"
    assert subs.exists()
    assert "Dialogue:" in subs.read_text()


@needs_ffmpeg
def test_render_candidate_without_captions_writes_no_subtitle_file(
    sample_video: Path, tmp_path: Path
):
    encode.render_candidate(
        sample_video, tmp_path / "out" / "plain.mp4", _candidate(), _transcript(),
        _plan(), work_dir=tmp_path / "work", burn_captions=False,
        settings=Settings(output_width=1080, output_height=1920),
    )
    assert not (tmp_path / "work" / "plain.ass").exists()


@needs_ffmpeg
@needs_libass
def test_captions_are_rebased_to_the_clip(sample_video: Path, tmp_path: Path):
    """Captions for a clip starting at 4s must begin at 0s in the rendered file."""
    encode.render_candidate(
        sample_video, tmp_path / "out" / "s.mp4", _candidate(start=4.0, end=8.0),
        _transcript(), _plan(), work_dir=tmp_path / "work",
        settings=Settings(output_width=1080, output_height=1920),
    )
    text = (tmp_path / "work" / "s.ass").read_text()
    first = next(ln for ln in text.splitlines() if ln.startswith("Dialogue:"))
    assert first.split(",")[1] == "0:00:00.00"


# --- pipeline wiring ---------------------------------------------------------


class _StubDetector:
    def detect(self, image):
        return [Detection(x=100.0, y=50.0, width=80.0, height=90.0, confidence=0.9)]


def _report(source: Path) -> Report:
    info = ingest.probe(source)
    return Report(
        media=MediaInfo(
            path=str(source), duration=info.duration, width=info.width,
            height=info.height, fps=info.fps,
        ),
        transcript=_transcript(),
        candidates=[_candidate(1.0, 5.0), _candidate(5.0, 9.0)],
    )


@needs_ffmpeg
def test_render_shorts_writes_one_file_per_candidate(sample_video: Path, tmp_path: Path):
    settings = Settings(output_width=1080, output_height=1920)
    written = run.render_shorts(
        _report(sample_video), out_dir=tmp_path / "shorts", work_dir=tmp_path / "work",
        track_speaker=False, burn_captions=False, settings=settings,
    )

    assert len(written) == 2
    assert all(p.exists() for p in written)
    assert all(ingest.probe(p).height == 1920 for p in written)
    # Filenames carry rank, category, and hook so the operator can find them.
    assert written[0].name.startswith("01_one_liner_repent-and-believe")


@needs_ffmpeg
def test_render_shorts_respects_the_limit(sample_video: Path, tmp_path: Path):
    written = run.render_shorts(
        _report(sample_video), out_dir=tmp_path / "s", work_dir=tmp_path / "w",
        limit=1, track_speaker=False, burn_captions=False,
        settings=Settings(output_width=1080, output_height=1920),
    )
    assert len(written) == 1


@needs_ffmpeg
def test_render_shorts_reports_progress_to_completion(sample_video: Path, tmp_path: Path):
    seen: list[float] = []
    run.render_shorts(
        _report(sample_video), out_dir=tmp_path / "s", work_dir=tmp_path / "w",
        track_speaker=False, burn_captions=False,
        settings=Settings(output_width=1080, output_height=1920),
        progress=lambda stage, frac: seen.append(frac),
    )
    assert seen == sorted(seen)
    assert seen[-1] == 1.0


@needs_ffmpeg
def test_render_shorts_falls_back_when_tracking_is_unavailable(
    sample_video: Path, tmp_path: Path, monkeypatch
):
    """A missing mediapipe should degrade to a center crop, not fail the run."""

    def _boom(*args, **kwargs):
        raise ImportError("no mediapipe")

    monkeypatch.setattr(
        "streetclip.pipeline.render.reframe.MediaPipeDetector.__init__", _boom
    )
    written = run.render_shorts(
        _report(sample_video), out_dir=tmp_path / "s", work_dir=tmp_path / "w",
        limit=1, track_speaker=True, burn_captions=False,
        settings=Settings(output_width=1080, output_height=1920),
    )
    assert len(written) == 1
    assert ingest.probe(written[0]).height == 1920


@needs_ffmpeg
def test_render_shorts_with_tracking_enabled(sample_video: Path, tmp_path: Path):
    settings = Settings(output_width=1080, output_height=1920, reframe_sample_fps=2.0)
    written = run.render_shorts(
        _report(sample_video), out_dir=tmp_path / "s", work_dir=tmp_path / "w",
        limit=1, track_speaker=True, burn_captions=False, settings=settings,
    )
    assert len(written) == 1
    assert ingest.probe(written[0]).height == 1920
