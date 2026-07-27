# Workspaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn each analyze job into a named, browsable workspace with a home screen, a readable transcript you can trim from, and automatic cleanup of intermediate audio.

**Architecture:** A workspace *is* an analyze job — no new container type. Three columns are added to `jobs`, filesystem lifecycle moves into a new `workspaces.py` module so `db.py` stays row-only, and the `/api/jobs` routes are renamed to `/api/workspaces`. The SPA gains a home screen and a transcript panel.

**Tech Stack:** Python 3.12, FastAPI, SQLite, ffmpeg, React 18, Vite.

## Global Constraints

- Python 3.12; `ruff` line-length 100; select `E,F,I,UP,B`.
- Run tests with `.venv/bin/python -m pytest`; lint with `.venv/bin/python -m ruff check .`.
- All work happens from `/Users/bsimmons/Coding_Projects/streetclip`.
- Never write a secret into a tracked file. Keys live in `.env`.
- ffmpeg-dependent tests use the `needs_ffmpeg` marker from `tests/conftest.py`.
- Commit after every task. Conventional Commits; no `Co-Authored-By` trailer.
- `report_json` holds the whole transcript (1-2 MB). No list endpoint may parse it.

## File Structure

| File | Responsibility |
|---|---|
| `src/streetclip/db.py` (modify) | Rows only: new columns, title fallback, count aggregate, rename, delete |
| `src/streetclip/workspaces.py` (**new**) | Filesystem lifecycle: directories, poster path, purge, delete, shared-source guard |
| `src/streetclip/pipeline/ingest.py` (modify) | `poster_frame()` |
| `src/streetclip/worker.py` (modify) | Write poster + duration, purge intermediates; use `workspaces.job_dir` |
| `src/streetclip/api.py` (modify) | `/api/workspaces` routes incl. transcript + poster |
| `web/src/api.js` (modify) | Client for the renamed routes |
| `web/src/WorkspaceList.jsx` (**new**) | Home screen |
| `web/src/Transcript.jsx` (**new**) | Windowed transcript, click-to-seek, drag-to-trim |
| `web/src/Review.jsx` (modify) | Host the transcript panel |
| `web/src/App.jsx` (modify) | Home ↔ workspace navigation |

---

### Task 1: Schema columns and title fallback

**Files:**
- Modify: `src/streetclip/db.py:23-56` (SCHEMA), `src/streetclip/db.py:100-102` (`migrate`)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing
- Produces: `jobs.title`, `jobs.duration`, `jobs.poster_path` columns; `Database.rename_job(job_id: int, title: str) -> dict`; `Database.set_media(job_id: int, duration: float, poster_path: str | None) -> None`

`migrate()` runs `CREATE TABLE IF NOT EXISTS`, so new columns never reach an
existing database. The migration must be an explicit idempotent `ALTER`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_db.py -k "columns or rename or set_media or idempotent" -v`
Expected: FAIL — `no such column: title`, `AttributeError: rename_job`.

- [ ] **Step 3: Add the columns to SCHEMA**

In `src/streetclip/db.py`, inside the `jobs` table definition, after `report_json TEXT,`:

```sql
    title         TEXT,
    duration      REAL NOT NULL DEFAULT 0.0,
    poster_path   TEXT,
```

- [ ] **Step 4: Make `migrate()` add columns to existing databases**

Replace `migrate` in `src/streetclip/db.py`:

```python
    # Columns added after the first release. CREATE TABLE IF NOT EXISTS will not
    # add them to a database that already exists, so they are applied by ALTER.
    ADDED_COLUMNS = (
        ("title", "TEXT"),
        ("duration", "REAL NOT NULL DEFAULT 0.0"),
        ("poster_path", "TEXT"),
    )

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
            for name, decl in self.ADDED_COLUMNS:
                if name not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")
```

- [ ] **Step 5: Add the write methods**

In `src/streetclip/db.py`, after `fail_job`:

```python
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
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/streetclip/db.py tests/test_db.py
git commit -m "feat(db): add title, duration, and poster columns to jobs"
```

---

### Task 2: Workspace list with clip counts

**Files:**
- Modify: `src/streetclip/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: Task 1 columns
- Produces: `Database.list_workspaces(limit: int = 100) -> list[dict]` — each row is the `jobs` row plus `clip_count`, `kept_count`, `rendered_count` (all `int`)

- [ ] **Step 1: Write the failing test**

