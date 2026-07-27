"""Turn a transcript into LLM-sized chunks of timestamped, energy-annotated text.

Chunks overlap so a moment straddling a seam is seen whole by at least one
request; `score` deduplicates the resulting double-proposals afterwards.
"""

from __future__ import annotations

from streetclip.config import Settings, get_settings
from streetclip.models import Chunk, EnergySeries, Segment, Transcript

# Segments this much above the recording's own baseline get flagged to the model
# as delivered with force.
RAISED_VOICE_THRESHOLD = 0.72


def _segments_or_synthesized(transcript: Transcript) -> list[Segment]:
    """Fall back to grouping words when a backend returns no segments."""
    if transcript.segments:
        return transcript.segments

    segments: list[Segment] = []
    group: list = []
    for word in transcript.words:
        group.append(word)
        if len(group) >= 20:
            segments.append(
                Segment(
                    text=" ".join(w.text for w in group),
                    start=group[0].start,
                    end=group[-1].end,
                )
            )
            group = []
    if group:
        segments.append(
            Segment(
                text=" ".join(w.text for w in group),
                start=group[0].start,
                end=group[-1].end,
            )
        )
    return segments


def annotate(segment: Segment, energy: EnergySeries | None) -> str:
    """One transcript line: an absolute start time, optional delivery marker, text.

    Times are absolute seconds rather than clock strings so the model returns
    numbers on the source timeline with no conversion step to get wrong.
    """
    marker = ""
    if energy is not None and energy.values:
        if energy.mean_in_range(segment.start, segment.end) >= RAISED_VOICE_THRESHOLD:
            marker = " <raised voice>"
    return f"[{segment.start:.1f}]{marker} {segment.text}"


def build_chunks(
    transcript: Transcript,
    energy: EnergySeries | None = None,
    settings: Settings | None = None,
) -> list[Chunk]:
    settings = settings or get_settings()
    size = settings.chunk_seconds
    overlap = min(settings.chunk_overlap_seconds, size / 2)
    stride = size - overlap

    segments = _segments_or_synthesized(transcript)
    if not segments:
        return []

    duration = max(transcript.duration, segments[-1].end)
    chunks: list[Chunk] = []
    index = 0
    start = 0.0

    while start < duration:
        end = min(start + size, duration)
        # Assign each segment to a chunk by its start, so a line is never split
        # across two requests.
        lines = [annotate(s, energy) for s in segments if start <= s.start < end]
        if lines:
            chunks.append(Chunk(index=index, start=start, end=end, text="\n".join(lines)))
            index += 1
        if end >= duration:
            break
        start += stride

    return chunks
