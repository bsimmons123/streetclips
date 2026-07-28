"""SQLite-backed job and clip store.

Deliberately plain: a single file, one connection per operation, no ORM. The
workload is one operator reviewing one recording at a time, so the interesting
concurrency is exactly one worker thread against a handful of readers.

Candidates are exploded into their own table rather than left inside the report
JSON because the operator edits them — adjusting bounds and choosing keepers is
the whole point of the review step, and that wants rows, not a blob.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    source_name   TEXT NOT NULL,
    stage         TEXT NOT NULL DEFAULT '',
    progress      REAL NOT NULL DEFAULT 0.0,
    error         TEXT,
    report_json   TEXT,
    title         TEXT,
    duration      REAL NOT NULL DEFAULT 0.0,
    poster_path   TEXT,
    -- Not a real FK: `users` lives in Accounts' own database file, a
    -- separate connection this table can't reference. See db.py's migrate().
    user_id       INTEGER,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    start         REAL NOT NULL,
    end           REAL NOT NULL,
    score         REAL NOT NULL,
    category      TEXT NOT NULL,
    hook_title    TEXT NOT NULL,
    reason        TEXT NOT NULL,
    excerpt       TEXT NOT NULL,
    selected      INTEGER NOT NULL DEFAULT 0,
    rendered_path TEXT
);

CREATE INDEX IF NOT EXISTS clips_job ON clips(job_id, position);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, id);
"""