```python
def test_list_workspaces_counts_clips_by_state(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    db.replace_clips(job_id, [_candidate(0, 30), _candidate(60, 90), _candidate(120, 150)])
    clips = db.list_clips(job_id)
    db.update_clip(clips[0]["id"], None, None, True)
    db.update_clip(clips[1]["id"], None, None, True)
    db.set_rendered_path(clips[0]["id"], Path("/out/a.mp4"))

    rows = db.list_workspaces()
    assert len(rows) == 1
    assert rows[0]["clip_count"] == 3
    assert rows[0]["kept_count"] == 2
    assert rows[0]["rendered_count"] == 1


def test_list_workspaces_excludes_render_jobs(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    db.create_job(JobKind.RENDER, Path("1"), "a.mp4")
    assert [r["kind"] for r in db.list_workspaces()] == ["analyze"]


def test_list_workspaces_counts_zero_for_a_fresh_job(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    assert db.list_workspaces()[0]["clip_count"] == 0
```

`_candidate` already exists in `tests/test_db.py`. If it does not, add:

```python
def _candidate(start, end, score=8.0):
    return Candidate(
        start=start, end=end, score=score, category=Category.ONE_LINER,
        hook_title="t", reason="r", excerpt="e",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_db.py -k list_workspaces -v`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'list_workspaces'`.

- [ ] **Step 3: Implement the aggregate**

In `src/streetclip/db.py`, after `list_jobs`:

```python
    def list_workspaces(self, limit: int = 100) -> list[dict[str, Any]]:
        """Analyze jobs with their clip tallies, newest first.

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
                " WHERE j.kind = ?"
                " GROUP BY j.id ORDER BY j.id DESC LIMIT ?",
                (JobKind.ANALYZE.value, limit),
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/streetclip/db.py tests/test_db.py
git commit -m "feat(db): list workspaces with clip tallies"
```

---

### Task 3: Workspace filesystem lifecycle

**Files:**
- Create: `src/streetclip/workspaces.py`
- Test: `tests/test_workspaces.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `job_dir(data_dir: Path, job_id: int) -> Path`
  - `poster_path(data_dir: Path, job_id: int) -> Path`
  - `purge_intermediates(data_dir: Path, job_id: int) -> int` — returns bytes freed
  - `delete_workspace(data_dir: Path, job_id: int) -> None`
  - `source_is_shared(source: Path, all_source_paths: list[str]) -> bool`

Keeping this out of `db.py` keeps that module about rows. `worker.job_dir`
moves here in Task 5 so there is one definition.

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

from streetclip import workspaces


def test_job_dir_is_zero_padded(tmp_path):
    assert workspaces.job_dir(tmp_path, 7).name == "job_00007"


def test_purge_removes_only_the_intermediate_audio(tmp_path):
    work = workspaces.job_dir(tmp_path, 1) / "work"
    work.mkdir(parents=True)
    (work / "audio.wav").write_bytes(b"x" * 2048)
    (work / "keepme.ass").write_text("subtitle")

    freed = workspaces.purge_intermediates(tmp_path, 1)

    assert freed == 2048
    assert not (work / "audio.wav").exists()
    assert (work / "keepme.ass").exists(), "the renderer reuses this directory"
    assert work.is_dir()


def test_purge_is_safe_when_nothing_is_there(tmp_path):
    assert workspaces.purge_intermediates(tmp_path, 99) == 0


def test_delete_workspace_removes_the_whole_directory(tmp_path):
    shorts = workspaces.job_dir(tmp_path, 1) / "shorts"
    shorts.mkdir(parents=True)
    (shorts / "01.mp4").write_bytes(b"video")

    workspaces.delete_workspace(tmp_path, 1)

    assert not workspaces.job_dir(tmp_path, 1).exists()


def test_delete_workspace_is_safe_when_absent(tmp_path):
    workspaces.delete_workspace(tmp_path, 42)  # must not raise


def test_source_is_shared_detects_another_reference(tmp_path):
    source = tmp_path / "uploads" / "a.mp4"
    assert workspaces.source_is_shared(source, [str(source), "/elsewhere/b.mp4"])
    assert not workspaces.source_is_shared(source, ["/elsewhere/b.mp4"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workspaces.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'streetclip.workspaces'`.

- [ ] **Step 3: Write the module**

Create `src/streetclip/workspaces.py`:

```python
"""Filesystem lifecycle for a workspace's directory.

Separate from `db` so that module stays about rows. A workspace owns one
directory under the data dir; everything that creates, prunes, or destroys it
lives here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Regenerable from the source in seconds, and nothing downstream reads it:
# `signals` runs during analysis only, and rendering works off the video.
INTERMEDIATES = ("audio.wav",)


def job_dir(data_dir: Path, job_id: int) -> Path:
    return data_dir / f"job_{job_id:05d}"


def poster_path(data_dir: Path, job_id: int) -> Path:
    return job_dir(data_dir, job_id) / "poster.jpg"


