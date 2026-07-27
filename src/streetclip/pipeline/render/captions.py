"""Word-level burned-in captions, as an ASS subtitle file.

Pure string formatting from a Transcript — no video, no ffmpeg — so the output
can be compared against a golden file. Caption drift is the kind of bug that
looks fine in a unit test and ruins every clip, so the timing lives here where
it can be asserted on directly.

The active-word highlight is done by emitting one Dialogue event per word, each
showing the whole group with a single word recolored. That is more events than
ASS karaoke tags would need, but it renders identically on every libass build
and stays readable as text.
"""

from __future__ import annotations

from streetclip.config import Settings, get_settings
from streetclip.models import Transcript, Word

# ASS colors are &HAABBGGRR — alpha first, then blue/green/red, reversed from hex.
WHITE = "&H00FFFFFF"
HIGHLIGHT = "&H0000E5FF"  # amber
OUTLINE = "&H00000000"

HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, \
Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{size},{white},{outline},{outline},-1,0,0,0,100,100,0,0,1,\
{border},2,2,80,80,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def timestamp(seconds: float) -> str:
    """ASS wants H:MM:SS.cc with a single-digit hour."""
    seconds = max(0.0, seconds)
    centiseconds = int(round(seconds * 100))
    hours, rem = divmod(centiseconds, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def escape(text: str) -> str:
    """Braces open an override block in ASS, so they cannot survive verbatim."""
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def group_words(
    words: list[Word],
    max_per_group: int,
    max_gap: float,
) -> list[list[Word]]:
    """Split words into caption-sized groups.

    Groups also break across a pause longer than `max_gap`, so a caption never
    spans silence — otherwise a group started before a long pause sits frozen
    on screen while nothing is being said.
    """
    groups: list[list[Word]] = []
    current: list[Word] = []

    for word in words:
        if current:
            gap = word.start - current[-1].end
            if len(current) >= max_per_group or gap > max_gap:
                groups.append(current)
                current = []
        current.append(word)

    if current:
        groups.append(current)
    return groups


def _dialogue(start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{timestamp(start)},{timestamp(end)},Caption,,0,0,0,,{text}"


def _line(group: list[Word], active: int) -> str:
    """The group's text with word `active` recolored."""
    parts = []
    for i, word in enumerate(group):
        token = escape(word.text)
        if i == active:
            parts.append(f"{{\\c{HIGHLIGHT}}}{token}{{\\c{WHITE}}}")
        else:
            parts.append(token)
    return " ".join(parts)


def build(
    transcript: Transcript,
    start: float = 0.0,
    end: float | None = None,
    settings: Settings | None = None,
) -> str:
    """Render captions for [start, end) with times rebased to the clip.

    `start`/`end` are on the source timeline; the emitted file starts at zero,
    because the clip ffmpeg burns it onto has been trimmed.
    """
    settings = settings or get_settings()
    end = transcript.duration if end is None else end

    header = HEADER.format(
        width=settings.output_width,
        height=settings.output_height,
        font=settings.caption_font,
        size=settings.caption_size,
        white=WHITE,
        outline=OUTLINE,
        border=settings.caption_outline,
        margin=settings.caption_margin_v,
    )

    words = transcript.words_in_range(start, end)
    lines: list[str] = []

    for group in group_words(words, settings.caption_words_per_group, settings.caption_max_gap):
        # Clamp to the clip so a word straddling the boundary doesn't render
        # before the clip begins or after it ends.
        group_end = min(group[-1].end, end)

        for i, word in enumerate(group):
            shown_from = max(word.start, start)
            # Hold each word until the next one begins, so there is never a gap
            # between captions inside a group.
            shown_to = group[i + 1].start if i + 1 < len(group) else group_end
            shown_to = min(shown_to, end)
            if shown_to <= shown_from:
                continue
            lines.append(
                _dialogue(shown_from - start, shown_to - start, _line(group, i))
            )

    return header + "\n".join(lines) + "\n"


def write(
    transcript: Transcript,
    dest,
    start: float = 0.0,
    end: float | None = None,
    settings: Settings | None = None,
):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(build(transcript, start, end, settings), encoding="utf-8")
    return dest
