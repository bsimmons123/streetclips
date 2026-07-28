from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from streetclip.accounts import Accounts
from streetclip.api import create_app
from streetclip.auth import COOKIE_NAME, hash_password
from streetclip.config import Settings
from streetclip.db import Database, JobKind
from streetclip.models import Candidate, Category, MediaInfo, Report, Transcript, Word
from streetclip.ranged import parse_range

from .conftest import needs_ffmpeg


@pytest.fixture
def env(tmp_path: Path):
    """An app with a signed-in approved user, and no worker thread."""
    data_dir = tmp_path / "data"
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)

    settings = Settings(
        data_dir=str(data_dir),
        input_dir=str(input_dir),
        admin_email="admin@x.com",
        admin_password="adminpw",
        key_encryption_secret="test-encryption-secret",
    )
    app = create_app(settings, data_dir=data_dir, start_worker=False)
    accounts = Accounts(data_dir / "streetclip.db")
    db = Database(data_dir / "streetclip.db")

    user_id = accounts.create_user(
        "me@x.com", hash_password("pw"), is_admin=True, approved=True
    )
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, accounts.create_session(user_id))
        yield client, db, input_dir, user_id, accounts


def _candidate(start=1.0, end=5.0, title="Who told you that") -> Candidate:
    return Candidate(
        start=start, end=end, score=9.0, category=Category.CONFRONTATION,
        hook_title=title, reason="r", excerpt="e",
    )


def _seed_done_job(db: Database, source: Path, user_id: int, candidates=None) -> int:
    job_id = db.create_job(JobKind.ANALYZE, source, source.name, user_id=user_id)
    report = Report(
        media=MediaInfo(path=str(source), duration=10.0, width=640, height=360, fps=25.0),
        transcript=Transcript(duration=10.0, words=[Word(text="a", start=0.0, end=1.0)]),
        candidates=candidates or [_candidate()],
    )
    db.replace_clips(job_id, report.candidates)
    db.finish_job(job_id, report.model_dump_json())
    return job_id


# --- range parsing -----------------------------------------------------------


def test_range_parsing_of_a_normal_request():
    assert parse_range("bytes=0-99", 1000) == (0, 99)


def test_range_open_ended_runs_to_the_end():
    assert parse_range("bytes=500-", 1000) == (500, 999)


def test_range_suffix_takes_the_tail():
    assert parse_range("bytes=-200", 1000) == (800, 999)


def test_range_is_clamped_to_the_file():
    assert parse_range("bytes=0-99999", 1000) == (0, 999)
    assert parse_range("bytes=-99999", 1000) == (0, 999)


def test_unsatisfiable_or_absent_ranges_fall_back():
    assert parse_range("", 1000) is None
    assert parse_range("bytes=2000-3000", 1000) is None  # past the end
    assert parse_range("bytes=500-100", 1000) is None  # inverted
    assert parse_range("bytes=-0", 1000) is None
    assert parse_range("nonsense", 1000) is None
    assert parse_range("bytes=0-10,20-30", 1000) is None  # multi-range


# --- session guard -------------------------------------------------------------


# The only /api/* routes that must work with no session at all. Anything not
# in this set is expected to 401 — adding to it is a deliberate, visible
# decision, not a side effect of some other change.
PUBLIC_API_ROUTES = {("POST", "/api/session"), ("POST", "/api/signup")}


def test_every_api_route_requires_a_session(env):
    """Sweeps every registered /api/* route so a newly added endpoint that
    forgets its auth dependency fails this test instead of shipping unguarded.
    """
    client, _, _, _, _ = env
    client.cookies.clear()

    checked = 0
    for route in client.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not path.startswith("/api/") or not methods:
            continue

        concrete_path = re.sub(r"\{[^{}]+\}", "1", path)
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            if (method, path) in PUBLIC_API_ROUTES:
                continue

            checked += 1
            # client.request(), not client.get()/.delete(): those two don't
            # accept `json=`, and a body-less request is fine for every route
            # here since the auth dependency runs before body validation.
            response = client.request(method, concrete_path, json={})
            assert response.status_code == 401, f"{method} {concrete_path} is unprotected"

    # A sweep that silently matches nothing is worse than no test at all.
    assert checked >= 14, f"expected to sweep at least 14 routes, found {checked}"


