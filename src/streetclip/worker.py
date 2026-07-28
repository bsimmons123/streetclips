"""Background worker: drains the job queue on a thread.

One worker, one job at a time. A 2-hour recording is bounded by ffmpeg and API
latency rather than by how many jobs run in parallel, and serializing keeps CPU
contention away from the encode.
"""

from __future__ import annotations

import logging
import threading
import traceback
from pathlib import Path

from streetclip import workspaces
from streetclip.config import Settings, get_settings
from streetclip.db import Database, JobKind, JobStatus
from streetclip.models import Candidate, Category, Report
from streetclip.pipeline import ingest, run

log = logging.getLogger(__name__)


def clip_to_candidate(row: dict) -> Candidate:
    """Rebuild a Candidate from a stored row, including operator edits."""
    return Candidate(
        start=row["start"],
        end=row["end"],
        score=row["score"],
        category=Category(row["category"]),
        hook_title=row["hook_title"],
        reason=row["reason"],
        excerpt=row["excerpt"],
    )


class Worker:
    def __init__(
        self,
        db: Database,
        data_dir: Path,
        settings: Settings | None = None,
        poll_seconds: float = 0.5,
    ) -> None:
        self.db = db
        self.data_dir = data_dir
        self.settings = settings or get_settings()
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        # Anything left 'running' belongs to a previous process that died.
        self.db.requeue_running_jobs()
        self._thread = threading.Thread(target=self._loop, name="streetclip-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._stop.wait(self.poll_seconds)

    # --- work ----------------------------------------------------------------

    def run_once(self) -> bool:
        """Claim and process one job. Returns False when the queue is empty."""
        job = self.db.claim_next_job()
        if job is None:
            return False

        try:
            if job["kind"] == JobKind.ANALYZE:
                self._analyze(job)
            elif job["kind"] == JobKind.RENDER:
                self._render(job)
            else:
                raise ValueError(f"unknown job kind: {job['kind']}")
        except Exception as exc:  # noqa: BLE001 - the queue must survive any failure
            log.exception("job %s failed", job["id"])
            self.db.fail_job(job["id"], f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return True

    def job_dir(self, job_id: int) -> Path:
        return workspaces.job_dir(self.data_dir, job_id)

    def _analyze(self, job: dict) -> None:
        job_id = job["id"]

        def progress(stage: str, fraction: float) -> None:
            self.db.update_progress(job_id, stage, fraction)

        report = run.analyze(
            Path(job["source_path"]),
            work_dir=self.job_dir(job_id) / "work",
            settings=self.settings,
            progress=progress,
        )

        self.db.replace_clips(job_id, report.candidates)

        poster = None
        try:
            # A tenth of the way in, so the frame is not a black lead-in.
            ingest.poster_frame(
                Path(job["source_path"]),
                workspaces.poster_path(self.data_dir, job_id),
                at=min(10.0, report.media.duration / 10),
                settings=self.settings,
            )
            poster = str(workspaces.poster_path(self.data_dir, job_id))
        except Exception:
            # A missing thumbnail is a cosmetic loss, not a failed analysis.
            log.warning("could not write a poster for job %s", job_id, exc_info=True)

        self.db.set_media(job_id, report.media.duration, poster)
        self.db.finish_job(job_id, report.model_dump_json())

        freed = workspaces.purge_intermediates(self.data_dir, job_id)
        log.info("job %s freed %.0f MB of intermediates", job_id, freed / 1e6)

    def _render(self, job: dict) -> None:
        """Render the clips the operator selected on the analysis job.

        A render job carries the analyze job's id in `source_path` so the queue
        stays a single table; the bounds come from the clips table rather than
        the stored report, because the operator may have adjusted them.
        """
        job_id = job["id"]
        analyze_job_id = int(job["source_path"])

        stored = self.db.report_for(analyze_job_id)
        if stored is None:
            raise ValueError(f"job {analyze_job_id} has no analysis to render from")
        report = Report.model_validate(stored)

        rows = self.db.selected_clips(analyze_job_id)
        if not rows:
            raise ValueError("no clips were selected")

        # Render from the operator's edited bounds, in their ranked order.
        report.candidates = [clip_to_candidate(row) for row in rows]

        out_dir = self.job_dir(analyze_job_id) / "shorts"
        written = run.render_shorts(
            report,
            out_dir=out_dir,
            work_dir=self.job_dir(analyze_job_id) / "work",
            burn_captions=self.settings.render_captions,
            track_speaker=self.settings.render_track_speaker,
            settings=self.settings,
            progress=lambda stage, fraction: self.db.update_progress(job_id, stage, fraction),
        )

        for row, path in zip(rows, written, strict=False):
            self.db.set_rendered_path(row["id"], path)

        self.db.finish_job(job_id)


def enqueue_render(db: Database, analyze_job_id: int) -> int:
    """Queue a render of whatever is currently selected on an analysis job."""
    job = db.get_job(analyze_job_id)
    if job is None:
        raise ValueError(f"no such job: {analyze_job_id}")
    if job["status"] != JobStatus.DONE:
        raise ValueError("cannot render a job that has not finished analyzing")

    return db.create_job(
        JobKind.RENDER,
        Path(str(analyze_job_id)),
        job["source_name"],
        user_id=job["user_id"],
    )
