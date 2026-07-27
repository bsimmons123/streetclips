"""Shared fixtures.

The sample video is generated with ffmpeg's lavfi sources rather than committed
as a binary, so the repo stays text-only and the fixture is reproducible.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from streetclip.config import get_settings
from streetclip.models import Segment, Transcript, Word

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def _has_libass() -> bool:
    """Burning captions needs the `subtitles` filter, which needs libass.

    Several ffmpeg builds ship without it — Homebrew's slim `ffmpeg` formula is
    one — and the failure mode is a confusing filtergraph parse error rather
    than a clear "missing feature", so check up front.
    """
    binary = get_settings().ffmpeg_bin
    if shutil.which(binary) is None:
        return False
    try:
        result = subprocess.run(
            [binary, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return " subtitles " in result.stdout


needs_libass = pytest.mark.skipif(
    not _has_libass(),
    reason="ffmpeg built without libass (no `subtitles` filter); "
    "set STREETCLIP_FFMPEG_BIN to a build that has it",
)


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 10s 640x360 25fps clip whose audio loudness rises and falls.

    The amplitude modulation gives `signals` a real envelope to find instead of
    a flat tone that would make any energy assertion vacuous.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    dest = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=10",
            "-f", "lavfi",
            "-i", "aevalsrc=0.4*sin(440*2*PI*t)*(0.55+0.45*sin(0.2*2*PI*t)):d=10:s=44100",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    return dest


@pytest.fixture
def transcript() -> Transcript:
    """A small canned transcript: two sentences, word-level timings."""
    spec = [
        ("Repent", 0.0, 0.6),
        ("and", 0.6, 0.8),
        ("believe", 0.8, 1.4),
        ("the", 1.4, 1.6),
        ("gospel", 1.6, 2.2),
        ("Who", 3.0, 3.3),
        ("told", 3.3, 3.6),
        ("you", 3.6, 3.9),
        ("that", 3.9, 4.2),
        ("nonsense", 4.2, 5.0),
    ]
    words = [Word(text=t, start=s, end=e) for t, s, e in spec]
    return Transcript(
        duration=10.0,
        words=words,
        segments=[
            Segment(text="Repent and believe the gospel", start=0.0, end=2.2),
            Segment(text="Who told you that nonsense", start=3.0, end=5.0),
        ],
    )