def test_another_users_workspace_is_404_not_403(env):
    """403 would confirm the workspace exists."""
    client, db, _, _, _ = env
    theirs = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=999)

    assert client.get(f"/api/workspaces/{theirs}").status_code == 404
    assert client.get(f"/api/workspaces/{theirs}/transcript").status_code == 404
    assert client.get(f"/api/workspaces/{theirs}/poster").status_code == 404
    assert client.get(f"/api/workspaces/{theirs}/source").status_code == 404
    assert client.delete(f"/api/workspaces/{theirs}").status_code == 404
    assert client.patch(f"/api/workspaces/{theirs}", json={"title": "x"}).status_code == 404
    assert client.post(f"/api/workspaces/{theirs}/render").status_code == 404


def test_the_list_shows_only_your_own(env):
    client, db, _, user_id, _ = env
    db.create_job(JobKind.ANALYZE, Path("/x/mine.mp4"), "mine.mp4", user_id=user_id)
    db.create_job(JobKind.ANALYZE, Path("/x/theirs.mp4"), "theirs.mp4", user_id=999)

    names = [w["source_name"] for w in client.get("/api/workspaces").json()]
    assert names == ["mine.mp4"]


def test_a_pending_user_cannot_spend_resources(env, tmp_path: Path):
    client, _, input_dir, _, accounts = env

    pending = accounts.create_user("pending@x.com", hash_password("pw"))
    client.cookies.set(COOKIE_NAME, accounts.create_session(pending))

    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    assert client.post("/api/workspaces", json={"path": str(source)}).status_code == 403
    upload = client.post(
        "/api/workspaces/upload", files={"file": ("s.mp4", b"x", "video/mp4")}
    )
    assert upload.status_code == 403


def test_an_approved_non_admin_cannot_spend_resources(env):
    client, db, input_dir, _, accounts = env
    user_id = accounts.create_user("member@x.com", hash_password("pw"), approved=True)
    client.cookies.set(COOKIE_NAME, accounts.create_session(user_id))
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = db.create_job(JobKind.ANALYZE, source, source.name, user_id=user_id)

    assert client.post("/api/workspaces", json={"path": str(source)}).status_code == 403
    assert (
        client.post(
            "/api/workspaces/upload", files={"file": ("s.mp4", b"x", "video/mp4")}
        ).status_code
        == 403
    )
    assert client.post(f"/api/workspaces/{job_id}/render").status_code == 403


def test_an_approved_non_admin_with_personal_keys_can_upload(env):
    client, _, _, _, accounts = env
    user_id = accounts.create_user("member@x.com", hash_password("pw"), approved=True)
    client.cookies.set(COOKIE_NAME, accounts.create_session(user_id))
    saved = client.put(
        "/api/session/keys",
        json={"groq": "personal-groq", "anthropic": "personal-anthropic"},
    )

    response = client.post(
        "/api/workspaces/upload", files={"file": ("mine.mp4", b"x", "video/mp4")}
    )

    assert saved.status_code == 204
    assert response.status_code == 201
    row = accounts.get_user(user_id)
    assert "personal-groq" not in row["groq_key_encrypted"]


def test_non_admin_upload_quota_is_enforced(env, monkeypatch):
    import streetclip.api as api_module

    monkeypatch.setattr(api_module, "USER_UPLOAD_QUOTA", 1)
    client, _, _, _, accounts = env
    user_id = accounts.create_user("member@x.com", hash_password("pw"), approved=True)
    client.cookies.set(COOKIE_NAME, accounts.create_session(user_id))
    client.put(
        "/api/session/keys",
        json={"groq": "personal-groq", "anthropic": "personal-anthropic"},
    )

    response = client.post(
        "/api/workspaces/upload", files={"file": ("large.mp4", b"xx", "video/mp4")}
    )

    assert response.status_code == 413


def test_admin_can_grant_unlimited_quota(env):
    client, _, _, _, accounts = env
    target = accounts.create_user("member@x.com", hash_password("pw"), approved=True)
    client.post("/api/session", json={"email": "admin@x.com", "password": "adminpw"})

    response = client.post(f"/api/users/{target}/quota", json={"unlimited": True})

    assert response.status_code == 200
    assert response.json()["quota_unlimited"] is True


