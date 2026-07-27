from __future__ import annotations

from pathlib import Path

import pytest

from streetclip.config import Settings, TranscribeBackend
from streetclip.models import Segment, Transcript, Word
from streetclip.pipeline import ingest
from streetclip.pipeline.transcribe.base import (
    FakeTranscriber,
    get_transcriber,
    merge,
    shift,
)
from streetclip.pipeline.transcribe.groq import GroqTranscriber, _to_transcript

from .conftest import needs_ffmpeg


def _t(words, duration=10.0):
    return Transcript(
        duration=duration,
        words=[Word(text=t, start=s, end=e) for t, s, e in words],
        segments=[Segment(text=" ".join(w[0] for w in words), start=words[0][1], end=words[-1][2])],
    )


def test_shift_moves_every_timestamp():
    shifted = shift(_t([("a", 0.0, 1.0), ("b", 1.0, 2.0)]), 600.0)
    assert [w.start for w in shifted.words] == [600.0, 601.0]
    assert shifted.segments[0].end == 602.0
    assert shifted.duration == 610.0


def test_merge_orders_by_time_across_chunks():
    merged = merge([shift(_t([("b", 0.0, 1.0)]), 10.0), _t([("a", 0.0, 1.0)])])
    assert [w.text for w in merged.words] == ["a", "b"]
    assert [w.start for w in merged.words] == [0.0, 10.0]


def test_merge_of_nothing_is_empty():
    merged = merge([])
    assert merged.words == []
    assert merged.duration == 0.0


def test_fake_transcriber_returns_canned(transcript: Transcript):
    assert FakeTranscriber(transcript).transcribe(Path("ignored.wav")) is transcript


def test_get_transcriber_rejects_fake_backend():
    settings = Settings(transcribe_backend=TranscribeBackend.FAKE)
    with pytest.raises(ValueError, match="constructed directly"):
        get_transcriber(settings)


def test_groq_requires_api_key():
    settings = Settings(transcribe_backend=TranscribeBackend.GROQ, groq_api_key="")
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqTranscriber(settings)


def test_to_transcript_parses_dict_response():
    result = _to_transcript(
        {
            "duration": 5.0,
            "language": "en",
            "words": [
                {"word": " Repent", "start": 0.0, "end": 0.6},
                {"word": "  ", "start": 1, "end": 1},
            ],
            "segments": [{"text": " Repent ", "start": 0.0, "end": 0.6}],
        }
    )
    # Whitespace-only tokens are dropped rather than becoming empty words.
    assert [w.text for w in result.words] == ["Repent"]
    assert result.segments[0].text == "Repent"
    assert result.duration == 5.0


def test_to_transcript_infers_duration_when_absent():
    result = _to_transcript({"words": [{"word": "x", "start": 0.0, "end": 3.5}]})
    assert result.duration == 3.5


class _StubGroqClient:
    """Records each upload and replies with a fixed one-word transcript."""

    def __init__(self):
        self.calls = 0
        self.audio = self
        self.transcriptions = self

    def create(self, **kwargs):
        self.calls += 1
        return {
            "duration": 600.0,
            "language": "en",
            "words": [{"word": f"w{self.calls}", "start": 1.0, "end": 2.0}],
            "segments": [{"text": f"w{self.calls}", "start": 1.0, "end": 2.0}],
        }


@needs_ffmpeg
def test_groq_chunks_long_audio_and_stitches_timeline(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A file longer than one chunk must upload N times with offset timestamps."""
    audio = ingest.extract_audio(sample_video, tmp_path / "a.wav")

    # The fixture is 10s; shrink the chunk size so it splits into ~3 pieces.
    monkeypatch.setattr("streetclip.pipeline.transcribe.groq.CHUNK_SECONDS", 4.0)

    stub = _StubGroqClient()
    result = GroqTranscriber(Settings(groq_api_key="x"), client=stub).transcribe(audio)

    assert stub.calls >= 2, "long audio should be uploaded in multiple chunks"
    # Each chunk reported a word at t=1.0 locally; after stitching they must be
    # spread across the real timeline instead of stacked at 1.0.
    starts = [w.start for w in result.words]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)
    assert starts[0] == 1.0
    assert starts[1] == 5.0


@needs_ffmpeg
def test_split_audio_offsets_match_chunk_order(sample_video: Path, tmp_path: Path):
    audio = ingest.extract_audio(sample_video, tmp_path / "a.wav")
    chunks = ingest.split_audio(audio, tmp_path / "chunks", chunk_seconds=4.0)
    assert [offset for _, offset in chunks] == [0.0, 4.0, 8.0]
    assert all(path.exists() for path, _ in chunks)


@needs_ffmpeg
def test_split_audio_chunks_report_their_own_duration(sample_video: Path, tmp_path: Path):
    """Every chunk's header must describe that chunk, not the whole recording.

    ffmpeg's segment muxer stamps the source's total sample count into the last
    segment's FLAC header. A hosted transcriber then waits for audio that never
    arrives and the request dies on a read timeout.
    """
    audio = ingest.extract_audio(sample_video, tmp_path / "a.wav")
    total = ingest.audio_duration(audio)
    chunks = ingest.split_audio(audio, tmp_path / "chunks", chunk_seconds=4.0)

    # The final chunk is the one the segment muxer got wrong.
    last_path, last_offset = chunks[-1]
    assert ingest.audio_duration(last_path) < total
    assert ingest.audio_duration(last_path) == pytest.approx(total - last_offset, abs=0.2)


@needs_ffmpeg
def test_split_audio_chunk_durations_sum_to_the_source(sample_video: Path, tmp_path: Path):
    """No audio may be dropped or duplicated across the seams."""
    audio = ingest.extract_audio(sample_video, tmp_path / "a.wav")
    chunks = ingest.split_audio(audio, tmp_path / "chunks", chunk_seconds=4.0)
    covered = sum(ingest.audio_duration(p) for p, _ in chunks)
    assert covered == pytest.approx(ingest.audio_duration(audio), abs=0.2)
