"""Local Whisper via faster-whisper (CTranslate2).

Free, offline, and slow on CPU-only hosts. No chunking needed — faster-whisper
streams the file itself and has no upload cap.
"""

from __future__ import annotations

from pathlib import Path

from streetclip.config import Settings, get_settings
from streetclip.models import Segment, Transcript, Word


class LocalWhisperTranscriber:
    def __init__(self, settings: Settings | None = None, model=None) -> None:
        self.settings = settings or get_settings()
        self._model = model

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.settings.local_whisper_model,
                device="cpu",
                compute_type=self.settings.local_whisper_compute_type,
            )
        return self._model

    def transcribe(self, audio_path: Path) -> Transcript:
        segments_iter, info = self._get_model().transcribe(
            str(audio_path),
            word_timestamps=True,
            vad_filter=True,
        )

        words: list[Word] = []
        segments: list[Segment] = []
        # faster-whisper returns a generator; consuming it is what runs inference.
        for seg in segments_iter:
            text = (seg.text or "").strip()
            if text:
                segments.append(Segment(text=text, start=float(seg.start), end=float(seg.end)))
            for w in seg.words or []:
                token = (w.word or "").strip()
                if token:
                    words.append(Word(text=token, start=float(w.start), end=float(w.end)))

        duration = float(getattr(info, "duration", 0.0) or 0.0)
        if not duration:
            duration = max((w.end for w in words), default=0.0)

        return Transcript(
            duration=duration,
            language=str(getattr(info, "language", "en") or "en"),
            words=words,
            segments=segments,
        )