def purge_intermediates(data_dir: Path, job_id: int) -> int:
    """Delete regenerable working files. Returns bytes freed.

    Only the named files go — the `work/` directory itself stays, because the
    renderer reuses it for crop plans and subtitle files.
    """
    work = job_dir(data_dir, job_id) / "work"
    freed = 0
    for name in INTERMEDIATES:
        target = work / name
        if target.is_file():
            freed += target.stat().st_size
            target.unlink()
    return freed


def delete_workspace(data_dir: Path, job_id: int) -> None:
    shutil.rmtree(job_dir(data_dir, job_id), ignore_errors=True)


def source_is_shared(source: Path, all_source_paths: list[str]) -> bool:
    """True when more than one job points at this file."""
    return sum(1 for p in all_source_paths if p == str(source)) > 1
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_workspaces.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/streetclip/workspaces.py tests/test_workspaces.py
git commit -m "feat: add workspace filesystem lifecycle module"
```

---

### Task 4: Poster frames

**Files:**
- Modify: `src/streetclip/pipeline/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `run_ffmpeg`, `Settings` (already in `ingest.py`)
- Produces: `ingest.poster_frame(source: Path, dest: Path, at: float, settings: Settings | None = None) -> Path`

- [ ] **Step 1: Write the failing tests**

```python
@needs_ffmpeg
def test_poster_frame_writes_a_jpeg(sample_video: Path, tmp_path: Path):
    dest = ingest.poster_frame(sample_video, tmp_path / "poster.jpg", at=1.0)
    assert dest.is_file()
    assert dest.stat().st_size > 0
    assert dest.read_bytes()[:2] == b"\xff\xd8", "not a JPEG"


@needs_ffmpeg
def test_poster_frame_is_downscaled(sample_video: Path, tmp_path: Path):
    """The home list shows thumbnails; full-resolution frames are wasted bytes."""
    dest = ingest.poster_frame(sample_video, tmp_path / "poster.jpg", at=1.0)
    info = json.loads(
        ingest.run_ffmpeg([
            get_settings().ffprobe_bin, "-v", "error", "-print_format", "json",
            "-show_streams", str(dest),
        ])
    )
    assert info["streams"][0]["width"] == 480


@needs_ffmpeg
def test_poster_frame_past_the_end_still_produces_a_file(sample_video: Path, tmp_path: Path):
    """A seek beyond the recording must not leave a zero-byte poster."""
    dest = ingest.poster_frame(sample_video, tmp_path / "poster.jpg", at=9999.0)
    assert dest.is_file() and dest.stat().st_size > 0
```

Add `import json` and `from streetclip.config import get_settings` to
`tests/test_ingest.py` if they are not already imported.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ingest.py -k poster -v`
Expected: FAIL — `AttributeError: module 'streetclip.pipeline.ingest' has no attribute 'poster_frame'`.

- [ ] **Step 3: Implement**

In `src/streetclip/pipeline/ingest.py`, after `extract_audio`:

```python
POSTER_WIDTH = 480


def poster_frame(
    source: Path,
    dest: Path,
    at: float,
    settings: Settings | None = None,
) -> Path:
    """Grab one downscaled frame, for the workspace list.

    Seeking past the end yields no frame at all, so an out-of-range `at`
    falls back to the first frame rather than writing an empty file.
    """
    settings = settings or get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)

    for seek in (max(0.0, at), 0.0):
        run_ffmpeg(
            [
                settings.ffmpeg_bin,
                "-v", "error",
                "-y",
                "-ss", f"{seek:.3f}",
                "-i", str(source),
                "-frames:v", "1",
                "-vf", f"scale={POSTER_WIDTH}:-2",
                str(dest),
            ]
        )
        if dest.is_file() and dest.stat().st_size > 0:
            return dest

    raise FFmpegError(f"could not extract a poster frame from {source}")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/streetclip/pipeline/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): extract downscaled poster frames"
```

---

### Task 5: Worker records media and purges intermediates

**Files:**
- Modify: `src/streetclip/worker.py:90-110` (`job_dir`, `_analyze`)
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `workspaces.job_dir`, `workspaces.poster_path`, `workspaces.purge_intermediates`, `ingest.poster_frame`, `Database.set_media`
- Produces: `Worker.job_dir` delegates to `workspaces.job_dir` (same signature, `(self, job_id: int) -> Path`)

The purge-then-render test is the load-bearing one in this plan. Deleting
`audio.wav` is the only change that could silently break exports.

- [ ] **Step 1: Write the failing tests**

These follow the exact stubbing convention of the existing analyze tests in
`tests/test_worker.py` — without both monkeypatches they would call the real
Groq and Anthropic APIs.

```python
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
```

`_settings()`, `_transcript()`, and `_StubScorer` already exist in
`tests/test_worker.py`. `_settings()` disables captions and speaker tracking.
Note the worker's data dir is `tmp_path / "data"`, matching the existing tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_worker.py -k "purge or duration or after_the_purge" -v`
Expected: FAIL — `audio.wav` still present; `duration` is `0.0`.

