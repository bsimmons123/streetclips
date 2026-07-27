from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from streetclip.api import create_app
from streetclip.config import Settings
from streetclip.db import Database, JobKind
from streetclip.models import Candidate, Category, MediaInfo, Report, Transcript, Word
from streetclip.ranged import parse_range

from .conftest import needs_ffmpeg


@pytest.fixture
def env(tmp_path: Path):
    """An app with its own data and input directories, and no worker thread."""
    data_dir = tmp_path / "data"
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)

    settings = Settings(data_dir=str(data_dir), input_dir=str(input_dir))
    app = create_app(settings, data_dir=data_dir, start_worker=False)
    with TestClient(app) as client:
        yield client, Database(data_dir / "streetclip.db"), input_dir


def _candidate(start=1.0, end=5.0, title="Who told you that") -> Candidate:
    return Candidate(
        start=start, end=end, score=9.0, category=Category.CONFRONTATION,
        hook_title=title, reason="r", excerpt="e",
    )


def _seed_done_job(db: Database, source: Path, candidates=None) -> int:
    job_id = db.create_job(JobKind.ANALYZE, source, source.name)
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


# --- inputs ------------------------------------------------------------------


def test_lists_only_video_files_from_the_input_dir(env):
    client, _, input_dir = env
    (input_dir / "session.mp4").write_bytes(b"x")
    (input_dir / "notes.txt").write_text("not a video")
    (input_dir / "clip.MOV").write_bytes(b"x")

    body = client.get("/api/inputs").json()
    assert sorted(f["name"] for f in body["files"]) == ["clip.MOV", "session.mp4"]


def test_missing_input_dir_is_not_an_error(tmp_path: Path):
    settings = Settings(data_dir=str(tmp_path / "d"), input_dir=str(tmp_path / "nope"))
    with TestClient(create_app(settings, tmp_path / "d", start_worker=False)) as client:
        assert client.get("/api/inputs").json()["files"] == []


# --- job creation ------------------------------------------------------------


def test_creating_a_job_from_an_input_file(env):
    client, _, input_dir = env
    source = input_dir / "session.mp4"
    source.write_bytes(b"x")

    response = client.post("/api/jobs", json={"path": str(source)})
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert response.json()["source_name"] == "session.mp4"


def test_creating_a_job_for_a_missing_file(env):
    client, _, input_dir = env
    assert client.post("/api/jobs", json={"path": str(input_dir / "nope.mp4")}).status_code == 404


def test_paths_outside_the_input_dir_are_refused(env, tmp_path: Path):
    """A crafted path must not reach the rest of the filesystem."""
    client, _, _ = env
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")

    assert client.post("/api/jobs", json={"path": str(outside)}).status_code == 403


def test_traversal_out_of_the_input_dir_is_refused(env, tmp_path: Path):
    client, _, input_dir = env
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")

    sneaky = str(input_dir / ".." / "secret.mp4")
    assert client.post("/api/jobs", json={"path": sneaky}).status_code == 403


def test_uploading_a_file_creates_a_job(env):
    client, _, _ = env
    response = client.post(
        "/api/jobs/upload", files={"file": ("session.mp4", b"video-bytes", "video/mp4")}
    )
    assert response.status_code == 201
    assert response.json()["source_name"] == "session.mp4"


def test_upload_filenames_are_stripped_of_directories(env, tmp_path: Path):
    """A filename like ../../etc/x must not escape the uploads directory."""
    client, _, _ = env
    response = client.post(
        "/api/jobs/upload", files={"file": ("../../evil.mp4", b"x", "video/mp4")}
    )
    assert response.status_code == 201
    assert response.json()["source_name"] == "evil.mp4"
    assert (tmp_path / "data" / "uploads" / "evil.mp4").is_file()


# --- job reads ---------------------------------------------------------------


def test_reading_a_finished_job_includes_clips_and_media(env, tmp_path: Path):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source)

    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "done"
    assert len(body["clips"]) == 1
    assert body["clips"][0]["hook_title"] == "Who told you that"
    assert body["clips"][0]["duration"] == 4.0
    assert body["media"]["width"] == 640


def test_reading_a_missing_job(env):
    client, _, _ = env
    assert client.get("/api/jobs/999").status_code == 404


def test_listing_jobs(env):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    _seed_done_job(db, source)

    body = client.get("/api/jobs").json()
    assert len(body) == 1
    assert body[0]["kind"] == "analyze"


# --- clip editing ------------------------------------------------------------


def test_editing_clip_bounds(env):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source)
    clip_id = db.list_clips(job_id)[0]["id"]

    body = client.patch(f"/api/clips/{clip_id}", json={"start": 2.0, "end": 4.0}).json()
    assert body["start"] == 2.0
    assert body["duration"] == 2.0


