from __future__ import annotations

import time
from pathlib import Path

import pytest

from streetclip.config import Settings
from streetclip.db import Database, JobKind, JobStatus
from streetclip.models import Candidate, Category, MediaInfo, Report, Transcript, Word
from streetclip.provider_keys import ProviderKeyVault
from streetclip.worker import Worker, clip_to_candidate, enqueue_render

from .conftest import needs_ffmpeg


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "db.sqlite")


def _settings(**overrides) -> Settings:
    base = {
        "output_width": 1080,
        "output_height": 1920,
        "min_clip_seconds": 2.0,
        "max_clip_seconds": 60.0,
        "boundary_padding_seconds": 0.1,
        # These tests are about selection and bounds, not the caption burn,
        # which needs an ffmpeg built with libass.
        "render_captions": False,
        "render_track_speaker": False,
    }
    base.update(overrides)
    return Settings(**base)


def test_non_admin_jobs_use_personal_provider_keys(tmp_path: Path):
    from streetclip.accounts import Accounts

    path = tmp_path / "db.sqlite"
    accounts = Accounts(path)
    db = Database(path)
    user_id = accounts.create_user("member@x.com", "hash", approved=True)
    vault = ProviderKeyVault(accounts, "encryption-secret")
    vault.set(user_id, "personal-groq", "personal-anthropic")
    worker = Worker(
        db,
        tmp_path,
        settings=Settings(groq_api_key="admin-groq", anthropic_api_key="admin-anthropic"),
        accounts=accounts,
        key_vault=vault,
    )
    job_id = db.create_job(
        JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id
    )

    settings = worker.settings_for(db.get_job(job_id))

    assert settings.groq_api_key == "personal-groq"
    assert settings.anthropic_api_key == "personal-anthropic"


def _transcript() -> Transcript:
    return Transcript(
        duration=10.0,
        words=[Word(text=f"w{i}", start=i * 0.5, end=i * 0.5 + 0.5) for i in range(20)],
    )


def _candidate(start=1.0, end=5.0, title="Who told you that") -> Candidate:
    return Candidate(
        start=start, end=end, score=9.0, category=Category.CONFRONTATION,
        hook_title=title, reason="r", excerpt="e",
    )


def _seed_analysis(db: Database, source: Path, candidates=None) -> int:
    """Put a finished analyze job in the database, as the worker would."""
    from streetclip.pipeline import ingest

    info = ingest.probe(source)
    job_id = db.create_job(JobKind.ANALYZE, source, source.name)
    report = Report(
        media=MediaInfo(
            path=str(source), duration=info.duration, width=info.width,
            height=info.height, fps=info.fps,
        ),
        transcript=_transcript(),
        candidates=candidates if candidates is not None else [_candidate()],
    )
    db.replace_clips(job_id, report.candidates)
    db.finish_job(job_id, report.model_dump_json())
    return job_id


# --- row round-tripping ------------------------------------------------------


def test_clip_row_becomes_a_candidate(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, [_candidate()])
    candidate = clip_to_candidate(db.list_clips(job_id)[0])

    assert candidate.hook_title == "Who told you that"
    assert candidate.category is Category.CONFRONTATION


def test_operator_edits_survive_the_round_trip(db: Database):
    """The whole point of review is that edited bounds are what get rendered."""
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, [_candidate(start=1.0, end=5.0)])
    clip_id = db.list_clips(job_id)[0]["id"]

    db.update_clip(clip_id, start=2.25, end=4.5)
    candidate = clip_to_candidate(db.get_clip(clip_id))
    assert (candidate.start, candidate.end) == (2.25, 4.5)


# --- failure handling --------------------------------------------------------


def test_a_failing_job_is_recorded_not_raised(db: Database, tmp_path: Path):
    """One bad job must not take down the worker thread."""
    db.create_job(JobKind.ANALYZE, tmp_path / "missing.mp4", "missing.mp4")
    worker = Worker(db, tmp_path / "data", settings=_settings())

    assert worker.run_once() is True
    job = db.get_job(1)
    assert job["status"] == JobStatus.FAILED
    assert "FileNotFoundError" in job["error"]


def test_unknown_job_kinds_fail_cleanly(db: Database, tmp_path: Path):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (kind, status, source_path, source_name, created_at, updated_at)"
            " VALUES ('nonsense', 'queued', '/x', 'x', 0, 0)"
        )
    worker = Worker(db, tmp_path / "data", settings=_settings())
    worker.run_once()
    assert "unknown job kind" in db.get_job(1)["error"]


def test_run_once_on_an_empty_queue(db: Database, tmp_path: Path):
    assert Worker(db, tmp_path / "data").run_once() is False


