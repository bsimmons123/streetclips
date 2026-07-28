from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from streetclip.db import Database, JobKind, JobStatus
from streetclip.models import Candidate, Category


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "streetclip.db")


def _candidates(n: int = 3) -> list[Candidate]:
    return [
        Candidate(
            start=i * 100.0,
            end=i * 100.0 + 30.0,
            score=9.0 - i,
            category=Category.ONE_LINER,
            hook_title=f"Moment {i}",
            reason="r",
            excerpt="e",
        )
        for i in range(n)
    ]


def _candidate(start, end, score=8.0):
    return Candidate(
        start=start,
        end=end,
        score=score,
        category=Category.ONE_LINER,
        hook_title="t",
        reason="r",
        excerpt="e",
    )


# --- jobs --------------------------------------------------------------------


def test_create_and_read_a_job(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/in/session.mp4"), "session.mp4")
    job = db.get_job(job_id)

    assert job["status"] == JobStatus.QUEUED
    assert job["kind"] == JobKind.ANALYZE
    assert job["source_name"] == "session.mp4"
    assert job["progress"] == 0.0


def test_get_missing_job_returns_none(db: Database):
    assert db.get_job(999) is None


def test_jobs_list_newest_first(db: Database):
    first = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    second = db.create_job(JobKind.ANALYZE, Path("/b.mp4"), "b.mp4")
    assert [j["id"] for j in db.list_jobs()] == [second, first]


def test_claim_takes_the_oldest_queued_job(db: Database):
    first = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.create_job(JobKind.ANALYZE, Path("/b.mp4"), "b.mp4")

    claimed = db.claim_next_job()
    assert claimed["id"] == first
    assert claimed["status"] == JobStatus.RUNNING


def test_a_claimed_job_is_not_claimed_again(db: Database):
    """The claim is the lock — a job must never run twice."""
    db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    first = db.claim_next_job()
    second = db.claim_next_job()

    assert first is not None
    assert second is None


def test_claim_on_an_empty_queue(db: Database):
    assert db.claim_next_job() is None


def test_progress_updates(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.update_progress(job_id, "transcribing", 0.4)

    job = db.get_job(job_id)
    assert job["stage"] == "transcribing"
    assert job["progress"] == 0.4


def test_progress_is_clamped(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.update_progress(job_id, "x", 5.0)
    assert db.get_job(job_id)["progress"] == 1.0
    db.update_progress(job_id, "x", -2.0)
    assert db.get_job(job_id)["progress"] == 0.0


def test_finishing_a_job_stores_the_report(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.finish_job(job_id, json.dumps({"candidates": []}))

    job = db.get_job(job_id)
    assert job["status"] == JobStatus.DONE
    assert job["progress"] == 1.0
    assert db.report_for(job_id) == {"candidates": []}


def test_finishing_without_a_report_keeps_the_existing_one(db: Database):
    """A render job completing must not wipe the analysis it rendered from."""
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.finish_job(job_id, json.dumps({"kept": True}))
    db.finish_job(job_id, None)
    assert db.report_for(job_id) == {"kept": True}


def test_failing_a_job_records_the_error(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.fail_job(job_id, "ffmpeg exploded")

    job = db.get_job(job_id)
    assert job["status"] == JobStatus.FAILED
    assert "ffmpeg exploded" in job["error"]


def test_enormous_errors_are_truncated(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.fail_job(job_id, "x" * 10_000)
    assert len(db.get_job(job_id)["error"]) <= 2000


def test_report_for_a_job_without_one(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    assert db.report_for(job_id) is None


def test_requeue_recovers_jobs_abandoned_by_a_crash(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.claim_next_job()

    assert db.requeue_running_jobs() == 1
    assert db.get_job(1)["status"] == JobStatus.QUEUED
    # And it can now be picked up again.
    assert db.claim_next_job() is not None


def test_requeue_leaves_finished_jobs_alone(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.finish_job(job_id)
    assert db.requeue_running_jobs() == 0
    assert db.get_job(job_id)["status"] == JobStatus.DONE


# --- clips -------------------------------------------------------------------


def test_clips_are_stored_in_rank_order(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, _candidates(3))

    clips = db.list_clips(job_id)
    assert [c["position"] for c in clips] == [0, 1, 2]
    assert clips[0]["hook_title"] == "Moment 0"
    assert clips[0]["selected"] == 0


def test_replacing_clips_discards_the_previous_run(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, _candidates(5))
    db.replace_clips(job_id, _candidates(2))
    assert len(db.list_clips(job_id)) == 2


def test_editing_a_clips_bounds(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, _candidates(1))
    clip_id = db.list_clips(job_id)[0]["id"]

    updated = db.update_clip(clip_id, start=12.5, end=48.0)
    assert updated["start"] == 12.5
    assert updated["end"] == 48.0


def test_selecting_a_clip(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, _candidates(2))
    clips = db.list_clips(job_id)

    db.update_clip(clips[0]["id"], selected=True)
    selected = db.selected_clips(job_id)
    assert len(selected) == 1
    assert selected[0]["id"] == clips[0]["id"]


def test_deselecting_a_clip(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, _candidates(1))
    clip_id = db.list_clips(job_id)[0]["id"]

    db.update_clip(clip_id, selected=True)
    db.update_clip(clip_id, selected=False)
    assert db.selected_clips(job_id) == []


def test_partial_update_leaves_other_fields_alone(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, _candidates(1))
    clip_id = db.list_clips(job_id)[0]["id"]

    db.update_clip(clip_id, selected=True)
    updated = db.update_clip(clip_id, start=5.0)
    assert updated["selected"] == 1
    assert updated["start"] == 5.0


def test_update_with_nothing_to_change_is_a_read(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, _candidates(1))
    clip_id = db.list_clips(job_id)[0]["id"]
    assert db.update_clip(clip_id)["id"] == clip_id


def test_update_of_a_missing_clip(db: Database):
    assert db.update_clip(999, start=1.0) is None


def test_recording_a_rendered_path(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, _candidates(1))
    clip_id = db.list_clips(job_id)[0]["id"]

    db.set_rendered_path(clip_id, Path("/out/01.mp4"))
    assert db.get_clip(clip_id)["rendered_path"] == "/out/01.mp4"


def test_deleting_a_job_takes_its_clips(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")
    db.replace_clips(job_id, _candidates(2))

    with db.connect() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    assert db.list_clips(job_id) == []


def test_schema_migration_is_idempotent(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    first = Database(path)
    job_id = first.create_job(JobKind.ANALYZE, Path("/a.mp4"), "a.mp4")

    # Reopening must not wipe or fail on the existing schema.
    second = Database(path)
    assert second.get_job(job_id) is not None


def test_new_columns_default_sensibly(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    job = db.get_job(job_id)
    assert job["title"] is None
    assert job["duration"] == 0.0
    assert job["poster_path"] is None


def test_rename_sets_title(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    row = db.rename_job(job_id, "Corner session")
    assert row["title"] == "Corner session"
    assert db.get_job(job_id)["title"] == "Corner session"


def test_set_media_records_duration_and_poster(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    db.set_media(job_id, 3919.3, "/data/job_00001/poster.jpg")
    job = db.get_job(job_id)
    assert job["duration"] == 3919.3
    assert job["poster_path"].endswith("poster.jpg")


def test_migrate_is_idempotent_on_an_existing_database(tmp_path):
    """Columns are added by ALTER, which errors if applied twice."""
    path = tmp_path / "s.db"
    Database(path).migrate()
    Database(path).migrate()  # must not raise
    db = Database(path)
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    assert db.get_job(job_id)["title"] is None


def _create_pre_migration_jobs_table(path: Path) -> int:
    """Build a `jobs` table as it existed before title/duration/poster_path,
    with one row already in it, and return that row's id.

    This is the real shape of an operator's existing data/streetclip.db.
    """
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE jobs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            kind          TEXT NOT NULL,
            status        TEXT NOT NULL,
            source_path   TEXT NOT NULL,
            source_name   TEXT NOT NULL,
            stage         TEXT NOT NULL DEFAULT '',
            progress      REAL NOT NULL DEFAULT 0.0,
            error         TEXT,
            report_json   TEXT,
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL
        )
        """
    )
    now = time.time()
    cursor = conn.execute(
        "INSERT INTO jobs (kind, status, source_path, source_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (JobKind.ANALYZE.value, JobStatus.QUEUED.value, "/a.mp4", "a.mp4", now, now),
    )
    job_id = int(cursor.lastrowid)
    conn.commit()
    conn.close()
    return job_id


def test_migrate_adds_columns_to_a_genuinely_pre_migration_database(tmp_path: Path):
    """The operator's real data/streetclip.db predates title/duration/poster_path
    and already has rows in it. migrate() must ALTER it in place, twice without
    raising, and the pre-existing row must survive with sensible defaults.
    """
    path = tmp_path / "existing.db"
    job_id = _create_pre_migration_jobs_table(path)

    db = Database(path)  # __init__ calls migrate()
    db.migrate()  # calling it again must not raise

    with db.connect() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert {"title", "duration", "poster_path"} <= columns

    job = db.get_job(job_id)
    assert job["title"] is None
    assert job["duration"] == 0.0
    assert job["poster_path"] is None


# --- list_workspaces ----------------------------------------------------------


def test_list_workspaces_counts_clips_by_state(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=1)
    db.replace_clips(job_id, [_candidate(0, 30), _candidate(60, 90), _candidate(120, 150)])
    clips = db.list_clips(job_id)
    db.update_clip(clips[0]["id"], None, None, True)
    db.update_clip(clips[1]["id"], None, None, True)
    db.set_rendered_path(clips[0]["id"], Path("/out/a.mp4"))

    rows = db.list_workspaces(user_id=1)
    assert len(rows) == 1
    assert rows[0]["clip_count"] == 3
    assert rows[0]["kept_count"] == 2
    assert rows[0]["rendered_count"] == 1


def test_list_workspaces_excludes_render_jobs(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=1)
    db.create_job(JobKind.RENDER, Path("1"), "a.mp4", user_id=1)
    assert [r["kind"] for r in db.list_workspaces(user_id=1)] == ["analyze"]


def test_list_workspaces_counts_zero_for_a_fresh_job(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=1)
    assert db.list_workspaces(user_id=1)[0]["clip_count"] == 0


def test_backfill_fills_durations_from_stored_reports(tmp_path):
    """A workspace analyzed before the column existed must not read 0:00."""
    import json as _json

    path = tmp_path / "s.db"
    db = Database(path)
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    report = {
        "media": {"path": "/x/a.mp4", "duration": 3919.34, "width": 1280,
                  "height": 720, "fps": 25.0},
        "transcript": {"duration": 3919.34, "words": [], "segments": []},
        "candidates": [],
    }
    db.finish_job(job_id, _json.dumps(report))
    # Simulate the pre-migration state: report present, duration never set.
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET duration = 0 WHERE id = ?", (job_id,))

    assert db.backfill_durations() == 1
    assert db.get_job(job_id)["duration"] == 3919.34
    # Idempotent: nothing left to fill.
    assert db.backfill_durations() == 0


def test_backfill_skips_jobs_without_a_report(tmp_path):
    db = Database(tmp_path / "s.db")
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    assert db.backfill_durations() == 0


def test_backfill_survives_a_malformed_report(tmp_path):
    """One corrupt row must not stop the others being filled."""
    db = Database(tmp_path / "s.db")
    bad = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    db.finish_job(bad, "{not json at all")

    assert db.backfill_durations() == 0
    assert db.get_job(bad)["duration"] == 0.0


# --- ownership -----------------------------------------------------------------


def test_jobs_carry_an_owner(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=7)
    assert db.get_job(job_id)["user_id"] == 7


def test_list_workspaces_only_returns_your_own(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=1)
    db.create_job(JobKind.ANALYZE, Path("/x/b.mp4"), "b.mp4", user_id=2)

    mine = db.list_workspaces(user_id=1)
    assert [w["source_name"] for w in mine] == ["a.mp4"]


def test_list_workspaces_for_a_user_with_none(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=1)
    assert db.list_workspaces(user_id=99) == []


def test_backfill_assigns_ownerless_jobs(db: Database):
    """Workspaces created before accounts existed become the admin's."""
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    db.create_job(JobKind.ANALYZE, Path("/x/b.mp4"), "b.mp4")

    assert db.backfill_owner(user_id=1) == 2
    assert db.list_workspaces(user_id=1)[0]["user_id"] == 1
    # Idempotent: nothing left without an owner.
    assert db.backfill_owner(user_id=1) == 0


def test_backfill_leaves_owned_jobs_alone(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=5)
    db.backfill_owner(user_id=1)
    assert db.list_workspaces(user_id=5)[0]["user_id"] == 5
