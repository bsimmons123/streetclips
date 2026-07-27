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
