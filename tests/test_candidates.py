from __future__ import annotations

from streetclip.config import Settings
from streetclip.models import EnergySeries, Segment, Transcript, Word
from streetclip.pipeline import candidates


def _transcript(n_segments: int, seconds_each: float = 10.0) -> Transcript:
    segments = [
        Segment(text=f"line {i}", start=i * seconds_each, end=(i + 1) * seconds_each)
        for i in range(n_segments)
    ]
    return Transcript(duration=n_segments * seconds_each, segments=segments)


def test_annotate_marks_only_loud_segments():
    energy = EnergySeries(values=[0.1] * 5 + [0.9] * 5)
    quiet = Segment(text="quietly spoken", start=0.0, end=5.0)
    loud = Segment(text="SHOUTED", start=5.0, end=10.0)

    assert candidates.annotate(quiet, energy) == "[0.0] quietly spoken"
    assert candidates.annotate(loud, energy) == "[5.0] <raised voice> SHOUTED"


def test_annotate_without_energy_omits_marker():
    segment = Segment(text="hello", start=12.25, end=13.0)
    assert candidates.annotate(segment, None) == "[12.2] hello"


def test_chunks_cover_whole_transcript_with_overlap():
    settings = Settings(chunk_seconds=100.0, chunk_overlap_seconds=20.0)
    chunks = candidates.build_chunks(_transcript(30), settings=settings)

    assert len(chunks) > 1
    assert chunks[0].start == 0.0
    # Stride is size - overlap, so chunk 1 starts 80s in and re-covers 80-100.
    assert chunks[1].start == 80.0
    assert chunks[-1].end == 300.0


def test_every_segment_appears_in_some_chunk():
    settings = Settings(chunk_seconds=100.0, chunk_overlap_seconds=20.0)
    transcript = _transcript(30)
    chunks = candidates.build_chunks(transcript, settings=settings)

    covered = "\n".join(c.text for c in chunks)
    for segment in transcript.segments:
        assert segment.text in covered, f"{segment.text} was dropped"


def test_overlap_is_capped_at_half_the_chunk():
    # An overlap larger than the chunk would make stride zero and loop forever.
    settings = Settings(chunk_seconds=100.0, chunk_overlap_seconds=500.0)
    chunks = candidates.build_chunks(_transcript(10), settings=settings)
    assert len(chunks) >= 1
    assert chunks[-1].end == 100.0


def test_short_transcript_makes_one_chunk():
    settings = Settings(chunk_seconds=900.0, chunk_overlap_seconds=120.0)
    chunks = candidates.build_chunks(_transcript(3), settings=settings)
    assert len(chunks) == 1
    assert chunks[0].index == 0


def test_empty_transcript_makes_no_chunks():
    assert candidates.build_chunks(Transcript(duration=0.0)) == []


def test_segments_synthesized_when_backend_returns_none():
    """Some backends emit words but no segments; chunking must still work."""
    words = [Word(text=f"w{i}", start=float(i), end=float(i) + 1) for i in range(45)]
    chunks = candidates.build_chunks(Transcript(duration=45.0, words=words))

    assert len(chunks) == 1
    # 45 words group into 20 + 20 + 5 lines.
    assert len(chunks[0].text.splitlines()) == 3
    assert "w0" in chunks[0].text
    assert "w44" in chunks[0].text
