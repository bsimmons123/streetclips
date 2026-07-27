"""Transcription backend interface.

Downstream stages depend only on this Protocol, so `signals`, `candidates`,
`score` and `render` are all testable with a fake and never touch the network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from streetclip.config import Settings, TranscribeBackend, get_settings
from streetclip.models import Segment, Transcript, Word


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> Transcript: ...


class FakeTranscriber:
    """Returns a canned transcript. For tests and dry runs."""

    def __init__(self, transcript: Transcript) -> None:
        self._transcript = transcript

    def transcribe(self, audio_path: Path) -> Transcript:
        return self._transcript


def shift(transcript: Transcript, offset: float) -> Transcript:
    """Move every timestamp forward by `offset` seconds.

    Used to reassemble chunked transcription back onto the source timeline.
    """
    return Transcript(
        duration=transcript.duration + offset,
        language=transcript.language,
        words=[
            Word(text=w.text, start=w.start + offset, end=w.end + offset)
            for w in transcript.words
        ],
        segments=[
            Segment(text=s.text, start=s.start + offset, end=s.end + offset)
            for s in transcript.segments
        ],
    )


def merge(parts: list[Transcript]) -> Transcript:
    """Concatenate already-shifted chunk transcripts into one timeline."""
    words: list[Word] = []
    segments: list[Segment] = []
    for part in parts:
        words.extend(part.words)
        segments.extend(part.segments)

    words.sort(key=lambda w: w.start)
    segments.sort(key=lambda s: s.start)
    duration = max((p.duration for p in parts), default=0.0)
    language = next((p.language for p in parts), "en")
    return Transcript(duration=duration, language=language, words=words, segments=segments)


def get_transcriber(settings: Settings | None = None) -> Transcriber:
    """Resolve the configured backend, importing it lazily.

    Lazy import matters: neither `groq` nor `faster-whisper` is a hard
    dependency, so installing only the backend you use has to actually work.
    """
    settings = settings or get_settings()

    match settings.transcribe_backend:
        case TranscribeBackend.GROQ:
            from streetclip.pipeline.transcribe.groq import GroqTranscriber

            return GroqTranscriber(settings)
        case TranscribeBackend.LOCAL:
            from streetclip.pipeline.transcribe.local_whisper import LocalWhisperTranscriber

            return LocalWhisperTranscriber(settings)
        case TranscribeBackend.FAKE:
            raise ValueError(
                "the fake backend must be constructed directly with a transcript, "
                "not resolved from settings"
            )

    raise ValueError(f"unknown transcribe backend: {settings.transcribe_backend}")