def test_selecting_a_clip(env):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source)
    clip_id = db.list_clips(job_id)[0]["id"]

    assert client.patch(f"/api/clips/{clip_id}", json={"selected": True}).json()["selected"]


def test_inverted_bounds_are_refused(env):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source)
    clip_id = db.list_clips(job_id)[0]["id"]

    response = client.patch(f"/api/clips/{clip_id}", json={"start": 8.0, "end": 2.0})
    assert response.status_code == 400


def test_a_partial_edit_is_validated_against_stored_bounds(env):
    """Sending only `start` must still be checked against the existing `end`."""
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source)
    clip_id = db.list_clips(job_id)[0]["id"]  # 1.0 -> 5.0

    assert client.patch(f"/api/clips/{clip_id}", json={"start": 9.0}).status_code == 400


def test_negative_bounds_are_refused(env):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source)
    clip_id = db.list_clips(job_id)[0]["id"]

    assert client.patch(f"/api/clips/{clip_id}", json={"start": -5.0}).status_code == 422


def test_editing_a_missing_clip(env):
    client, _, _ = env
    assert client.patch("/api/clips/999", json={"selected": True}).status_code == 404


# --- render ------------------------------------------------------------------


def test_render_queues_a_job(env):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source)
    db.update_clip(db.list_clips(job_id)[0]["id"], selected=True)

    response = client.post(f"/api/jobs/{job_id}/render")
    assert response.status_code == 201
    assert response.json()["kind"] == "render"
    assert response.json()["status"] == "queued"


def test_render_of_an_unfinished_job_is_refused(env):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = db.create_job(JobKind.ANALYZE, source, "s.mp4")

    assert client.post(f"/api/jobs/{job_id}/render").status_code == 400


def test_downloading_before_rendering_is_a_conflict(env):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source)
    clip_id = db.list_clips(job_id)[0]["id"]

    assert client.get(f"/api/clips/{clip_id}/download").status_code == 409


def test_downloading_a_rendered_clip(env, tmp_path: Path):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source)
    clip_id = db.list_clips(job_id)[0]["id"]

    rendered = tmp_path / "short.mp4"
    rendered.write_bytes(b"rendered-bytes")
    db.set_rendered_path(clip_id, rendered)

    response = client.get(f"/api/clips/{clip_id}/download")
    assert response.status_code == 200
    assert response.content == b"rendered-bytes"


def test_downloading_when_the_file_has_been_deleted(env, tmp_path: Path):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = _seed_done_job(db, source)
    clip_id = db.list_clips(job_id)[0]["id"]
    db.set_rendered_path(clip_id, tmp_path / "gone.mp4")

    assert client.get(f"/api/clips/{clip_id}/download").status_code == 404


# --- source streaming --------------------------------------------------------


@needs_ffmpeg
def test_source_is_served_with_range_support(env, sample_video: Path):
    """Seeking is what makes reviewing a 2hr recording bearable."""
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(sample_video.read_bytes())
    job_id = db.create_job(JobKind.ANALYZE, source, "s.mp4")

    full = client.get(f"/api/jobs/{job_id}/source")
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"

    partial = client.get(f"/api/jobs/{job_id}/source", headers={"Range": "bytes=100-199"})
    assert partial.status_code == 206
    assert partial.headers["content-range"].startswith("bytes 100-199/")
    assert len(partial.content) == 100
    assert partial.content == full.content[100:200]


def test_source_for_a_missing_job(env):
    client, _, _ = env
    assert client.get("/api/jobs/999/source").status_code == 404


def test_source_when_the_file_has_been_removed(env, tmp_path: Path):
    client, db, _ = env
    job_id = db.create_job(JobKind.ANALYZE, tmp_path / "gone.mp4", "gone.mp4")
    assert client.get(f"/api/jobs/{job_id}/source").status_code == 404


# --- SSE ---------------------------------------------------------------------


def test_events_stream_reports_progress_then_closes(env):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = db.create_job(JobKind.ANALYZE, source, "s.mp4")
    # Settle the job before connecting so the stream terminates promptly.
    db.finish_job(job_id, json.dumps({"candidates": []}))

    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    payloads = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert payloads[-1]["status"] == "done"
    assert payloads[-1]["progress"] == 1.0


def test_events_stream_reports_failures(env):
    client, db, input_dir = env
    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    job_id = db.create_job(JobKind.ANALYZE, source, "s.mp4")
    db.fail_job(job_id, "ffmpeg exploded")

    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        body = "".join(response.iter_text())

    payloads = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert payloads[-1]["status"] == "failed"
    assert "exploded" in payloads[-1]["error"]


def test_events_for_a_missing_job(env):
    client, _, _ = env
    assert client.get("/api/jobs/999/events").status_code == 404