def test_worker_keeps_going_after_a_failure(db: Database, tmp_path: Path, sample_video: Path):
    db.create_job(JobKind.ANALYZE, tmp_path / "missing.mp4", "missing.mp4")
    second = db.create_job(JobKind.ANALYZE, sample_video, "ok.mp4")
    worker = Worker(db, tmp_path / "data", settings=_settings())

    worker.run_once()
    worker.run_once()
    # The second job was claimed rather than blocked by the first one's failure.
    assert db.get_job(second)["status"] != JobStatus.QUEUED


# --- render jobs -------------------------------------------------------------


def test_enqueue_render_requires_a_finished_analysis(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    with pytest.raises(ValueError, match="has not finished"):
        enqueue_render(db, job_id)


def test_render_job_inherits_analysis_owner(db: Database):
    analyze_id = db.create_job(
        JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=17
    )
    db.finish_job(analyze_id, Report(
        media=MediaInfo(path="/x/a.mp4", duration=10, width=640, height=360, fps=30),
        transcript=Transcript(duration=10),
        candidates=[],
    ).model_dump_json())

    render_id = enqueue_render(db, analyze_id)

    assert db.get_job(render_id)["user_id"] == 17


def test_enqueue_render_requires_a_real_job(db: Database):
    with pytest.raises(ValueError, match="no such job"):
        enqueue_render(db, 999)


@needs_ffmpeg
def test_render_job_requires_a_selection(db: Database, tmp_path: Path, sample_video: Path):
    analyze_id = _seed_analysis(db, sample_video)
    enqueue_render(db, analyze_id)

    worker = Worker(db, tmp_path / "data", settings=_settings())
    worker.run_once()
    assert "no clips were selected" in db.get_job(2)["error"]


@needs_ffmpeg
def test_render_job_writes_only_the_selected_clips(
    db: Database, tmp_path: Path, sample_video: Path
):
    analyze_id = _seed_analysis(
        db, sample_video, [_candidate(1.0, 5.0, "First"), _candidate(5.0, 9.0, "Second")]
    )
    clips = db.list_clips(analyze_id)
    db.update_clip(clips[1]["id"], selected=True)

    enqueue_render(db, analyze_id)
    worker = Worker(db, tmp_path / "data", settings=_settings())
    worker.run_once()

    assert db.get_job(2)["status"] == JobStatus.DONE
    assert db.get_clip(clips[0]["id"])["rendered_path"] is None
    rendered = db.get_clip(clips[1]["id"])["rendered_path"]
    assert rendered is not None and Path(rendered).exists()


@needs_ffmpeg
def test_render_uses_edited_bounds_not_the_stored_report(
    db: Database, tmp_path: Path, sample_video: Path
):
    from streetclip.pipeline import ingest

    analyze_id = _seed_analysis(db, sample_video, [_candidate(1.0, 9.0)])
    clip_id = db.list_clips(analyze_id)[0]["id"]
    # Operator trims it down before exporting.
    db.update_clip(clip_id, start=2.0, end=5.0, selected=True)

    enqueue_render(db, analyze_id)
    worker = Worker(db, tmp_path / "data", settings=_settings())
    worker.run_once()

    rendered = Path(db.get_clip(clip_id)["rendered_path"])
    assert 2.7 <= ingest.probe(rendered).duration <= 3.3


@needs_ffmpeg
def test_render_job_does_not_clobber_the_analysis_report(
    db: Database, tmp_path: Path, sample_video: Path
):
    analyze_id = _seed_analysis(db, sample_video)
    db.update_clip(db.list_clips(analyze_id)[0]["id"], selected=True)
    enqueue_render(db, analyze_id)

    Worker(db, tmp_path / "data", settings=_settings()).run_once()
    assert db.report_for(analyze_id) is not None


# --- analyze jobs ------------------------------------------------------------


class _StubScorer:
    def propose(self, chunk):
        from streetclip.models import Proposal

        return [
            Proposal(
                start=1.0, end=8.0, score=8.0, category=Category.ONE_LINER,
                hook_title="Stub moment", reason="r",
            )
        ]


@needs_ffmpeg
def test_analyze_job_stores_clips_and_report(
    db: Database, tmp_path: Path, sample_video: Path, monkeypatch
):
    from streetclip.pipeline.transcribe.base import FakeTranscriber

    monkeypatch.setattr(
        "streetclip.pipeline.run.get_transcriber", lambda s: FakeTranscriber(_transcript())
    )
    monkeypatch.setattr("streetclip.pipeline.score.Scorer", lambda *a, **k: _StubScorer())

    db.create_job(JobKind.ANALYZE, sample_video, sample_video.name)
    worker = Worker(db, tmp_path / "data", settings=_settings())
    worker.run_once()

    job = db.get_job(1)
    assert job["status"] == JobStatus.DONE
    assert job["progress"] == 1.0
    clips = db.list_clips(1)
    assert len(clips) == 1
    assert clips[0]["hook_title"] == "Stub moment"


@needs_ffmpeg
def test_analyze_job_reports_progress_as_it_goes(
    db: Database, tmp_path: Path, sample_video: Path, monkeypatch
):
    from streetclip.pipeline.transcribe.base import FakeTranscriber

    seen: list[float] = []
    monkeypatch.setattr(
        "streetclip.pipeline.run.get_transcriber", lambda s: FakeTranscriber(_transcript())
    )
    monkeypatch.setattr("streetclip.pipeline.score.Scorer", lambda *a, **k: _StubScorer())

    db.create_job(JobKind.ANALYZE, sample_video, sample_video.name)
    worker = Worker(db, tmp_path / "data", settings=_settings())
    original = db.update_progress
    db.update_progress = lambda jid, stage, frac: (seen.append(frac), original(jid, stage, frac))[1]
    worker.run_once()

    assert seen == sorted(seen)
    assert seen[-1] == 1.0


@needs_ffmpeg
def test_analyze_purges_intermediate_audio(
    db: Database, tmp_path: Path, sample_video: Path, monkeypatch
):
    from streetclip.pipeline.transcribe.base import FakeTranscriber

    monkeypatch.setattr(
        "streetclip.pipeline.run.get_transcriber", lambda s: FakeTranscriber(_transcript())
    )
    monkeypatch.setattr("streetclip.pipeline.score.Scorer", lambda *a, **k: _StubScorer())

    job_id = db.create_job(JobKind.ANALYZE, sample_video, sample_video.name)
    data_dir = tmp_path / "data"
    Worker(db, data_dir, settings=_settings()).run_once()

    assert db.get_job(job_id)["status"] == JobStatus.DONE
    assert not (data_dir / "job_00001" / "work" / "audio.wav").exists()


@needs_ffmpeg
def test_analyze_records_duration_and_poster(
    db: Database, tmp_path: Path, sample_video: Path, monkeypatch
):
    from streetclip.pipeline.transcribe.base import FakeTranscriber

    monkeypatch.setattr(
        "streetclip.pipeline.run.get_transcriber", lambda s: FakeTranscriber(_transcript())
    )
    monkeypatch.setattr("streetclip.pipeline.score.Scorer", lambda *a, **k: _StubScorer())

    job_id = db.create_job(JobKind.ANALYZE, sample_video, sample_video.name)
    Worker(db, tmp_path / "data", settings=_settings()).run_once()

    job = db.get_job(job_id)
    assert job["duration"] > 0
    assert Path(job["poster_path"]).is_file()


@needs_ffmpeg
def test_render_still_works_after_the_purge(
    db: Database, tmp_path: Path, sample_video: Path, monkeypatch
):
    """Exports must survive cleanup — this is what makes the purge safe."""
    from streetclip.pipeline.transcribe.base import FakeTranscriber

    monkeypatch.setattr(
        "streetclip.pipeline.run.get_transcriber", lambda s: FakeTranscriber(_transcript())
    )
    monkeypatch.setattr("streetclip.pipeline.score.Scorer", lambda *a, **k: _StubScorer())

    worker = Worker(db, tmp_path / "data", settings=_settings())
    analyze_id = db.create_job(JobKind.ANALYZE, sample_video, sample_video.name)
    worker.run_once()

    clips = db.list_clips(analyze_id)
    assert clips, "the stub scorer must produce at least one clip"
    db.update_clip(clips[0]["id"], None, None, True)

    enqueue_render(db, analyze_id)
    worker.run_once()

    rendered = db.list_clips(analyze_id)[0]["rendered_path"]
    assert rendered and Path(rendered).is_file()


# --- threading ---------------------------------------------------------------


def test_start_requeues_jobs_left_running_by_a_crash(db: Database, tmp_path: Path):
    db.create_job(JobKind.ANALYZE, tmp_path / "missing.mp4", "x.mp4")
    db.claim_next_job()

    worker = Worker(db, tmp_path / "data", settings=_settings(), poll_seconds=0.01)
    worker.start()
    try:
        # Wait for a terminal status: leaving QUEUED only means the worker
        # claimed it, and on a loaded machine the run itself takes a moment.
        deadline = time.time() + 5.0
        terminal = (JobStatus.DONE, JobStatus.FAILED)
        while time.time() < deadline and db.get_job(1)["status"] not in terminal:
            time.sleep(0.02)
        # It was requeued and then picked up (and failed, since the file is missing).
        assert db.get_job(1)["status"] == JobStatus.FAILED
    finally:
        worker.stop(timeout=5.0)


def test_stopping_a_worker_that_never_started(db: Database, tmp_path: Path):
    Worker(db, tmp_path / "data").stop()


def test_starting_twice_is_harmless(db: Database, tmp_path: Path):
    worker = Worker(db, tmp_path / "data", poll_seconds=0.01)
    worker.start()
    worker.start()
    try:
        assert worker._thread is not None
    finally:
        worker.stop(timeout=5.0)
