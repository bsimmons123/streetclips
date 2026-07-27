"""Ask Claude which moments are worth posting, then clean up its boundaries.

This module is the core of the tool. The rubric below is the actual product —
everything else in the pipeline exists to feed it and to render what it picks.
"""

from __future__ import annotations

import json

from streetclip.config import Settings, get_settings
from streetclip.models import Candidate, Category, Chunk, Proposal, Transcript

SYSTEM_PROMPT = """\
You find the moments in a street preacher's recording that would work as \
standalone short-form video clips for YouTube Shorts, Instagram Reels, or TikTok.

You are given a transcript chunk. Each line starts with its absolute start time \
in seconds on the source recording, and lines delivered at noticeably raised \
volume are marked <raised voice>.

Judge each candidate moment against these four categories:

- confrontation: a back-and-forth with a heckler or challenger. Usually the \
  highest-engagement material. Look for question-and-answer structure, direct \
  address, or a shift in who is speaking.
- one_liner: a quotable line that lands without setup. It must make sense to \
  someone who has no idea what came before it.
- emotional_peak: a moment of real intensity where the delivery carries the \
  weight. The <raised voice> markers corroborate these, but volume alone is not \
  a moment — the words have to be worth hearing.
- scripture: a verse followed by a concrete, self-contained application. \
  A teaching clip rather than a conflict one.

Rules for the moments you return:

- Between 20 and 60 seconds long. Prefer 25-45.
- The clip must stand alone. If it opens mid-argument or ends on an unresolved \
  thought, either extend it or drop it.
- Start slightly before the good part so the viewer has a beat of context, and \
  end on the landing, not after it.
- Score 0-10 on how likely this is to hold a scrolling viewer. Be honest and \
  use the full range: most of a long recording is not clip-worthy. A 5 means \
  "fine but forgettable." Reserve 8+ for moments you would actually post.
- Return only moments scoring 5 or above. If a chunk has nothing worth posting, \
  return an empty list. That is a valid and useful answer.
- hook_title: how the operator would recognize this clip in a list. Six words \
  or fewer, drawn from what was actually said. Not clickbait.
- reason: one sentence on why this works as a short.
"""

MOMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "score": {"type": "number"},
                    "category": {
                        "type": "string",
                        "enum": [c.value for c in Category],
                    },
                    "hook_title": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["start", "end", "score", "category", "hook_title", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["moments"],
    "additionalProperties": False,
}


class Scorer:
    def __init__(self, settings: Settings | None = None, client=None) -> None:
        self.settings = settings or get_settings()
        if client is not None:
            self._client = client
        else:
            import anthropic

            self._client = anthropic.Anthropic(
                api_key=self.settings.anthropic_api_key or None,
                timeout=self.settings.scoring_timeout_seconds,
            )

    def propose(self, chunk: Chunk) -> list[Proposal]:
        # Streamed rather than a plain create(): reading a 15-minute transcript
        # at high effort routinely runs past the SDK's 10-minute request
        # timeout, and a non-streaming call has no keepalive to survive it.
        with self._client.messages.stream(
            model=self.settings.scoring_model,
            max_tokens=self.settings.scoring_max_tokens,
            system=SYSTEM_PROMPT,
            output_config={
                "effort": self.settings.scoring_effort,
                "format": {"type": "json_schema", "schema": MOMENT_SCHEMA},
            },
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Transcript from {chunk.start:.0f}s to {chunk.end:.0f}s "
                        f"of the recording:\n\n{chunk.text}"
                    ),
                }
            ],
        ) as stream:
            response = stream.get_final_message()
        return _parse(response, chunk)


def _parse(response, chunk: Chunk) -> list[Proposal]:
    """Turn the model's JSON into Proposals, dropping anything unusable.

    A malformed or out-of-range moment is discarded rather than raised: one bad
    entry in one chunk should not fail a two-hour run.
    """
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text.strip():
        return []

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []

    proposals: list[Proposal] = []
    for raw in payload.get("moments", []):
        try:
            start = float(raw["start"])
            end = float(raw["end"])
            # The model occasionally drifts outside the chunk it was shown.
            # Clamping is safer than trusting a timestamp we can't verify.
            start = max(chunk.start, start)
            end = min(chunk.end, end)
            if end <= start:
                continue
            proposals.append(
                Proposal(
                    start=start,
                    end=end,
                    score=min(10.0, max(0.0, float(raw["score"]))),
                    category=Category(raw["category"]),
                    hook_title=str(raw["hook_title"]).strip(),
                    reason=str(raw["reason"]).strip(),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return proposals


def dedup(proposals: list[Proposal], settings: Settings | None = None) -> list[Proposal]:
    """Collapse the same moment proposed by two overlapping chunks.

    Highest score wins, so the chunk that saw the moment most completely is the
    one that decides its boundaries.
    """
    settings = settings or get_settings()
    threshold = settings.dedup_overlap_threshold

    kept: list[Proposal] = []
    for proposal in sorted(proposals, key=lambda p: (-p.score, p.start)):
        if any(proposal.overlaps(k) >= threshold for k in kept):
            continue
        kept.append(proposal)

    return sorted(kept, key=lambda p: p.start)


def snap(
    proposal: Proposal,
    transcript: Transcript,
    settings: Settings | None = None,
) -> tuple[float, float]:
    """Move boundaries outward to the nearest word edge, plus a little padding.

    Cutting mid-word is the most obvious tell of an auto-generated clip, so the
    boundaries always move outward rather than to the nearest edge in either
    direction.
    """
    settings = settings or get_settings()
    pad = settings.boundary_padding_seconds

    words = transcript.words_in_range(proposal.start, proposal.end)
    if words:
        start = min(proposal.start, words[0].start)
        end = max(proposal.end, words[-1].end)
    else:
        start, end = proposal.start, proposal.end

    start = max(0.0, start - pad)
    end = min(transcript.duration, end + pad) if transcript.duration else end + pad
    return start, end


def to_candidates(
    proposals: list[Proposal],
    transcript: Transcript,
    settings: Settings | None = None,
) -> list[Candidate]:
    """Dedup, snap, drop anything outside the target length, rank by score."""
    settings = settings or get_settings()

    candidates: list[Candidate] = []
    for proposal in dedup(proposals, settings):
        start, end = snap(proposal, transcript, settings)
        duration = end - start
        if duration < settings.min_clip_seconds or duration > settings.max_clip_seconds:
            continue
        candidates.append(
            Candidate(
                start=start,
                end=end,
                score=proposal.score,
                category=proposal.category,
                hook_title=proposal.hook_title,
                reason=proposal.reason,
                excerpt=transcript.excerpt(start, end),
            )
        )

    return sorted(candidates, key=lambda c: -c.score)
