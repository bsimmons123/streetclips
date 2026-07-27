"""Core data structures passed between pipeline stages.

Every stage takes and returns these plain models, so each stage can be tested
in isolation against fixtures without touching ffmpeg, an API, or the network.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Category(StrEnum):
    """The rubric a proposed moment is judged against."""

    CONFRONTATION = "confrontation"
    ONE_LINER = "one_liner"
    EMOTIONAL_PEAK = "emotional_peak"
    SCRIPTURE = "scripture"


class Word(BaseModel):
    text: str
    start: float
    end: float


class Segment(BaseModel):
    """A transcriber-emitted utterance. Roughly sentence-sized."""

    text: str
    start: float
    end: float


class Transcript(BaseModel):
    duration: float
    language: str = "en"
    words: list[Word] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def words_in_range(self, start: float, end: float) -> list[Word]:
        """Words whose span overlaps [start, end)."""
        return [w for w in self.words if w.end > start and w.start < end]

    def excerpt(self, start: float, end: float) -> str:
        return " ".join(w.text for w in self.words_in_range(start, end))


class MediaInfo(BaseModel):
    """What `ingest` learns about the source file."""

    path: str
    duration: float
    width: int
    height: int
    fps: float


class EnergySeries(BaseModel):
    """Per-second normalized loudness, index 0 == second 0 of the source."""

    values: list[float]

    @property
    def duration(self) -> float:
        return float(len(self.values))

    def mean_in_range(self, start: float, end: float) -> float:
        lo = max(0, int(start))
        hi = min(len(self.values), max(lo + 1, int(end)))
        window = self.values[lo:hi]
        return sum(window) / len(window) if window else 0.0


class Chunk(BaseModel):
    """A slice of transcript sized to fit comfortably in one LLM request."""

    index: int
    start: float
    end: float
    text: str


class Proposal(BaseModel):
    """A moment the model proposed. Boundaries are still raw model output."""

    start: float
    end: float
    score: float = Field(ge=0.0, le=10.0)
    category: Category
    hook_title: str
    reason: str

    @property
    def duration(self) -> float:
        return self.end - self.start

    def overlaps(self, other: Proposal) -> float:
        """Fraction of the shorter proposal covered by the intersection."""
        intersection = min(self.end, other.end) - max(self.start, other.start)
        if intersection <= 0:
            return 0.0
        shorter = min(self.duration, other.duration)
        return intersection / shorter if shorter > 0 else 0.0


class Candidate(BaseModel):
    """A proposal after dedup and boundary snapping. What the operator reviews."""

    start: float
    end: float
    score: float
    category: Category
    hook_title: str
    reason: str
    excerpt: str

    @property
    def duration(self) -> float:
        return self.end - self.start


class Report(BaseModel):
    """The Phase 1 deliverable: everything the run learned, as JSON."""

    media: MediaInfo
    transcript: Transcript
    candidates: list[Candidate]
