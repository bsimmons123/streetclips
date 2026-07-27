from __future__ import annotations

from pathlib import Path

from streetclip.config import Settings
from streetclip.models import Transcript, Word
from streetclip.pipeline.render import captions

GOLDEN = Path(__file__).parent / "fixtures" / "captions.ass"


def _words(spec) -> list[Word]:
    return [Word(text=t, start=s, end=e) for t, s, e in spec]


def _settings(**overrides) -> Settings:
    base = {"caption_words_per_group": 3, "caption_max_gap": 0.8}
    base.update(overrides)
    return Settings(**base)


# --- timestamps --------------------------------------------------------------


def test_timestamp_format():
    assert captions.timestamp(0.0) == "0:00:00.00"
    assert captions.timestamp(1.5) == "0:00:01.50"
    assert captions.timestamp(3725.25) == "1:02:05.25"


def test_timestamp_rounds_to_centiseconds():
    assert captions.timestamp(1.004) == "0:00:01.00"
    assert captions.timestamp(1.006) == "0:00:01.01"


def test_timestamp_clamps_negative():
    assert captions.timestamp(-5.0) == "0:00:00.00"


# --- escaping ----------------------------------------------------------------


def test_escape_neutralizes_override_braces():
    # A literal brace would open an ASS override block and swallow the text.
    assert "{" not in captions.escape("we {said} this")
    assert "}" not in captions.escape("we {said} this")


def test_escape_flattens_newlines():
    assert "\n" not in captions.escape("two\nlines")


def test_escape_leaves_ordinary_text_alone():
    assert captions.escape("Repent!") == "Repent!"


# --- grouping ----------------------------------------------------------------


def test_groups_respect_max_size():
    words = _words([(f"w{i}", i * 0.3, i * 0.3 + 0.3) for i in range(7)])
    groups = captions.group_words(words, max_per_group=3, max_gap=10.0)
    assert [len(g) for g in groups] == [3, 3, 1]


def test_groups_break_on_a_long_pause():
    words = _words([("a", 0.0, 0.4), ("b", 0.4, 0.8), ("c", 5.0, 5.4)])
    groups = captions.group_words(words, max_per_group=10, max_gap=0.8)
    assert [[w.text for w in g] for g in groups] == [["a", "b"], ["c"]]


def test_grouping_of_no_words():
    assert captions.group_words([], max_per_group=3, max_gap=0.8) == []


# --- timing ------------------------------------------------------------------


def _dialogue_lines(ass: str) -> list[str]:
    return [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]


def test_one_event_per_word():
    transcript = Transcript(duration=10.0, words=_words([("a", 0, 1), ("b", 1, 2)]))
    assert len(_dialogue_lines(captions.build(transcript, settings=_settings()))) == 2


def test_each_word_is_highlighted_in_turn():
    transcript = Transcript(duration=10.0, words=_words([("alpha", 0, 1), ("beta", 1, 2)]))
    lines = _dialogue_lines(captions.build(transcript, settings=_settings()))

    # Both events show the full group; the highlight moves between them.
    assert "alpha" in lines[0] and "beta" in lines[0]
    assert lines[0].index(captions.HIGHLIGHT) < lines[0].index("alpha")
    assert lines[1].index(captions.HIGHLIGHT) < lines[1].index("beta")


def test_words_are_held_until_the_next_one_starts():
    """No gap between captions inside a group, even when speech has micro-pauses."""
    transcript = Transcript(duration=10.0, words=_words([("a", 0.0, 0.4), ("b", 1.0, 1.4)]))
    lines = _dialogue_lines(captions.build(transcript, settings=_settings()))
    # First word runs to 1.0 (when "b" starts), not 0.4 (when "a" ends).
    assert "0:00:00.00,0:00:01.00" in lines[0]


def test_times_are_rebased_to_the_clip():
    words = _words([("a", 100.0, 100.5), ("b", 100.5, 101.0)])
    transcript = Transcript(duration=200.0, words=words)
    ass = captions.build(transcript, start=100.0, end=101.0, settings=_settings())
    lines = _dialogue_lines(ass)
    # The clip starts at zero even though the words live at 100s in the source.
    assert lines[0].startswith("Dialogue: 0,0:00:00.00,")


def test_words_outside_the_clip_are_excluded():
    words = _words([("before", 0.0, 1.0), ("inside", 50.0, 51.0), ("after", 90.0, 91.0)])
    transcript = Transcript(duration=100.0, words=words)
    ass = captions.build(transcript, start=49.0, end=52.0, settings=_settings())
    assert "inside" in ass
    assert "before" not in ass
    assert "after" not in ass


def test_word_straddling_the_end_is_clamped():
    words = _words([("a", 10.0, 11.0), ("spills", 11.0, 20.0)])
    transcript = Transcript(duration=100.0, words=words)
    lines = _dialogue_lines(captions.build(transcript, start=10.0, end=13.0, settings=_settings()))
    # Nothing may extend past the clip's own duration.
    assert lines[-1].split(",")[2] == "0:00:03.00"


def test_zero_length_events_are_dropped():
    words = _words([("a", 5.0, 5.0), ("b", 5.0, 6.0)])
    transcript = Transcript(duration=10.0, words=words)
    for line in _dialogue_lines(captions.build(transcript, settings=_settings())):
        start, end = line.split(",")[1:3]
        assert end > start


def test_empty_transcript_still_produces_a_valid_file():
    ass = captions.build(Transcript(duration=10.0), settings=_settings())
    assert "[Events]" in ass
    assert _dialogue_lines(ass) == []


# --- header ------------------------------------------------------------------


def test_header_uses_configured_output_size_and_font():
    settings = _settings(output_width=1080, output_height=1920, caption_font="Impact")
    ass = captions.build(Transcript(duration=1.0), settings=settings)
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "Impact" in ass


# --- golden file -------------------------------------------------------------


def _golden_transcript() -> Transcript:
    return Transcript(
        duration=12.0,
        words=_words(
            [
                ("Repent", 0.0, 0.6),
                ("and", 0.6, 0.8),
                ("believe", 0.8, 1.4),
                ("the", 1.4, 1.6),
                ("gospel", 1.6, 2.4),
                ("Who", 4.0, 4.3),
                ("told", 4.3, 4.6),
                ("you", 4.6, 4.9),
                ("that?", 4.9, 5.6),
            ]
        ),
    )


def test_matches_golden_file():
    """Caption regressions are invisible until you watch a clip — pin the output."""
    produced = captions.build(_golden_transcript(), settings=_settings())
    assert produced == GOLDEN.read_text(), (
        "caption output changed; review the diff and update tests/fixtures/captions.ass "
        "if the change is intended"
    )


def test_write_creates_parent_directories(tmp_path: Path):
    dest = captions.write(
        _golden_transcript(), tmp_path / "nested" / "c.ass", settings=_settings()
    )
    assert dest.exists()
    assert dest.read_text().startswith("[Script Info]")
