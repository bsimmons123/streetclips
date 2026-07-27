from __future__ import annotations

import json
from types import SimpleNamespace

from streetclip.config import Settings
from streetclip.models import Category, Chunk, Proposal, Transcript, Word
from streetclip.pipeline import score


def _chunk(start=0.0, end=900.0) -> Chunk:
    return Chunk(index=0, start=start, end=end, text="[0.0] hello")


def _response(payload) -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _moment(**overrides):
    base = {
        "start": 100.0,
        "end": 130.0,
        "score": 8.0,
        "category": "one_liner",
        "hook_title": "Repent and believe",
        "reason": "Lands without setup.",
    }
    base.update(overrides)
    return base


def _proposal(start, end, sc=8.0) -> Proposal:
    return Proposal(
        start=start,
        end=end,
        score=sc,
        category=Category.ONE_LINER,
        hook_title="t",
        reason="r",
    )


# --- parsing -----------------------------------------------------------------


def test_parse_reads_moments():
    result = score._parse(_response({"moments": [_moment()]}), _chunk())
    assert len(result) == 1
    assert result[0].category is Category.ONE_LINER
    assert result[0].duration == 30.0


def test_parse_returns_empty_for_no_moments():
    assert score._parse(_response({"moments": []}), _chunk()) == []


def test_parse_survives_malformed_json():
    """A garbled chunk response must not kill a two-hour run."""
    assert score._parse(_response("not json at all"), _chunk()) == []


def test_parse_skips_bad_entries_but_keeps_good_ones():
    payload = {
        "moments": [
            _moment(category="not_a_category"),
            {"start": 1.0},  # missing fields
            _moment(start=200.0, end=230.0),
        ]
    }
    result = score._parse(_response(payload), _chunk())
    assert len(result) == 1
    assert result[0].start == 200.0


def test_parse_clamps_timestamps_to_the_chunk():
    payload = {"moments": [_moment(start=-50.0, end=99999.0)]}
    result = score._parse(_response(payload), _chunk(start=0.0, end=900.0))
    assert result[0].start == 0.0
    assert result[0].end == 900.0


def test_parse_drops_inverted_ranges():
    assert score._parse(_response({"moments": [_moment(start=200, end=100)]}), _chunk()) == []


def test_parse_clamps_out_of_range_scores():
    result = score._parse(_response({"moments": [_moment(score=42.0)]}), _chunk())
    assert result[0].score == 10.0


# --- dedup -------------------------------------------------------------------


def test_dedup_keeps_higher_scoring_of_two_overlapping():
    proposals = [_proposal(100, 140, sc=6.0), _proposal(105, 145, sc=9.0)]
    kept = score.dedup(proposals)
    assert len(kept) == 1
    assert kept[0].score == 9.0


def test_dedup_keeps_distinct_moments():
    kept = score.dedup([_proposal(0, 30), _proposal(500, 530)])
    assert len(kept) == 2


def test_dedup_returns_chronological_order():
    kept = score.dedup([_proposal(500, 530, sc=9.0), _proposal(0, 30, sc=6.0)])
    assert [p.start for p in kept] == [0.0, 500.0]


def test_dedup_of_empty_list():
    assert score.dedup([]) == []


# --- boundary snapping -------------------------------------------------------


def _word_transcript() -> Transcript:
    return Transcript(
        duration=100.0,
        words=[
            Word(text="a", start=9.0, end=10.5),
            Word(text="b", start=10.5, end=12.0),
            Word(text="c", start=12.0, end=14.5),
        ],
    )


def test_snap_extends_outward_to_word_edges():
    settings = Settings(boundary_padding_seconds=0.0)
    # Range starts and ends mid-word; both edges must move outward, never inward.
    start, end = score.snap(_proposal(10.0, 13.0), _word_transcript(), settings)
    assert start == 9.0
    assert end == 14.5


def test_snap_adds_padding():
    settings = Settings(boundary_padding_seconds=0.5)
    start, end = score.snap(_proposal(10.0, 13.0), _word_transcript(), settings)
    assert start == 8.5
    assert end == 15.0


def test_snap_clamps_to_recording_bounds():
    settings = Settings(boundary_padding_seconds=5.0)
    transcript = Transcript(duration=20.0, words=[Word(text="a", start=0.0, end=20.0)])
    start, end = score.snap(_proposal(0.0, 20.0), transcript, settings)
    assert start == 0.0
    assert end == 20.0


def test_snap_leaves_boundaries_alone_when_no_words_overlap():
    settings = Settings(boundary_padding_seconds=0.0)
    start, end = score.snap(_proposal(50.0, 80.0), _word_transcript(), settings)
    assert (start, end) == (50.0, 80.0)


# --- candidates --------------------------------------------------------------


def _long_transcript() -> Transcript:
    words = [Word(text=f"w{i}", start=float(i), end=float(i) + 1.0) for i in range(600)]
    return Transcript(duration=600.0, words=words)


def test_to_candidates_drops_too_short_and_too_long():
    settings = Settings(
        min_clip_seconds=20.0, max_clip_seconds=60.0, boundary_padding_seconds=0.0
    )
    proposals = [
        _proposal(0, 5, sc=9.0),  # too short
        _proposal(100, 130, sc=8.0),  # keeper
        _proposal(200, 400, sc=7.0),  # too long
    ]
    result = score.to_candidates(proposals, _long_transcript(), settings)
    assert len(result) == 1
    assert result[0].start == 100.0


def test_to_candidates_ranks_by_score():
    settings = Settings(boundary_padding_seconds=0.0)
    proposals = [_proposal(0, 30, sc=6.0), _proposal(100, 130, sc=9.5)]
    result = score.to_candidates(proposals, _long_transcript(), settings)
    assert [c.score for c in result] == [9.5, 6.0]


def test_to_candidates_fills_excerpt_from_transcript():
    settings = Settings(boundary_padding_seconds=0.0)
    result = score.to_candidates([_proposal(10, 40)], _long_transcript(), settings)
    assert result[0].excerpt.startswith("w10")


def test_to_candidates_of_empty_list():
    assert score.to_candidates([], _long_transcript()) == []


# --- scorer wiring -----------------------------------------------------------


class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None
        self.messages = self

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _response(self.payload)


def test_scorer_sends_chunk_text_and_parses_result():
    stub = _StubClient({"moments": [_moment()]})
    result = score.Scorer(Settings(), client=stub).propose(_chunk())

    assert len(result) == 1
    assert "[0.0] hello" in stub.kwargs["messages"][0]["content"]
    assert stub.kwargs["model"] == "claude-opus-5"
    # Structured output keeps us from having to scrape prose for JSON.
    assert stub.kwargs["output_config"]["format"]["type"] == "json_schema"
