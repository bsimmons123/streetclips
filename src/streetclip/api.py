"""FastAPI app: job submission, review, export, and the SPA that drives them."""

from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from streetclip import workspaces as workspaces_fs
from streetclip.accounts import Accounts
from streetclip.auth import bootstrap_admin, make_dependencies
from streetclip.config import Settings, get_settings
from streetclip.db import Database, JobKind, JobStatus
from streetclip.provider_keys import ProviderKeyVault
from streetclip.ranged import ranged_file_response
from streetclip.routes_auth import build_auth_router
from streetclip.worker import Worker, enqueue_render

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mpg", ".mpeg", ".ts"}

# How often the SSE endpoint re-reads job state. Fast enough to feel live,
# slow enough that a long analysis doesn't hammer SQLite.
POLL_SECONDS = 0.5
USER_UPLOAD_QUOTA = 15 * 1024**3


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


class RenameRequest(BaseModel):
    title: str = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()


def workspace_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Shape for both the list and the detail view.

    `report_json` is deliberately absent — it carries the whole transcript,
    which runs to megabytes on a long recording.
    """
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "stage": row["stage"],
        "progress": row["progress"],
        "error": row["error"],
        "source_name": row["source_name"],
        "title": row["title"] or row["source_name"],
        "duration": row["duration"],
        "has_poster": bool(row["poster_path"]),
        "created_at": row["created_at"],
        # Only the list query carries tallies; the detail route has none.
        "clip_count": row.get("clip_count", 0),
        "kept_count": row.get("kept_count", 0),
        "rendered_count": row.get("rendered_count", 0),
    }


def create_app(
    settings: Settings | None = None,
    data_dir: Path | None = None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    data_dir = data_dir or Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    database_path = (
        Path(settings.database_path)
        if settings.database_path
        else data_dir / "streetclip.db"
    )
    scratch_dir = Path(settings.scratch_dir) if settings.scratch_dir else data_dir
    database_path.parent.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    db = Database(database_path)
    input_dir = Path(settings.input_dir)

    accounts = Accounts(database_path)
    key_vault = ProviderKeyVault(
        accounts,
        settings.key_encryption_secret,
        database_path.parent / ".provider-key-secret",
    )
    worker = Worker(
        db,
        data_dir,
        scratch_dir=scratch_dir,
        settings=settings,
        accounts=accounts,
        key_vault=key_vault,
    )
    # Captured before bootstrap_admin runs: it is the only reliable signal
    # that this boot is the one creating the very first account. On every
    # later boot the admin already exists, so this is non-empty and the
    # backfill below is skipped — bootstrap_admin's return value can't tell
    # "just created" from "already existed" (deliberately, so a restart
    # never resets the admin's password), so the call site checks this
    # instead of the returned id.
    no_accounts_yet = not accounts.list_users()
    admin_id = bootstrap_admin(accounts, settings)
    if admin_id is not None and no_accounts_yet:
        # One-time migration: workspaces that predate accounts become the
        # first admin's. Runs only on the boot that creates that admin.
        db.backfill_owner(admin_id)

    current_user, _approved_user, admin_user = make_dependencies(accounts)

    def resource_user(user=Depends(current_user)):
        if user["approved_at"] is None:
            raise HTTPException(403, "this account is awaiting approval")
        if not user["is_admin"] and not key_vault.configured(user):
            raise HTTPException(403, "add personal Groq and Anthropic keys first")
        return user

    def _owned(job_id: int, user: dict[str, Any]) -> dict[str, Any]:
        """The job, if this user owns it.

        404 rather than 403 for someone else's workspace: a 403 confirms one
        exists at that id.
        """
        job = db.get_job(job_id)
        if job is None or job["user_id"] != user["id"]:
            raise HTTPException(404, "no such workspace")
        return job

    def _owned_clip(clip_id: int, user: dict[str, Any]) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if clip is None:
            raise HTTPException(404, "no such clip")
        _owned(clip["job_id"], user)
        return clip

    def _delete_job_and_files(job: dict[str, Any]) -> None:
        source = Path(job["source_path"])
        shared = workspaces_fs.source_is_shared(source, db.all_source_paths())
        db.delete_job(job["id"])
        workspaces_fs.delete_workspace(data_dir, job["id"])
        if scratch_dir != data_dir:
            workspaces_fs.delete_workspace(scratch_dir, job["id"])
        if not shared and _within(source, data_dir / "uploads") and source.is_file():
            source.unlink()

    def _delete_user_data(user_id: int) -> None:
        for job in db.list_jobs_for_user(user_id):
            _delete_job_and_files(job)

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
    app.state.database_path = database_path
    app.state.scratch_dir = scratch_dir

    app.include_router(
        build_auth_router(accounts, settings, _delete_user_data, key_vault)
    )

    # --- sources -------------------------------------------------------------

    @app.get("/api/inputs")
    def list_inputs(user=Depends(current_user)) -> dict[str, Any]:
        """Video files sitting in the mounted input directory."""
        if not input_dir.is_dir():
            return {"dir": str(input_dir), "files": []}

        files = [
            {"name": p.name, "path": str(p), "size": p.stat().st_size}
            for p in sorted(input_dir.iterdir())
            if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
        ]
        return {"dir": str(input_dir), "files": files}

    # --- workspaces ----------------------------------------------------------

    @app.get("/api/workspaces")
    def list_workspaces(user=Depends(current_user)) -> list[dict[str, Any]]:
        """Analyze jobs with their clip tallies. Render jobs stay internal."""
        return [workspace_payload(w) for w in db.list_workspaces(user["id"])]

    def _dashboard_for(user_id: int) -> dict[str, int | float]:
        jobs = db.list_jobs_for_user(user_id)
        analyses = [job for job in jobs if job["kind"] == JobKind.ANALYZE]
        uploads_dir = data_dir / "uploads" / str(user_id)
        uploaded = [
            job for job in analyses if _within(Path(job["source_path"]), uploads_dir)
        ]
        return {
            "uploads": len(uploaded),
            "workspaces": len(analyses),
            "completed": sum(job["status"] == JobStatus.DONE for job in analyses),
            "processing": sum(
                job["status"] in (JobStatus.QUEUED, JobStatus.RUNNING)
                for job in analyses
            ),
            "failed": sum(job["status"] == JobStatus.FAILED for job in analyses),
            "exports": sum(job["kind"] == JobKind.RENDER for job in jobs),
            "processing_minutes": round(
                sum(float(job["duration"] or 0) for job in analyses) / 60, 1
            ),
        }

    @app.get("/api/dashboard")
    def dashboard(user=Depends(current_user)) -> dict[str, int | float]:
        """General account overview; every signed-in user sees only their own data."""
        return _dashboard_for(user["id"])

    @app.get("/api/admin/dashboard")
    def admin_dashboard(admin=Depends(admin_user)) -> dict[str, Any]:
        """Platform-wide operational usage. Billing stays unavailable until connected."""
        users = accounts.list_users()
        rows = []
        for user in users:
            usage = _dashboard_for(user["id"])
            uploads_dir = data_dir / "uploads" / str(user["id"])
            storage_bytes = sum(
                path.stat().st_size
                for path in uploads_dir.rglob("*")
                if path.is_file()
            ) if uploads_dir.exists() else 0
            rows.append({
                "id": user["id"],
                "email": user["email"],
                "approved": user["approved_at"] is not None,
                "disabled": user["disabled_at"] is not None,
                "quota_unlimited": bool(user.get("quota_unlimited")),
                "storage_bytes": storage_bytes,
                **usage,
            })

        return {
            "accounts": len(rows),
            "active_accounts": sum(r["approved"] and not r["disabled"] for r in rows),
            "workspaces": sum(r["workspaces"] for r in rows),
            "processing_minutes": round(sum(r["processing_minutes"] for r in rows), 1),
            "storage_bytes": sum(r["storage_bytes"] for r in rows),
            "users": rows,
            "billing": {
                "configured": False,
                "provider": None,
                "message": "No billing provider is connected.",
            },
        }

    @app.patch("/api/workspaces/{job_id}")
    def rename_workspace(
        job_id: int, request: RenameRequest, user=Depends(current_user)
    ) -> dict[str, Any]:
        _owned(job_id, user)
        return workspace_payload(db.rename_job(job_id, request.title))

    @app.delete("/api/workspaces/{job_id}", status_code=204)
    def delete_workspace(job_id: int, user=Depends(current_user)) -> Response:
        job = _owned(job_id, user)
        _delete_job_and_files(job)
        return Response(status_code=204)

    @app.get("/api/workspaces/{job_id}/transcript")
    def workspace_transcript(job_id: int, user=Depends(current_user)) -> dict[str, Any]:
        """Words and segments only — the report also carries every candidate."""
        _owned(job_id, user)
        report = db.report_for(job_id)
        if report is None:
            raise HTTPException(404, "this workspace has no transcript yet")

        transcript = report.get("transcript", {})
        return {
            "duration": transcript.get("duration", 0.0),
            "words": transcript.get("words", []),
            "segments": transcript.get("segments", []),
        }

    @app.get("/api/workspaces/{job_id}/poster")
    def workspace_poster(job_id: int, user=Depends(current_user)) -> FileResponse:
        job = _owned(job_id, user)
        if not job["poster_path"]:
            raise HTTPException(404, "no poster")

        path = Path(job["poster_path"])
        if not path.is_file():
            raise HTTPException(404, "poster file is gone")
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/workspaces", status_code=201)
    def create_job(request: JobRequest, user=Depends(resource_user)) -> dict[str, Any]:
        """Analyze a file already on disk, picked from the input directory."""
        source = Path(request.path).expanduser()
        if not source.is_file():
            raise HTTPException(404, f"no such file: {source}")
        # Only files from the configured input directory may be submitted by
        # path, so a crafted request cannot reach the rest of the filesystem.
        if not _within(source, input_dir):
            raise HTTPException(403, "path is outside the input directory")

        job_id = db.create_job(JobKind.ANALYZE, source.resolve(), source.name, user_id=user["id"])
        return workspace_payload(db.get_job(job_id))

    @app.post("/api/workspaces/upload", status_code=201)
    async def upload_job(file: UploadFile, user=Depends(resource_user)) -> dict[str, Any]:
        """Analyze an uploaded file."""
        name = Path(file.filename or "upload.mp4").name
        uploads = data_dir / "uploads" / str(user["id"])
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / name
        used = sum(
            path.stat().st_size
            for path in uploads.rglob("*")
            if path.is_file() and path != dest
        )
        quota = None if user["is_admin"] or user["quota_unlimited"] else USER_UPLOAD_QUOTA
        written = 0
        partial = uploads / f".{name}.{secrets.token_hex(8)}.part"

        try:
            with partial.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    written += len(chunk)
                    if quota is not None and used + written > quota:
                        raise HTTPException(413, "15 GB upload quota exceeded")
                    handle.write(chunk)
            partial.replace(dest)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

        job_id = db.create_job(JobKind.ANALYZE, dest, name, user_id=user["id"])
        return workspace_payload(db.get_job(job_id))

    @app.get("/api/workspaces/{job_id}")
    def get_job(job_id: int, user=Depends(current_user)) -> dict[str, Any]:
        job = _owned(job_id, user)

        payload = workspace_payload(job)
        payload["clips"] = [clip_payload(c) for c in db.list_clips(job_id)]
        report = db.report_for(job_id)
        payload["media"] = report.get("media") if report else None
        return payload

    @app.get("/api/workspaces/{job_id}/events")
    async def job_events(
        job_id: int, request: Request, user=Depends(current_user)
    ) -> StreamingResponse:
        """Server-sent events carrying job progress until it settles."""
        _owned(job_id, user)

        async def stream():
            last: str | None = None
            while True:
                if await request.is_disconnected():
                    return

                job = db.get_job(job_id)
                if job is None:
                    return

                payload = json.dumps(workspace_payload(job))
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

    @app.get("/api/workspaces/{job_id}/source")
    def job_source(job_id: int, request: Request, user=Depends(current_user)):
        """The original recording, seekable, for previewing candidate bounds."""
        job = _owned(job_id, user)

        source = Path(job["source_path"])
        if not source.is_file():
            raise HTTPException(404, "source file is gone")
        return ranged_file_response(source, request.headers.get("range", ""))

    @app.post("/api/workspaces/{job_id}/render", status_code=201)
    def render_job(job_id: int, user=Depends(resource_user)) -> dict[str, Any]:
        _owned(job_id, user)
        try:
            render_id = enqueue_render(db, job_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return workspace_payload(db.get_job(render_id))

    # --- clips ---------------------------------------------------------------

    @app.patch("/api/clips/{clip_id}")
    def update_clip(
        clip_id: int, update: ClipUpdate, user=Depends(current_user)
    ) -> dict[str, Any]:
        existing = _owned_clip(clip_id, user)

        start = update.start if update.start is not None else existing["start"]
        end = update.end if update.end is not None else existing["end"]
        if end <= start:
            raise HTTPException(400, "end must be after start")

        row = db.update_clip(clip_id, update.start, update.end, update.selected)
        return clip_payload(row)

    @app.get("/api/clips/{clip_id}/download")
    def download_clip(clip_id: int, user=Depends(current_user)) -> FileResponse:
        clip = _owned_clip(clip_id, user)
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
