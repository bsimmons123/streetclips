"""Render one finished short: trim, crop to 9:16, burn captions, encode.

All of it happens in a single ffmpeg pass. Chaining separate crop and burn-in
passes would re-encode the video twice and visibly soften it.
"""

from __future__ import annotations

from pathlib import Path

from streetclip.config import Settings, get_settings
from streetclip.models import Candidate, Transcript
from streetclip.pipeline.ingest import run_ffmpeg
from streetclip.pipeline.render import captions
from streetclip.pipeline.render.reframe import CropPlan


def escape_filter_path(path: Path) -> str:
    r"""Quote a path for use inside an ffmpeg filter argument.

    Filter graphs are parsed before the OS sees the path, so `:` (Windows
    drives, and any colon in a directory name) and `\` need escaping or the
    graph fails to parse — usually with an error that blames the wrong filter.
    """
    text = str(path)
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    return text


def build_filter(
    plan: CropPlan,
    subtitle_path: Path | None,
    settings: Settings | None = None,
) -> str:
    """crop -> scale to output size -> burn captions."""
    settings = settings or get_settings()

    stages = [
        f"crop=w={plan.crop_width}:h={plan.crop_height}:x='{plan.x_expression}':y=0",
        f"scale={settings.output_width}:{settings.output_height}:flags=lanczos",
        # An odd dimension anywhere in the chain makes libx264 refuse the frame.
        "setsar=1",
    ]
    if subtitle_path is not None:
        stages.append(f"subtitles='{escape_filter_path(subtitle_path)}'")

    return ",".join(stages)


def render_clip(
    source: Path,
    dest: Path,
    start: float,
    end: float,
    plan: CropPlan,
    subtitle_path: Path | None = None,
    settings: Settings | None = None,
) -> Path:
    settings = settings or get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)

    run_ffmpeg(
        [
            settings.ffmpeg_bin,
            "-v", "error",
            "-y",
            # -ss before -i seeks fast; the re-encode below means we still land
            # on the exact requested frame rather than the nearest keyframe.
            "-ss", f"{start:.3f}",
            "-i", str(source),
            "-t", f"{max(0.0, end - start):.3f}",
            "-vf", build_filter(plan, subtitle_path, settings),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "160k",
            "-movflags", "+faststart",
            str(dest),
        ]
    )
    return dest


def render_candidate(
    source: Path,
    dest: Path,
    candidate: Candidate,
    transcript: Transcript,
    plan: CropPlan,
    work_dir: Path,
    burn_captions: bool = True,
    settings: Settings | None = None,
) -> Path:
    """Render one reviewed candidate into an upload-ready short."""
    settings = settings or get_settings()

    subtitle_path = None
    if burn_captions:
        subtitle_path = captions.write(
            transcript,
            work_dir / f"{dest.stem}.ass",
            start=candidate.start,
            end=candidate.end,
            settings=settings,
        )

    return render_clip(
        source,
        dest,
        candidate.start,
        candidate.end,
        plan,
        subtitle_path,
        settings,
    )
