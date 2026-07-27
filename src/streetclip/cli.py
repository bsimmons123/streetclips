"""Phase 1 command line entry point.

    streetclip analyze session.mp4 --cut 5

Runs ingest -> transcribe -> signals -> score, writes a JSON report, and
optionally trims raw 16:9 cuts of the top candidates for review.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from streetclip.config import TranscribeBackend, get_settings
from streetclip.pipeline import ingest, run


def _progress(stage: str, fraction: float) -> None:
    bar = "#" * int(fraction * 30)
    print(f"\r  {stage:<20} [{bar:<30}] {fraction * 100:3.0f}%", end="", file=sys.stderr)
    if fraction >= 1.0:
        print(file=sys.stderr)


def _analyze(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.backend:
        settings = settings.model_copy(update={"transcribe_backend": args.backend})

    source = Path(args.video).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()

    report = run.analyze(
        source,
        work_dir=out_dir / "work",
        settings=settings,
        progress=_progress if not args.quiet else run._noop,
    )

    report_path = run.write_report(report, out_dir / "report.json")
    print(run.summarize(report))
    print(f"\nreport: {report_path}")

    if args.cut:
        written = run.cut_candidates(report, out_dir / "clips", limit=args.cut, settings=settings)
        print(f"clips:  {len(written)} written to {out_dir / 'clips'}")

    return 0 if report.candidates else 1


def _cut(args: argparse.Namespace) -> int:
    """Re-cut from an existing report without re-running transcription."""
    report = run.load_report(Path(args.report).expanduser().resolve())
    out_dir = Path(args.out).expanduser().resolve()
    written = run.cut_candidates(report, out_dir, limit=args.limit)
    print(f"{len(written)} clips written to {out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="streetclip",
        description="Find the moments worth posting in a long preaching recording.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="analyze a video and rank candidate clips")
    analyze.add_argument("video", help="path to the source recording")
    analyze.add_argument("-o", "--out", default="out", help="output directory (default: out)")
    analyze.add_argument(
        "-c", "--cut", type=int, metavar="N", help="also trim the top N candidates"
    )
    analyze.add_argument(
        "-b",
        "--backend",
        choices=[b.value for b in TranscribeBackend if b != TranscribeBackend.FAKE],
        help="transcription backend (default: from config)",
    )
    analyze.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")
    analyze.set_defaults(func=_analyze)

    cut = sub.add_parser("cut", help="trim clips from an existing report.json")
    cut.add_argument("report", help="path to report.json")
    cut.add_argument("-o", "--out", default="clips", help="output directory")
    cut.add_argument("-n", "--limit", type=int, help="only cut the top N candidates")
    cut.set_defaults(func=_cut)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: file not found: {exc}", file=sys.stderr)
        return 2
    except ingest.FFmpegError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
