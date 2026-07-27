"""FastAPI app: job submission, review, export, and the SPA that drives them."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from streetclip.config import Settings, get_settings
from streetclip.db import Database, JobKind, JobStatus
from streetclip.ranged import ranged_file_response
from streetclip.worker import Worker, enqueue_render

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mpg", ".mpeg", ".ts"}

# How often the SSE endpoint re-reads job state. Fast enough to feel live,
# slow enough that a long analysis doesn't hammer SQLite.
POLL_SECONDS = 0.5


class ClipUpdate(BaseModel):
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)
    selected: bool | None = None


class JobRequest(BaseModel):
    path: str


def clip_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "position": row["position"],
        "start": row["start"],
        "end": row["end"],
        "duration": row["end"] - row["start"],
        "score": row["score"],
        "category": row["category"],
        "hook_title": row["hook_title"],
        "reason": row["reason"],
        "excerpt": row["excerpt"],
        "selected": bool(row["selected"]),
        "rendered": row["rendered_path"] is not None,
    }


def job_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "stage": row["stage"],
        "progress": row["progress"],
        "error": row["error"],
        "source_name": row["source_name"],
        "created_at": row["created_at"],
    }


def create_app(
    settings: Settings | None = None,
    data_dir: Path | None = None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    data_dir = data_dir or Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    db = Database(data_dir / "streetclip.db")
    worker = Worker(db, data_dir, settings=settings)
    input_dir = Path(settings.input_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            worker.start()
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(title="streetclip", lifespan=lifespan)
    app.state.db = db
    app.state.worker = worker
    app.state.settings = settings
    app.state.data_dir = data_dir

    # --- sources -------------------------------------------------------------

    @app.get("/api/inputs")
    def list_inputs() -> dict[str, Any]:
        """Video files sitting in the mounted input directory."""
        if not input_dir.is_dir():
            return {"dir": str(input_dir), "files": []}

        files = [
            {"name": p.name, "path": str(p), "size": p.stat().st_size}
            for p in sorted(input_dir.iterdir())
            if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
        ]
        return {"dir": str(input_dir), "files": files}

    # --- jobs ----------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return [job_payload(j) for j in db.list_jobs()]

    @app.post("/api/jobs", status_code=201)
    def create_job(request: JobRequest) -> dict[str, Any]:
        """Analyze a file already on disk, picked from the input directory."""
        source = Path(request.path).expanduser()
        if not source.is_file():
            raise HTTPException(404, f"no such file: {source}")
        # Only files from the configured input directory may be submitted by
        # path, so a crafted request cannot reach the rest of the filesystem.
        if not _within(source, input_dir):
            raise HTTPException(403, "path is outside the input directory")

        job_id = db.create_job(JobKind.ANALYZE, source.resolve(), source.name)
        return job_payload(db.get_job(job_id))

    @app.post("/api/jobs/upload", status_code=201)
    async def upload_job(file: UploadFile) -> dict[str, Any]:
        """Analyze an uploaded file."""
        name = Path(file.filename or "upload.mp4").name
        uploads = data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / name

        with dest.open("wb") as handle:
            # Streamed rather than read whole: these files run to gigabytes.
            while chunk := await file.read(1024 * 1024):
                handle.write(chunk)

        job_id = db.create_job(JobKind.ANALYZE, dest, name)
        return job_payload(db.get_job(job_id))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: int) -> dict[str, Any]:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "no such job")

        payload = job_payload(job)
        payload["clips"] = [clip_payload(c) for c in db.list_clips(job_id)]
        report = db.report_for(job_id)
        payload["media"] = report.get("media") if report else None
        return payload

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: int, request: Request) -> StreamingResponse:
        """Server-sent events carrying job progress until it settles."""
        if db.get_job(job_id) is None:
            raise HTTPException(404, "no such job")

        async def stream():
            last: str | None = None
            while True:
                if await request.is_disconnected():
                    return

                job = db.get_job(job_id)
                if job is None:
                    return

                payload = json.dumps(job_payload(job))
                # Only send on change, so an idle job isn't a heartbeat firehose.
                if payload != last:
                    last = payload
                    yield f"data: {payload}\n\n"

                if job["status"] in (JobStatus.DONE, JobStatus.FAILED):
                    return
                await asyncio.sleep(POLL_SECONDS)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/jobs/{job_id}/source")
    def job_source(job_id: int, request: Request):
        """The original recording, seekable, for previewing candidate bounds."""
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "no such job")

        source = Path(job["source_path"])
        if not source.is_file():
            raise HTTPException(404, "source file is gone")
        return ranged_file_response(source, request.headers.get("range", ""))

    @app.post("/api/jobs/{job_id}/render", status_code=201)
    def render_job(job_id: int) -> dict[str, Any]:
        try:
            render_id = enqueue_render(db, job_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return job_payload(db.get_job(render_id))

    # --- clips ---------------------------------------------------------------

    @app.patch("/api/clips/{clip_id}")
    def update_clip(clip_id: int, update: ClipUpdate) -> dict[str, Any]:
        existing = db.get_clip(clip_id)
        if existing is None:
            raise HTTPException(404, "no such clip")

        start = update.start if update.start is not None else existing["start"]
        end = update.end if update.end is not None else existing["end"]
        if end <= start:
            raise HTTPException(400, "end must be after start")

        row = db.update_clip(clip_id, update.start, update.end, update.selected)
        return clip_payload(row)

    @app.get("/api/clips/{clip_id}/download")
    def download_clip(clip_id: int) -> FileResponse:
        clip = db.get_clip(clip_id)
        if clip is None:
            raise HTTPException(404, "no such clip")
        if not clip["rendered_path"]:
            raise HTTPException(409, "clip has not been rendered yet")

        path = Path(clip["rendered_path"])
        if not path.is_file():
            raise HTTPException(404, "rendered file is gone")
        return FileResponse(path, filename=path.name, media_type="video/mp4")

    # --- SPA -----------------------------------------------------------------

    spa_dir = Path(__file__).parent / "static"
    if spa_dir.is_dir():
        app.mount("/", StaticFiles(directory=spa_dir, html=True), name="spa")

    return app


def _within(path: Path, parent: Path) -> bool:
    """True when `path` resolves to somewhere inside `parent`."""
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