- [ ] **Step 3: Delegate `job_dir` and extend `_analyze`**

In `src/streetclip/worker.py`, add to the imports:

```python
from streetclip import workspaces
from streetclip.pipeline import ingest
```

Replace `Worker.job_dir`:

```python
    def job_dir(self, job_id: int) -> Path:
        return workspaces.job_dir(self.data_dir, job_id)
```

Then, in `_analyze`, replace the two closing lines
(`self.db.replace_clips(...)` and `self.db.finish_job(...)`) with:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_worker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/streetclip/worker.py tests/test_worker.py
git commit -m "feat(worker): record media, write a poster, purge intermediates"
```

---

### Task 6: Rename the API to workspaces

**Files:**
- Modify: `src/streetclip/api.py`
- Modify: `web/src/api.js`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Database.list_workspaces`, `Database.rename_job`
- Produces: routes `GET /api/workspaces`, `GET /api/workspaces/{id}`, `PATCH /api/workspaces/{id}`, `GET /api/workspaces/{id}/events`, `GET /api/workspaces/{id}/source`, `POST /api/workspaces/{id}/render`; `workspace_payload(row) -> dict`

This is a rename, not new behavior. Every `/api/jobs` path becomes
`/api/workspaces`; `POST /api/jobs/upload` becomes `POST /api/workspaces/upload`.
`GET /api/inputs` and the `/api/clips/*` routes are unchanged.

- [ ] **Step 1: Update the existing tests to the new paths**

In `tests/test_api.py`, replace every occurrence of `"/api/jobs"` with
`"/api/workspaces"`. Then add:

`tests/test_api.py` has one fixture, `env`, yielding `(client, db, input_dir)`.

```python
def test_list_workspaces_includes_counts_and_title(env):
    client, db, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    db.rename_job(job_id, "Corner session")

    row = client.get("/api/workspaces").json()[0]
    assert row["title"] == "Corner session"
    assert row["clip_count"] == 0
    assert row["has_poster"] is False
    assert "report_json" not in row, "the transcript must never reach the list"


def test_title_falls_back_to_the_filename(env):
    client, db, _ = env
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    assert client.get("/api/workspaces").json()[0]["title"] == "a.mp4"


def test_rename_workspace(env):
    client, db, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    response = client.patch(f"/api/workspaces/{job_id}", json={"title": "Renamed"})
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"


def test_rename_rejects_an_empty_title(env):
    client, db, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    assert client.patch(f"/api/workspaces/{job_id}", json={"title": "  "}).status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: FAIL — 404 on every `/api/workspaces` path.

- [ ] **Step 3: Rename the routes and add the payload helper**

In `src/streetclip/api.py`, change every `@app.get("/api/jobs...")` /
`@app.post` / decorator path from `/api/jobs` to `/api/workspaces`. Then replace
`job_payload` with:

```python
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

    `report_json` is deliberately absent — it carries the whole transcript.
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
        "clip_count": row.get("clip_count", 0),
        "kept_count": row.get("kept_count", 0),
        "rendered_count": row.get("rendered_count", 0),
    }
```

Add `field_validator` to the pydantic import. Point the list route at the new
query and add the rename route:

```python
    @app.get("/api/workspaces")
    def list_workspaces() -> list[dict[str, Any]]:
        return [workspace_payload(w) for w in db.list_workspaces()]

    @app.patch("/api/workspaces/{job_id}")
    def rename_workspace(job_id: int, request: RenameRequest) -> dict[str, Any]:
        if db.get_job(job_id) is None:
            raise HTTPException(404, "no such workspace")
        return workspace_payload(db.rename_job(job_id, request.title))