def test_admin_dashboard_counts_uploads_and_processing(env):
    client, db, input_dir, user_id, _ = env
    upload = input_dir.parent / "data" / "uploads" / str(user_id) / "a.mp4"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"x")
    complete = db.create_job(JobKind.ANALYZE, upload, upload.name, user_id=user_id)
    db.finish_job(complete)
    db.create_job(JobKind.ANALYZE, input_dir / "b.mp4", "b.mp4", user_id=user_id)

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    assert response.json() == {
        "uploads": 1,
        "workspaces": 2,
        "completed": 1,
        "processing": 1,
        "failed": 0,
        "exports": 0,
    }


def test_a_pending_user_can_still_read(env):
    client, _, _, _, accounts = env

    pending = accounts.create_user("pending@x.com", hash_password("pw"))
    client.cookies.set(COOKIE_NAME, accounts.create_session(pending))

    assert client.get("/api/workspaces").status_code == 200
    assert client.get("/api/inputs").status_code == 200


def test_uploads_go_to_a_per_user_directory(env, tmp_path: Path):
    """Two users uploading the same filename must not overwrite each other."""
    client, _, _, user_id, _ = env
    client.post("/api/workspaces/upload", files={"file": ("s.mp4", b"x", "video/mp4")})
    assert (tmp_path / "data" / "uploads" / str(user_id) / "s.mp4").is_file()


# --- inputs ------------------------------------------------------------------


def test_lists_only_video_files_from_the_input_dir(env):
    client, _, input_dir, _, _ = env
    (input_dir / "session.mp4").write_bytes(b"x")
    (input_dir / "notes.txt").write_text("not a video")
    (input_dir / "clip.MOV").write_bytes(b"x")

    body = client.get("/api/inputs").json()
    assert sorted(f["name"] for f in body["files"]) == ["clip.MOV", "session.mp4"]


def test_missing_input_dir_is_not_an_error(tmp_path: Path):
    settings = Settings(
        data_dir=str(tmp_path / "d"),
        input_dir=str(tmp_path / "nope"),
        admin_email="admin@x.com",
        admin_password="adminpw",
    )
    app = create_app(settings, tmp_path / "d", start_worker=False)
    accounts = Accounts(tmp_path / "d" / "streetclip.db")
    user_id = accounts.create_user("me@x.com", hash_password("pw"), approved=True)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, accounts.create_session(user_id))
        assert client.get("/api/inputs").json()["files"] == []


# --- job creation ------------------------------------------------------------


def test_creating_a_job_from_an_input_file(env):
    client, _, input_dir, _, _ = env
    source = input_dir / "session.mp4"
    source.write_bytes(b"x")

    response = client.post("/api/workspaces", json={"path": str(source)})
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert response.json()["source_name"] == "session.mp4"


def test_creating_a_job_for_a_missing_file(env):
    client, _, input_dir, _, _ = env
    response = client.post("/api/workspaces", json={"path": str(input_dir / "nope.mp4")})
    assert response.status_code == 404


def test_paths_outside_the_input_dir_are_refused(env, tmp_path: Path):
    """A crafted path must not reach the rest of the filesystem."""
    client, _, _, _, _ = env
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")

    assert client.post("/api/workspaces", json={"path": str(outside)}).status_code == 403


def test_traversal_out_of_the_input_dir_is_refused(env, tmp_path: Path):
    client, _, input_dir, _, _ = env
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")

    sneaky = str(input_dir / ".." / "secret.mp4")
    assert client.post("/api/workspaces", json={"path": sneaky}).status_code == 403


def test_uploading_a_file_creates_a_job(env):
    client, _, _, _, _ = env
    response = client.post(
        "/api/workspaces/upload", files={"file": ("session.mp4", b"video-bytes", "video/mp4")}
    )
    assert response.status_code == 201
    assert response.json()["source_name"] == "session.mp4"


def test_upload_filenames_are_stripped_of_directories(env, tmp_path: Path):
    """A filename like ../../etc/x must not escape the uploads directory."""
    client, _, _, user_id, _ = env
    response = client.post(
        "/api/workspaces/upload", files={"file": ("../../evil.mp4", b"x", "video/mp4")}
    )
    assert response.status_code == 201
    assert response.json()["source_name"] == "evil.mp4"
    assert (tmp_path / "data" / "uploads" / str(user_id) / "evil.mp4").is_file()


