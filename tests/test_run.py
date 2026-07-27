from __future__ import annotations

from pathlib import Path

import pytest

from streetclip.cli import main
from streetclip.config import Settings
from streetclip.models import Category, MediaInfo, Report, Transcript, Word
from streetclip.pipeline import run
from streetclip.pipeline.transcribe.base import FakeTranscriber

from .conftest import needs_ffmpeg


class _StubScorer:
    """Proposes one moment spanning most of the fixture clip."""

    def __init__(self, moments=None):
        self.calls = 0
        self.moments = moments

    def propose(self, chunk):
        self.calls += 1
        from streetclip.models import Proposal

        if self.moments is not None:
            return self.moments
        return [
            Proposal(
                start=1.0,
                end=9.0,
                score=8.5,
                category=Category.CONFRONTATION,
                hook_title="Who told you that",
                reason="A clean exchange.",
            )
        ]


def _transcript() -> Transcript:
    words = [Word(text=f"w{i}", start=float(i) * 0.5, end=float(i) * 0.5 + 0.5) for i in range(20)]
    return Transcript(duration=10.0, words=words)


def _settings() -> Settings:
    # The fixture clip is 10s, so allow short clips through the length filter.
    return Settings(min_clip_seconds=2.0, max_clip_seconds=60.0, boundary_padding_seconds=0.1)


@needs_ffmpeg
def test_analyze_runs_end_to_end(sample_video: Path, tmp_path: Path):
    report = run.analyze(
        sample_video,
        work_dir=tmp_path / "work",
        settings=_settings(),
        transcriber=FakeTranscriber(_transcript()),
        scorer=_StubScorer(),
    )

    assert report.media.width == 640
    assert len(report.candidates) == 1
    assert report.candidates[0].hook_title == "Who told you that"
    assert report.candidates[0].excerpt.startswith("w")


@needs_ffmpeg
def test_analyze_reports_progress_monotonically(sample_video: Path, tmp_path: Path):
    seen: list[tuple[str, float]] = []
    run.analyze(
        sample_video,
        work_dir=tmp_path / "work",
        settings=_settings(),
        transcriber=FakeTranscriber(_transcript()),
        scorer=_StubScorer(),
        progress=lambda stage, frac: seen.append((stage, frac)),
    )

    fractions = [f for _, f in seen]
    assert fractions == sorted(fractions), "progress went backwards"
    assert fractions[-1] == 1.0
    assert seen[-1][0] == "done"


@needs_ffmpeg
def test_analyze_with_no_good_moments_yields_no_candidates(sample_video: Path, tmp_path: Path):
    """An hour of nothing postable is a valid outcome, not an error."""
    report = run.analyze(
        sample_video,
        work_dir=tmp_path / "work",
        settings=_settings(),
        transcriber=FakeTranscriber(_transcript()),
        scorer=_StubScorer(moments=[]),
    )
    assert report.candidates == []


@needs_ffmpeg
def test_cut_candidates_writes_playable_files(sample_video: Path, tmp_path: Path):
    from streetclip.pipeline import ingest

    report = run.analyze(
        sample_video,
        work_dir=tmp_path / "work",
        settings=_settings(),
        transcriber=FakeTranscriber(_transcript()),
        scorer=_StubScorer(),
    )
    written = run.cut_candidates(report, tmp_path / "clips", settings=_settings())

    assert len(written) == 1
    assert written[0].exists()
    # Probing succeeds only if the file is a real, readable video.
    assert ingest.probe(written[0]).duration > 0


def test_report_round_trips_through_json(tmp_path: Path):
    report = Report(
        media=MediaInfo(path="/x.mp4", duration=10.0, width=640, height=360, fps=25.0),
        transcript=_transcript(),
        candidates=[],
    )
    path = run.write_report(report, tmp_path / "report.json")
    assert run.load_report(path).media.width == 640


def test_slug_is_filesystem_safe():
    assert run._slug("Who told you THAT?! / nonsense") == "who-told-you-that-nonsense"
    assert run._slug("!!!") == "clip"


def test_timestamp_formats_hours():
    assert run._timestamp(0) == "0:00:00"
    assert run._timestamp(3725) == "1:02:05"


# --- CLI ---------------------------------------------------------------------


def test_cli_requires_a_subcommand(capsys: pytest.CaptureFixture):
    with pytest.raises(SystemExit):
        main([])


def test_cli_reports_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture):
    code = main(["analyze", str(tmp_path / "nope.mp4"), "-o", str(tmp_path / "out")])
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_cli_cut_from_report(tmp_path: Path, capsys: pytest.CaptureFixture):
    report = Report(
        media=MediaInfo(path="/x.mp4", duration=10.0, width=640, height=360, fps=25.0),
        transcript=_transcript(),
        candidates=[],
    )
    path = run.write_report(report, tmp_path / "report.json")

    assert main(["cut", str(path), "-o", str(tmp_path / "clips")]) == 0
    assert "0 clips written" in capsys.readouterr().out