```

Replace the remaining `job_payload(` call sites with `workspace_payload(`.

- [ ] **Step 4: Update the web client**

In `web/src/api.js`, replace the endpoint constants:

```js
export const listWorkspaces = () => request("/api/workspaces");
export const getWorkspace = (id) => request(`/api/workspaces/${id}`);
export const createWorkspace = (path) => request("/api/workspaces", json({ path }));
export const renameWorkspace = (id, title) =>
  request(`/api/workspaces/${id}`, { ...json({ title }), method: "PATCH" });
export const renderWorkspace = (id) =>
  request(`/api/workspaces/${id}/render`, { method: "POST" });

export function uploadWorkspace(file) {
  const form = new FormData();
  form.append("file", file);
  return request("/api/workspaces/upload", { method: "POST", body: form });
}

export function watchWorkspace(id, onUpdate) {
  const source = new EventSource(`/api/workspaces/${id}/events`);
  source.onmessage = (event) => onUpdate(JSON.parse(event.data));
  source.onerror = () => source.close();
  return () => source.close();
}
```

Update the call sites in `App.jsx` and `SourcePicker.jsx` to the new names, and
change the `<video src>` in `Review.jsx` to `/api/workspaces/${job.id}/source`.

- [ ] **Step 5: Run the tests and build the SPA**

Run: `.venv/bin/python -m pytest -v && npm --prefix web run build`
Expected: PASS; build succeeds.

- [ ] **Step 6: Commit**

```bash
git add src/streetclip/api.py web/src tests/test_api.py
git commit -m "refactor(api): expose analyze jobs as workspaces"
```

---

### Task 7: Delete, transcript, and poster endpoints

**Files:**
- Modify: `src/streetclip/api.py`, `src/streetclip/db.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `workspaces.delete_workspace`, `workspaces.source_is_shared`
- Produces: `Database.delete_job(job_id: int) -> None`; `Database.all_source_paths() -> list[str]`; routes `DELETE /api/workspaces/{id}`, `GET /api/workspaces/{id}/transcript`, `GET /api/workspaces/{id}/poster`

- [ ] **Step 1: Write the failing tests**

`tests/test_api.py` has one fixture, `env`, yielding
`(client, db, input_dir)`. Its data directory is `input_dir.parent / "data"`.
Unpack it the way the existing tests in that file do.

```python
def test_delete_workspace_removes_rows_and_directory(env):
    client, db, input_dir = env
    data_dir = input_dir.parent / "data"
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    job_dir = data_dir / f"job_{job_id:05d}"
    (job_dir / "shorts").mkdir(parents=True)
    (job_dir / "shorts" / "01.mp4").write_bytes(b"video")

    assert client.delete(f"/api/workspaces/{job_id}").status_code == 204
    assert db.get_job(job_id) is None
    assert not job_dir.exists()


def test_delete_workspace_removes_its_upload(env):
    client, db, input_dir = env
    upload = input_dir.parent / "data" / "uploads" / "a.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video")
    job_id = db.create_job(JobKind.ANALYZE, upload, "a.mp4")

    client.delete(f"/api/workspaces/{job_id}")
    assert not upload.exists()


def test_delete_workspace_keeps_an_upload_another_job_uses(env):
    client, db, input_dir = env
    upload = input_dir.parent / "data" / "uploads" / "a.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video")
    first = db.create_job(JobKind.ANALYZE, upload, "a.mp4")
    db.create_job(JobKind.ANALYZE, upload, "a.mp4")

    client.delete(f"/api/workspaces/{first}")
    assert upload.exists(), "the second workspace still needs it"


def test_transcript_returns_words_without_the_report(env, sample_video):
    client, db, _ = env
    job_id = _seed_done_job(db, sample_video)

    body = client.get(f"/api/workspaces/{job_id}/transcript").json()
    assert body["words"][0]["text"]
    assert "start" in body["words"][0]
    assert "candidates" not in body and "media" not in body


def test_transcript_404s_before_analysis_finishes(env):
    client, db, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    assert client.get(f"/api/workspaces/{job_id}/transcript").status_code == 404


def test_poster_404s_when_absent(env):
    client, db, _ = env
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    assert client.get(f"/api/workspaces/{job_id}/poster").status_code == 404
```

`_seed_done_job(db, source)` already exists in `tests/test_api.py` and stores a
finished `Report`. Confirm the `Report` it builds carries at least one
`Word`; if its transcript is empty, add a word to it so the transcript
assertions have something to read.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_api.py -k "delete or transcript or poster" -v`
Expected: FAIL — 405 on DELETE, 404 on transcript.

- [ ] **Step 3: Add the database methods**

In `src/streetclip/db.py`:

```python
    def delete_job(self, job_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def all_source_paths(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT source_path FROM jobs").fetchall()
        return [r["source_path"] for r in rows]
```

Clips cascade via the existing `ON DELETE CASCADE` and the `PRAGMA foreign_keys`
already set in `connect()`.

- [ ] **Step 4: Add the routes**

In `src/streetclip/api.py`:

```python
    @app.delete("/api/workspaces/{job_id}", status_code=204)
    def delete_workspace(job_id: int) -> Response:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "no such workspace")

        source = Path(job["source_path"])
        shared = workspaces_fs.source_is_shared(source, db.all_source_paths())

        db.delete_job(job_id)
        workspaces_fs.delete_workspace(data_dir, job_id)

        # Only uploads are ours to remove; files in the input directory are the
        # operator's originals.
        if not shared and _within(source, data_dir / "uploads") and source.is_file():
            source.unlink()
        return Response(status_code=204)

    @app.get("/api/workspaces/{job_id}/transcript")
    def workspace_transcript(job_id: int) -> dict[str, Any]:
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
    def workspace_poster(job_id: int) -> FileResponse:
        job = db.get_job(job_id)
        if job is None or not job["poster_path"]:
            raise HTTPException(404, "no poster")
        path = Path(job["poster_path"])
        if not path.is_file():
            raise HTTPException(404, "poster file is gone")
        return FileResponse(path, media_type="image/jpeg")
```

Add to the imports: `from streetclip import workspaces as workspaces_fs` and
`Response` from `fastapi`.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/streetclip/api.py src/streetclip/db.py tests/test_api.py
git commit -m "feat(api): delete workspaces, serve transcripts and posters"
```

---

### Task 8: Home screen

**Files:**
- Create: `web/src/WorkspaceList.jsx`
- Modify: `web/src/App.jsx`, `web/src/styles.css`, `web/src/api.js`

**Interfaces:**
- Consumes: `listWorkspaces`, `renameWorkspace`, `deleteWorkspace`, `getWorkspace`
- Produces: `<WorkspaceList onOpen={(id) => void} onNew={() => void} />`

Add to `web/src/api.js`:

```js
export const deleteWorkspace = (id) =>
  fetch(`/api/workspaces/${id}`, { method: "DELETE" }).then((r) => {
    if (!r.ok) throw new Error("could not delete");
  });
```

- [ ] **Step 1: Write the component**

Create `web/src/WorkspaceList.jsx`:

```jsx
import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { clock } from "./format";

const STATUS_LABEL = {
  queued: "queued",
  running: "analyzing",
  failed: "failed",
};

export default function WorkspaceList({ onOpen, onNew, onError }) {
  const [rows, setRows] = useState(null);
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");

  const refresh = useCallback(() => {
    api.listWorkspaces().then(setRows).catch(onError);
  }, [onError]);

  useEffect(refresh, [refresh]);

  // Anything still analyzing needs its progress refreshed without an SSE
  // connection per row.
  useEffect(() => {
    if (!rows?.some((w) => w.status === "running" || w.status === "queued")) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [rows, refresh]);

  function commitRename(id) {
    const title = draft.trim();
    setEditing(null);
    if (!title) return;
    api.renameWorkspace(id, title).then(refresh).catch(onError);
  }

  function remove(workspace) {
    const label = workspace.title;
    if (!window.confirm(`Delete "${label}" and its exported shorts?`)) return;
    api.deleteWorkspace(workspace.id).then(refresh).catch(onError);
  }

  return (
    <div className="home">
      <div className="home-head">
        <h1>Workspaces</h1>
        <button className="btn primary" onClick={onNew}>
          + New recording
        </button>
      </div>

      {rows && rows.length === 0 && (
        <p className="empty">Nothing yet. Add a recording to get started.</p>
      )}

      <div className="workspace-grid">
        {(rows || []).map((w) => (
          <div key={w.id} className="workspace-card">
            <button className="poster" onClick={() => onOpen(w.id)}>
              {w.has_poster ? (
                <img src={`/api/workspaces/${w.id}/poster`} alt="" />
              ) : (
                <span className="poster-fallback" />
              )}
            </button>

            <div className="workspace-body">
              {editing === w.id ? (
                <input
                  className="rename"
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={() => commitRename(w.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename(w.id);
                    if (e.key === "Escape") setEditing(null);
                  }}
                />
              ) : (
                <button
                  className="workspace-title"
                  onClick={() => onOpen(w.id)}
                  onDoubleClick={() => {
                    setDraft(w.title);
                    setEditing(w.id);
                  }}
                >
                  {w.title}
                </button>
              )}

              <div className="workspace-meta">
                {w.status === "done" ? (
                  <>
                    <span>{clock(w.duration)}</span>
                    <span>{w.clip_count} clips</span>
                    <span>{w.kept_count} kept</span>
                    {w.rendered_count > 0 && <span>{w.rendered_count} exported</span>}
                  </>
                ) : (
                  <span className={w.status === "failed" ? "bad" : "working"}>
                    {STATUS_LABEL[w.status] || w.status}
                    {w.status === "running" && ` ${Math.round(w.progress * 100)}%`}
                  </span>
                )}
              </div>

              <div className="workspace-actions">
                <button className="btn ghost" onClick={() => { setDraft(w.title); setEditing(w.id); }}>
                  Rename
                </button>
                <button className="btn ghost" onClick={() => remove(w)}>
                  Delete
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add the styles**

Append to `web/src/styles.css`:

```css
.home {
  width: min(1100px, 100%);
  margin: 0 auto;
  padding: 40px 28px;
}

.home-head {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-bottom: 32px;
}

.home-head h1 {
  font-family: var(--display);
  font-weight: 800;
  font-size: clamp(26px, 4vw, 38px);
  letter-spacing: -0.02em;
  margin: 0;
  margin-right: auto;
}

.workspace-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 20px;
}

.workspace-card {
  border: 1px solid var(--edge);
  border-radius: 4px;
  overflow: hidden;
  background: var(--slab);
}

.workspace-card .poster {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
  padding: 0;
}

.workspace-card .poster img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.poster-fallback {
  display: block;
  width: 100%;
  height: 100%;
  background: linear-gradient(135deg, #1d2733, #2c3644);
}

.workspace-body {
  padding: 12px 14px 14px;
}

.workspace-title {
  font-weight: 600;
  font-size: 16px;
  text-align: left;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-meta {
  display: flex;
  gap: 10px;
  margin-top: 4px;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--mute);
}

.workspace-meta .working {
  color: var(--sodium);
}

.workspace-meta .bad {
  color: var(--alarm);
}

.workspace-actions {
  display: flex;
  gap: 4px;
  margin-top: 10px;
}

.workspace-actions .btn {
  padding: 4px 8px;
  font-size: 12px;
}

.rename {
  width: 100%;
  background: var(--dusk);
  border: 1px solid var(--sodium);
  border-radius: 3px;
  padding: 4px 6px;
  font-weight: 600;
}
```

- [ ] **Step 3: Route between home and workspace in `App.jsx`**

Replace the body-selection block in `web/src/App.jsx` so the default view is the
list, `picking` opens the source picker, and a selected id opens the workspace:

```jsx
  const [picking, setPicking] = useState(false);

  let body;
  if (jobId === null && picking) {
    body = (
      <SourcePicker
        onStarted={(created) => {
          setPicking(false);
          setJobId(created.id);
        }}
        onError={fail}
      />
    );
  } else if (jobId === null) {
    body = (
      <WorkspaceList
        onOpen={setJobId}
        onNew={() => setPicking(true)}
        onError={fail}
      />
    );
  } else if (job === null) {
    body = <div className="progress"><p className="stage">loading…</p></div>;
  } else if (job.status !== "done") {
    body = <Progress job={job} onBack={back} />;
  } else {
    body = <Review job={job} onPatchClip={patchClip} onExport={exportKept}
                   exporting={renderId !== null} onBack={back} />;
  }
```

Update `back()` to also clear `picking`, and remove the "Recent" block from
`SourcePicker.jsx` — the home screen replaces it.

- [ ] **Step 4: Build and check in a browser**

Run: `npm --prefix web run build && docker compose up -d --build`
Open `http://localhost:8080`. Confirm: the list renders, posters appear, rename
persists after reload, delete asks first and removes the card.

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat(web): workspace home screen with rename and delete"
```

---

### Task 9: Transcript panel

**Files:**
- Create: `web/src/Transcript.jsx`
- Modify: `web/src/Review.jsx`, `web/src/styles.css`, `web/src/api.js`

**Interfaces:**
- Consumes: `GET /api/workspaces/{id}/transcript`
- Produces: `<Transcript words={...} clip={...} position={...} onSeek={(t) => void} onSetBounds={({start, end}) => void} />`

Add to `web/src/api.js`:

```js
export const getTranscript = (id) => request(`/api/workspaces/${id}/transcript`);
```

**The panel renders a window, not the session.** A 65-minute recording is
~11,000 words; mounting that many spans is slow and the operator never scrolls
them.

- [ ] **Step 1: Write the component**

Create `web/src/Transcript.jsx`:

```jsx
import { useMemo, useRef, useState } from "react";

// Seconds of context shown either side of the clip.
const PAD = 30;

export default function Transcript({ words, clip, position, onSeek, onSetBounds }) {
  const [drag, setDrag] = useState(null);
  const container = useRef(null);

  // Only the clip's neighbourhood is mounted; the full transcript would be
  // thousands of spans the operator never scrolls to.
  const visible = useMemo(
    () => words.filter((w) => w.end > clip.start - PAD && w.start < clip.end + PAD),
    [words, clip.start, clip.end],
  );

  const range = drag
    ? { start: Math.min(drag.from, drag.to), end: Math.max(drag.from, drag.to) }
    : { start: clip.start, end: clip.end };

  function finishDrag() {
    if (!drag) return;
    const start = Math.min(drag.from, drag.to);
    const end = Math.max(drag.from, drag.to);
    setDrag(null);
    // A click is a drag of zero length — treat it as a seek, not a 0s clip.
    if (end - start < 0.05) onSeek(start);
    else onSetBounds({ start, end });
  }

  return (
    <div
      className="transcript"
      ref={container}
      onPointerUp={finishDrag}
      onPointerLeave={finishDrag}
    >
      {visible.map((word, i) => {
        const inClip = word.start >= range.start && word.end <= range.end;
        const isNow = position >= word.start && position < word.end;
        return (
          <span
            key={`${word.start}-${i}`}
            className={[
              "word",
              inClip ? "in-clip" : "",
              isNow ? "now" : "",
            ].join(" ")}
            onPointerDown={(event) => {
              event.preventDefault();
              setDrag({ from: word.start, to: word.end });
            }}
            onPointerEnter={() => {
              if (drag) setDrag((d) => ({ ...d, to: word.end }));
            }}
          >
            {word.text}{" "}
          </span>
        );
      })}
      <p className="transcript-hint">click a word to seek · drag across words to set bounds</p>
    </div>
  );
}
```

- [ ] **Step 2: Add the styles**

Append to `web/src/styles.css`:

```css
.transcript {
  font-size: 19px;
  line-height: 1.7;
  max-width: 46ch;
  max-height: 320px;
  overflow-y: auto;
  margin-bottom: 24px;
  user-select: none;
  cursor: text;
}

.transcript .word {
  color: var(--mute);
  border-radius: 2px;
}

.transcript .word.in-clip {
  color: var(--bone);
  background: rgba(242, 160, 61, 0.1);
}

.transcript .word.now {
  color: var(--sodium);
  font-weight: 600;
}

.transcript-hint {
  position: sticky;
  bottom: 0;
  margin: 12px 0 0;
  padding-top: 8px;
  background: var(--dusk);
  font-family: var(--mono);
  font-size: 11px;
  color: var(--mute);
}
```

- [ ] **Step 3: Host it in `Review.jsx`**

Load the transcript once per workspace and swap it in for the static excerpt:

```jsx
  const [words, setWords] = useState([]);

  useEffect(() => {
    api.getTranscript(job.id).then((t) => setWords(t.words)).catch(() => setWords([]));
  }, [job.id]);
```

Replace `<p className="excerpt">{current.excerpt}</p>` with:

```jsx
              {words.length > 0 ? (
                <Transcript
                  words={words}
                  clip={current}
                  position={position}
                  onSeek={(t) => {
                    if (video.current) video.current.currentTime = t;
                  }}
                  onSetBounds={({ start, end }) =>
                    onPatchClip(current.id, { start, end })
                  }
                />
              ) : (
                <p className="excerpt">{current.excerpt}</p>
              )}
```

Import `Transcript` and `* as api` at the top of `Review.jsx`.

- [ ] **Step 4: Build and check in a browser**

Run: `npm --prefix web run build && docker compose up -d --build`
Confirm: words highlight during playback; clicking seeks; dragging across words
changes the in/out stamps and survives a reload.

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat(web): transcript panel with click-to-seek and drag-to-trim"
```

---

### Task 10: End-to-end verification

**Files:** none — verification only.

- [ ] **Step 1: Full suite and lint**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 2: Fresh-database migration check**

Run:

```bash
docker compose down
docker compose up -d --build
```

Against the **existing** `data/streetclip.db`, confirm the app starts and the
home screen lists prior workspaces with their titles. This exercises the
`ALTER TABLE` path from Task 1, which a fresh test database never does.

- [ ] **Step 3: Walk the loop in a browser**

Add a recording, watch it analyze from the home screen, open it, drag a clip's
bounds in the transcript, keep two clips, export, download one, return home,
rename the workspace, reload, then delete it and confirm the directory is gone:

```bash
ls data/
```

- [ ] **Step 4: Confirm the purge actually freed space**

```bash
du -sh data/job_*/
```

Expected: no `work/audio.wav` in any completed workspace.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: address issues found in end-to-end verification"
```

---

## Self-Review

**Spec coverage:** data model → Task 1; counts → Task 2; filesystem lifecycle →
Task 3; poster → Tasks 4-5; purge + purge-then-render → Task 5; API rename →
Task 6; delete/transcript/poster routes → Task 7; home screen → Task 8;
transcript panel → Task 9; migration of the existing database → Task 10 Step 2.
No spec section is unimplemented.

**Type consistency:** `workspaces.job_dir(data_dir, job_id)` is used with that
signature in Tasks 3, 5, and 7. `workspace_payload` is defined in Task 6 and
reused in Task 7. `onSetBounds({start, end})` matches the `PATCH` body accepted
by the existing `ClipUpdate` model.

**Known risk:** Task 6 renames every API route in one commit. If it goes wrong
the SPA is fully broken until fixed — it is deliberately placed after the
database work so the rename lands on a green suite.