# --- job reads ---------------------------------------------------------------


def test_reading_a_finished_job_includes_clips_and_media(env, tmp_path: Path):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source, user_id)

    body = client.get(f"/api/workspaces/{job_id}").json()
    assert body["status"] == "done"
    assert len(body["clips"]) == 1
    assert body["clips"][0]["hook_title"] == "Who told you that"
    assert body["clips"][0]["duration"] == 4.0
    assert body["media"]["width"] == 640


def test_reading_a_missing_job(env):
    client, _, _, _, _ = env
    assert client.get("/api/workspaces/999").status_code == 404


def test_listing_jobs(env):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    _seed_done_job(db, source, user_id)

    body = client.get("/api/workspaces").json()
    assert len(body) == 1
    assert body[0]["kind"] == "analyze"


# --- clip editing ------------------------------------------------------------


def test_editing_clip_bounds(env):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source, user_id)
    clip_id = db.list_clips(job_id)[0]["id"]

    body = client.patch(f"/api/clips/{clip_id}", json={"start": 2.0, "end": 4.0}).json()
    assert body["start"] == 2.0
    assert body["duration"] == 2.0


def test_selecting_a_clip(env):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source, user_id)
    clip_id = db.list_clips(job_id)[0]["id"]

    assert client.patch(f"/api/clips/{clip_id}", json={"selected": True}).json()["selected"]


def test_inverted_bounds_are_refused(env):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source, user_id)
    clip_id = db.list_clips(job_id)[0]["id"]

    response = client.patch(f"/api/clips/{clip_id}", json={"start": 8.0, "end": 2.0})
    assert response.status_code == 400


def test_a_partial_edit_is_validated_against_stored_bounds(env):
    """Sending only `start` must still be checked against the existing `end`."""
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source, user_id)
    clip_id = db.list_clips(job_id)[0]["id"]  # 1.0 -> 5.0

    assert client.patch(f"/api/clips/{clip_id}", json={"start": 9.0}).status_code == 400


def test_negative_bounds_are_refused(env):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source, user_id)
    clip_id = db.list_clips(job_id)[0]["id"]

    assert client.patch(f"/api/clips/{clip_id}", json={"start": -5.0}).status_code == 422


def test_editing_a_missing_clip(env):
    client, _, _, _, _ = env
    assert client.patch("/api/clips/999", json={"selected": True}).status_code == 404


def test_editing_a_clip_from_another_users_workspace_is_404(env):
    client, db, _, _, _ = env
    theirs = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=999)
    db.replace_clips(theirs, [_candidate()])
    clip_id = db.list_clips(theirs)[0]["id"]

    assert client.patch(f"/api/clips/{clip_id}", json={"selected": True}).status_code == 404
    assert client.get(f"/api/clips/{clip_id}/download").status_code == 404


# --- render ------------------------------------------------------------------


def test_render_queues_a_job(env):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source, user_id)
    db.update_clip(db.list_clips(job_id)[0]["id"], selected=True)

    response = client.post(f"/api/workspaces/{job_id}/render")
    assert response.status_code == 201
    assert response.json()["kind"] == "render"
    assert response.json()["status"] == "queued"


def test_render_of_an_unfinished_job_is_refused(env):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = db.create_job(JobKind.ANALYZE, source, "s.mp4", user_id=user_id)

    assert client.post(f"/api/workspaces/{job_id}/render").status_code == 400


def test_downloading_before_rendering_is_a_conflict(env):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source, user_id)
    clip_id = db.list_clips(job_id)[0]["id"]

    assert client.get(f"/api/clips/{clip_id}/download").status_code == 409


def test_downloading_a_rendered_clip(env, tmp_path: Path):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source, user_id)
    clip_id = db.list_clips(job_id)[0]["id"]

    rendered = tmp_path / "short.mp4"
    rendered.write_bytes(b"rendered-bytes")
    db.set_rendered_path(clip_id, rendered)

    response = client.get(f"/api/clips/{clip_id}/download")
    assert response.status_code == 200
    assert response.content == b"rendered-bytes"


