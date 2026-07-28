"""Phase 1 pipeline: source video in, ranked candidate moments out.

Separated from the CLI so the web worker can drive the same code path without
going through argument parsing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from streetclip.config import Settings, get_settings
from streetclip.models import Candidate, Report, Transcript
from streetclip.pipeline import candidates, ingest, score, signals
from streetclip.pipeline.transcribe.base import Transcriber, get_transcriber

# stage name, fraction complete
ProgressFn = Callable[[str, float], None]


def _noop(stage: str, fraction: float) -> None:
    pass


def analyze(
    source: Path,
    work_dir: Path,
    settings: Settings | None = None,
    transcriber: Transcriber | None = None,
    scorer: score.Scorer | None = None,
    progress: ProgressFn = _noop,
) -> Report:
    settings = settings or get_settings()
    work_dir.mkdir(parents=True, exist_ok=True)

    progress("probing", 0.0)
    media = ingest.probe(source, settings)

    progress("extracting audio", 0.05)
    audio = ingest.extract_audio(source, work_dir / "audio.wav", settings)

    progress("transcribing", 0.15)
    transcriber = transcriber or get_transcriber(settings)
    transcript: Transcript = transcriber.transcribe(audio)
    if not transcript.duration:
        transcript.duration = media.duration

    progress("measuring delivery", 0.55)
    energy = signals.compute_energy(audio)

    progress("scoring", 0.60)
    chunks = candidates.build_chunks(transcript, energy, settings)
    scorer = scorer or score.Scorer(settings)

    proposals = []
    for i, chunk in enumerate(chunks):
        proposals.extend(scorer.propose(chunk))
        progress("scoring", 0.60 + 0.35 * (i + 1) / max(1, len(chunks)))

    found: list[Candidate] = score.to_candidates(proposals, transcript, settings)
    progress("done", 1.0)

    return Report(media=media, transcript=transcript, candidates=found)


def write_report(report: Report, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(report.model_dump_json(indent=2))
    return dest


def cut_candidates(
    report: Report,
    out_dir: Path,
    limit: int | None = None,
    settings: Settings | None = None,
) -> list[Path]:
    """Write raw 16:9 trims of the top candidates for eyeball review."""
    settings = settings or get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    source = Path(report.media.path)

    written: list[Path] = []
    for i, candidate in enumerate(report.candidates[:limit], start=1):
        name = f"{i:02d}_{candidate.category.value}_{_slug(candidate.hook_title)}.mp4"
        written.append(
            ingest.cut(source, out_dir / name, candidate.start, candidate.end, settings)
        )
    return written


def render_shorts(
    report: Report,
    out_dir: Path,
    work_dir: Path,
    limit: int | None = None,
    burn_captions: bool = True,
    track_speaker: bool = True,
    settings: Settings | None = None,
    progress: ProgressFn = _noop,
) -> list[Path]:
    """Turn reviewed candidates into upload-ready 9:16 shorts.

    Speaker tracking is per clip: each one gets its own crop path, because the
    preacher moves between moments and a path derived from the whole recording
    would be wrong for most of them.
    """
    from streetclip.pipeline.render import encode, reframe

    settings = settings or get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    source = Path(report.media.path)

    detector = None
    if track_speaker:
        try:
            detector = reframe.get_detector(settings)
        except (ImportError, RuntimeError):
            # Rendering without tracking is a worse clip, not a failed run.
            track_speaker = False

    selected = report.candidates[:limit]
    written: list[Path] = []

    for i, candidate in enumerate(selected, start=1):
        progress(f"rendering {i}/{len(selected)}", (i - 1) / max(1, len(selected)))

        if track_speaker and detector is not None:
            plan = reframe.plan_for_clip(
                source,
                candidate.start,
                candidate.end,
                report.media.width,
                report.media.height,
                detector=detector,
                settings=settings,
            )
        else:
            plan = reframe.plan([], report.media.width, report.media.height, settings)

        name = f"{i:02d}_{candidate.category.value}_{_slug(candidate.hook_title)}.mp4"
        written.append(
            encode.render_candidate(
                source,
                out_dir / name,
                candidate,
                report.transcript,
                plan,
                work_dir,
                burn_captions=burn_captions,
                settings=settings,
            )
        )

    progress("done", 1.0)
    return written


def _slug(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in text.lower())
    return "-".join(cleaned.split())[:50] or "clip"


def summarize(report: Report) -> str:
    """A human-readable digest for the terminal."""
    lines = [
        f"{Path(report.media.path).name} — {report.media.duration / 60:.1f} min, "
        f"{len(report.transcript.words)} words",
        f"{len(report.candidates)} candidates:",
        "",
    ]
    for i, c in enumerate(report.candidates, start=1):
        lines.append(
            f"{i:2d}. [{c.score:4.1f}] {_timestamp(c.start)}-{_timestamp(c.end)} "
            f"({c.duration:.0f}s, {c.category.value})  {c.hook_title}"
        )
        lines.append(f"     {c.reason}")
    return "\n".join(lines)


def _timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def load_report(path: Path) -> Report:
    return Report.model_validate(json.loads(path.read_text()))