class JobKind(StrEnum):
    ANALYZE = "analyze"
    RENDER = "render"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        # A single shared connection keeps an in-memory database alive between
        # calls, which is what the tests use.
        self._shared = sqlite3.connect(":memory:") if str(path) == ":memory:" else None
        if self._shared is not None:
            self._shared.row_factory = sqlite3.Row
        self.migrate()
        self.backfill_durations()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self._shared is not None:
            yield self._shared
            self._shared.commit()
            return

        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets the API read while the worker writes instead of blocking.
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # Columns added after the first release. CREATE TABLE IF NOT EXISTS will not
    # add them to a database that already exists, so they are applied by ALTER.
    ADDED_COLUMNS = (
        ("title", "TEXT"),
        ("duration", "REAL NOT NULL DEFAULT 0.0"),
        ("poster_path", "TEXT"),
        ("user_id", "INTEGER"),
    )

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
            for name, decl in self.ADDED_COLUMNS:
                if name not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")

    # --- jobs ----------------------------------------------------------------

    def create_job(
        self,
        kind: JobKind,
        source_path: Path,
        source_name: str,
        user_id: int | None = None,
    ) -> int:
        now = time.time()
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO jobs (kind, status, source_path, source_name, user_id,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    kind.value,
                    JobStatus.QUEUED.value,
                    str(source_path),
                    source_name,
                    user_id,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_workspaces(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        """One user's analyze jobs with their clip tallies, newest first.

        A LEFT JOIN aggregate rather than stored counters: the numbers change
        on every keep and skip, and at this scale counting is free.
        """
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT j.*,"
                " COUNT(c.id) AS clip_count,"
                " COALESCE(SUM(c.selected), 0) AS kept_count,"
                " COUNT(c.rendered_path) AS rendered_count"
                " FROM jobs j LEFT JOIN clips c ON c.job_id = j.id"
                " WHERE j.kind = ? AND j.user_id = ?"
                " GROUP BY j.id ORDER BY j.id DESC LIMIT ?",
                (JobKind.ANALYZE.value, user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def backfill_owner(self, user_id: int) -> int:
        """Give ownerless jobs to a user. Returns how many were claimed."""
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET user_id = ? WHERE user_id IS NULL", (user_id,)
            )
            return cursor.rowcount

    def claim_next_job(self) -> dict[str, Any] | None:
        """Atomically take the oldest queued job.

        The UPDATE ... WHERE status='queued' is the lock: if two workers race,
        only one sees a non-zero rowcount, so a job can never run twice.
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id LIMIT 1",
                (JobStatus.QUEUED.value,),
            ).fetchone()
            if row is None:
                return None

            cursor = conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (JobStatus.RUNNING.value, time.time(), row["id"], JobStatus.QUEUED.value),
            )
            if cursor.rowcount == 0:
                return None

            claimed = dict(row)
            claimed["status"] = JobStatus.RUNNING.value
            return claimed

    def update_progress(self, job_id: int, stage: str, progress: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET stage = ?, progress = ?, updated_at = ? WHERE id = ?",
                (stage, max(0.0, min(1.0, progress)), time.time(), job_id),
            )

    def finish_job(self, job_id: int, report_json: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, stage = 'done', progress = 1.0,"
                " report_json = COALESCE(?, report_json), updated_at = ? WHERE id = ?",
                (JobStatus.DONE.value, report_json, time.time(), job_id),
            )

    def fail_job(self, job_id: int, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (JobStatus.FAILED.value, error[:2000], time.time(), job_id),
            )

    def delete_job(self, job_id: int) -> None:
        """Clips go too, via the schema's ON DELETE CASCADE."""
        with self.connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def backfill_durations(self) -> int:
        """Fill `duration` for jobs analyzed before the column existed.

        Their runtime is already in the stored report, so a workspace from
        before this migration would otherwise read 0:00 forever. Runs once —
        after this, every row has a duration and the query matches nothing.
        """
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, report_json FROM jobs"
                " WHERE duration = 0 AND report_json IS NOT NULL"
            ).fetchall()

            filled = 0
            for row in rows:
                try:
                    duration = float(json.loads(row["report_json"])["media"]["duration"])
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    continue
                if duration <= 0:
                    continue
                conn.execute(
                    "UPDATE jobs SET duration = ? WHERE id = ?", (duration, row["id"])
                )
                filled += 1
        return filled

    def all_source_paths(self) -> list[str]:
        """Every job's source, including the one a caller is about to delete."""
        with self.connect() as conn:
            rows = conn.execute("SELECT source_path FROM jobs").fetchall()
        return [r["source_path"] for r in rows]

    def rename_job(self, job_id: int, title: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET title = ?, updated_at = ? WHERE id = ?",
                (title.strip() or None, time.time(), job_id),
            )
        return self.get_job(job_id)

    def set_media(self, job_id: int, duration: float, poster_path: str | None) -> None:
        """Denormalize what the home list needs, so it never parses report_json."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET duration = ?, poster_path = ?, updated_at = ?"
                " WHERE id = ?",
                (duration, poster_path, time.time(), job_id),
            )

    def requeue_running_jobs(self) -> int:
        """Return jobs abandoned by a crash to the queue.

        Without this a process restart leaves a job stuck at 'running' forever
        with nothing driving it.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = ?, stage = '', progress = 0.0 WHERE status = ?",
                (JobStatus.QUEUED.value, JobStatus.RUNNING.value),
            )
            return cursor.rowcount

    # --- clips ---------------------------------------------------------------

    def replace_clips(self, job_id: int, candidates) -> None:
        """Store a fresh analysis, discarding anything from a previous run."""
        with self.connect() as conn:
            conn.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
            conn.executemany(
                "INSERT INTO clips (job_id, position, start, end, score, category,"
                " hook_title, reason, excerpt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        job_id,
                        i,
                        c.start,
                        c.end,
                        c.score,
                        c.category.value,
                        c.hook_title,
                        c.reason,
                        c.excerpt,
                    )
                    for i, c in enumerate(candidates)
                ],
            )

    def list_clips(self, job_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM clips WHERE job_id = ? ORDER BY position", (job_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_clip(self, clip_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return dict(row) if row else None

    def update_clip(
        self,
        clip_id: int,
        start: float | None = None,
        end: float | None = None,
        selected: bool | None = None,
    ) -> dict[str, Any] | None:
        fields, values = [], []
        if start is not None:
            fields.append("start = ?")
            values.append(start)
        if end is not None:
            fields.append("end = ?")
            values.append(end)
        if selected is not None:
            fields.append("selected = ?")
            values.append(int(selected))

        if fields:
            with self.connect() as conn:
                conn.execute(
                    f"UPDATE clips SET {', '.join(fields)} WHERE id = ?", (*values, clip_id)
                )
        return self.get_clip(clip_id)

    def set_rendered_path(self, clip_id: int, path: Path) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE clips SET rendered_path = ? WHERE id = ?", (str(path), clip_id))

    def selected_clips(self, job_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM clips WHERE job_id = ? AND selected = 1 ORDER BY position",
                (job_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def report_for(self, job_id: int) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if not job or not job.get("report_json"):
            return None
        return json.loads(job["report_json"])