def test_downloading_when_the_file_has_been_deleted(env, tmp_path: Path):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source, user_id)
    clip_id = db.list_clips(job_id)[0]["id"]
    db.set_rendered_path(clip_id, tmp_path / "gone.mp4")

    assert client.get(f"/api/clips/{clip_id}/download").status_code == 404


# --- source streaming --------------------------------------------------------


@needs_ffmpeg
def test_source_is_served_with_range_support(env, sample_video: Path):
    """Seeking is what makes reviewing a 2hr recording bearable."""
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(sample_video.read_bytes())
    job_id = db.create_job(JobKind.ANALYZE, source, "s.mp4", user_id=user_id)

    full = client.get(f"/api/workspaces/{job_id}/source")
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"

    partial = client.get(f"/api/workspaces/{job_id}/source", headers={"Range": "bytes=100-199"})
    assert partial.status_code == 206
    assert partial.headers["content-range"].startswith("bytes 100-199/")
    assert len(partial.content) == 100
    assert partial.content == full.content[100:200]


def test_source_for_a_missing_job(env):
    client, _, _, _, _ = env
    assert client.get("/api/workspaces/999/source").status_code == 404


def test_source_when_the_file_has_been_removed(env, tmp_path: Path):
    client, db, _, user_id, _ = env
    job_id = db.create_job(JobKind.ANALYZE, tmp_path / "gone.mp4", "gone.mp4", user_id=user_id)
    assert client.get(f"/api/workspaces/{job_id}/source").status_code == 404


# --- SSE ---------------------------------------------------------------------


def test_events_stream_reports_progress_then_closes(env):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = db.create_job(JobKind.ANALYZE, source, "s.mp4", user_id=user_id)
    # Settle the job before connecting so the stream terminates promptly.
    db.finish_job(job_id, json.dumps({"candidates": []}))

    with client.stream("GET", f"/api/workspaces/{job_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    payloads = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert payloads[-1]["status"] == "done"
    assert payloads[-1]["progress"] == 1.0


def test_events_stream_reports_failures(env):
    client, db, input_dir, user_id, _ = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = db.create_job(JobKind.ANALYZE, source, "s.mp4", user_id=user_id)
    db.fail_job(job_id, "ffmpeg exploded")

    with client.stream("GET", f"/api/workspaces/{job_id}/events") as response:
        body = "".join(response.iter_text())

    payloads = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert payloads[-1]["status"] == "failed"
    assert "exploded" in payloads[-1]["error"]


def test_events_for_a_missing_job(env):
    client, _, _, _, _ = env
    assert client.get("/api/workspaces/999/events").status_code == 404


# --- workspaces --------------------------------------------------------------


def test_list_workspaces_includes_counts_and_title(env):
    client, db, _, user_id, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id)
    db.rename_job(job_id, "Corner session")

    row = client.get("/api/workspaces").json()[0]
    assert row["title"] == "Corner session"
    assert row["clip_count"] == 0
    assert row["has_poster"] is False
    assert "report_json" not in row, "the transcript must never reach the list"


def test_title_falls_back_to_the_filename(env):
    client, db, _, user_id, _ = env
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id)
    assert client.get("/api/workspaces").json()[0]["title"] == "a.mp4"


def test_rename_workspace(env):
    client, db, _, user_id, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id)
    response = client.patch(f"/api/workspaces/{job_id}", json={"title": "Renamed"})
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"


def test_rename_rejects_a_blank_title(env):
    client, db, _, user_id, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id)
    assert client.patch(f"/api/workspaces/{job_id}", json={"title": "  "}).status_code == 422


def test_rename_a_missing_workspace(env):
    client, _, _, _, _ = env
    assert client.patch("/api/workspaces/999", json={"title": "x"}).status_code == 404


def test_list_workspaces_excludes_render_jobs(env):
    """Render jobs are internal plumbing; the operator never sees them."""
    client, db, _, user_id, _ = env
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id)
    db.create_job(JobKind.RENDER, Path("1"), "a.mp4", user_id=user_id)
    assert len(client.get("/api/workspaces").json()) == 1


# --- delete, transcript, poster ----------------------------------------------


