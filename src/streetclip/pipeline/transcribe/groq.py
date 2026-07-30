"""Hosted Whisper via Groq.

Fast and cheap (~$0.04 per audio-hour) but capped on upload size, so anything
longer than a few minutes is split, transcribed piecewise, and stitched back
onto the source timeline.
"""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from streetclip.config import Settings, get_settings
from streetclip.models import Segment, Transcript, Word
from streetclip.pipeline import ingest
from streetclip.pipeline.transcribe.base import merge, shift

# Groq's documented cap is 25MB on the free tier. 10 minutes of 16kHz mono FLAC
# lands around 5-10MB, which leaves comfortable headroom for dense audio.
CHUNK_SECONDS = 600.0


class GroqTranscriber:
    def __init__(self, settings: Settings | None = None, client=None) -> None:
        self.settings = settings or get_settings()
        if client is not None:
            self._client = client
        else:
            if not self.settings.groq_api_key:
                raise ValueError("STREETCLIP_GROQ_API_KEY is not set")
            from groq import Groq

            self._client = Groq(api_key=self.settings.groq_api_key)

    def transcribe(self, audio_path: Path) -> Transcript:
        with tempfile.TemporaryDirectory() as tmp:
            chunks = ingest.split_audio(
                audio_path, Path(tmp), CHUNK_SECONDS, settings=self.settings
            )
            workers = max(1, min(self.settings.transcribe_concurrency, len(chunks)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                transcripts = pool.map(self._transcribe_one, (path for path, _ in chunks))
                # executor.map preserves input order, keeping timeline assembly
                # deterministic even when later uploads finish first.
                parts = [
                    shift(transcript, offset)
                    for transcript, (_, offset) in zip(transcripts, chunks, strict=True)
                ]
        return merge(parts)

    def _transcribe_one(self, path: Path) -> Transcript:
        with path.open("rb") as fh:
            response = self._client.audio.transcriptions.create(
                file=(path.name, fh.read()),
                model=self.settings.groq_model,
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
        return _to_transcript(response)


def _field(obj, name, default=None):
    """Groq's SDK returns pydantic models; tests hand us plain dicts."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _to_transcript(response) -> Transcript:
    words = [
        Word(
            text=str(_field(w, "word", "")).strip(),
            start=float(_field(w, "start", 0.0)),
            end=float(_field(w, "end", 0.0)),
        )
        for w in (_field(response, "words") or [])
    ]
    segments = [
        Segment(
            text=str(_field(s, "text", "")).strip(),
            start=float(_field(s, "start", 0.0)),
            end=float(_field(s, "end", 0.0)),
        )
        for s in (_field(response, "segments") or [])
    ]
    duration = float(_field(response, "duration", 0.0) or 0.0)
    if not duration:
        duration = max((w.end for w in words), default=0.0)

    return Transcript(
        duration=duration,
        language=str(_field(response, "language", "en") or "en"),
        words=[w for w in words if w.text],
        segments=[s for s in segments if s.text],
    )