def test_delete_workspace_removes_rows_and_directory(env):
    client, db, input_dir, user_id, _ = env
    data_dir = input_dir.parent / "data"
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id)
    job_dir = data_dir / f"job_{job_id:05d}"
    (job_dir / "shorts").mkdir(parents=True)
    (job_dir / "shorts" / "01.mp4").write_bytes(b"video")

    assert client.delete(f"/api/workspaces/{job_id}").status_code == 204
    assert db.get_job(job_id) is None
    assert not job_dir.exists()


def test_delete_workspace_cascades_to_its_clips(env):
    client, db, _, user_id, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id)
    db.replace_clips(job_id, [_candidate()])
    assert db.list_clips(job_id)

    client.delete(f"/api/workspaces/{job_id}")
    assert db.list_clips(job_id) == []


def test_delete_workspace_removes_its_upload(env):
    client, db, input_dir, user_id, _ = env
    upload = input_dir.parent / "data" / "uploads" / "a.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video")
    job_id = db.create_job(JobKind.ANALYZE, upload, "a.mp4", user_id=user_id)

    client.delete(f"/api/workspaces/{job_id}")
    assert not upload.exists()


def test_delete_workspace_keeps_an_upload_another_job_uses(env):
    """Two workspaces can point at the same uploaded file."""
    client, db, input_dir, user_id, _ = env
    upload = input_dir.parent / "data" / "uploads" / "a.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video")
    first = db.create_job(JobKind.ANALYZE, upload, "a.mp4", user_id=user_id)
    db.create_job(JobKind.ANALYZE, upload, "a.mp4", user_id=user_id)

    client.delete(f"/api/workspaces/{first}")
    assert upload.exists(), "the second workspace still needs it"


def test_delete_leaves_files_in_the_input_directory_alone(env):
    """Originals the operator put there are not ours to remove."""
    client, db, input_dir, user_id, _ = env
    original = input_dir / "a.mp4"
    original.write_bytes(b"video")
    job_id = db.create_job(JobKind.ANALYZE, original, "a.mp4", user_id=user_id)

    client.delete(f"/api/workspaces/{job_id}")
    assert original.exists()


def test_delete_a_missing_workspace(env):
    client, _, _, _, _ = env
    assert client.delete("/api/workspaces/999").status_code == 404


def test_delete_user_removes_owned_jobs_directories_and_upload(env):
    client, db, input_dir, _, accounts = env
    data_dir = input_dir.parent / "data"
    target = accounts.create_user("target@x.com", hash_password("pw"), approved=True)
    upload = data_dir / "uploads" / str(target) / "a.mp4"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"video")
    job_id = db.create_job(JobKind.ANALYZE, upload, "a.mp4", user_id=target)
    job_dir = data_dir / f"job_{job_id:05d}"
    job_dir.mkdir()

    response = client.post(
        "/api/session", json={"email": "admin@x.com", "password": "adminpw"}
    )
    assert response.status_code == 200
    assert client.delete(f"/api/users/{target}").status_code == 204

    assert accounts.get_user(target) is None
    assert db.get_job(job_id) is None
    assert not upload.exists()
    assert not job_dir.exists()


def test_transcript_returns_words_without_the_report(env, sample_video):
    client, db, _, user_id, _ = env
    job_id = _seed_done_job(db, sample_video, user_id)

    body = client.get(f"/api/workspaces/{job_id}/transcript").json()
    assert body["words"][0]["text"] == "a"
    assert body["words"][0]["start"] == 0.0
    assert "candidates" not in body and "media" not in body


def test_transcript_404s_before_analysis_finishes(env):
    client, db, _, user_id, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id)
    assert client.get(f"/api/workspaces/{job_id}/transcript").status_code == 404


def test_poster_404s_when_absent(env):
    client, db, _, user_id, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id)
    assert client.get(f"/api/workspaces/{job_id}/poster").status_code == 404


def test_poster_is_served_when_present(env):
    client, db, input_dir, user_id, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=user_id)
    poster = input_dir.parent / "data" / f"job_{job_id:05d}" / "poster.jpg"
    poster.parent.mkdir(parents=True, exist_ok=True)
    poster.write_bytes(b"\xff\xd8fake-jpeg")
    db.set_media(job_id, 12.5, str(poster))

    response = client.get(f"/api/workspaces/{job_id}/poster")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
