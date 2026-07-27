import { useMemo, useState } from "react";

// Seconds of context shown either side of the clip.
const PAD = 30;

// A drag shorter than this is a click, not a selection.
const CLICK_THRESHOLD = 0.05;

export default function Transcript({ words, clip, position, onSeek, onSetBounds }) {
  const [drag, setDrag] = useState(null);

  // Only the clip's neighbourhood is mounted. A 65-minute recording is ~11,000
  // words; rendering every span would be slow and nobody scrolls that far.
  const visible = useMemo(
    () => words.filter((w) => w.end > clip.start - PAD && w.start < clip.end + PAD),
    [words, clip.start, clip.end],
  );

  const range = drag
    ? { start: Math.min(drag.from, drag.to), end: Math.max(drag.from, drag.to) }
    : { start: clip.start, end: clip.end };

  function finishDrag() {
    if (!drag) return;
    const start = Math.min(drag.from, drag.to);
    const end = Math.max(drag.from, drag.to);
    setDrag(null);
    if (end - start < CLICK_THRESHOLD) onSeek(start);
    else onSetBounds({ start, end });
  }

  return (
    <div className="transcript" onPointerUp={finishDrag} onPointerLeave={finishDrag}>
      <div className="transcript-body">
        {visible.map((word, i) => {
          const inClip = word.start >= range.start && word.end <= range.end;
          const isNow = position >= word.start && position < word.end;
          return (
            <span
              key={`${word.start}-${i}`}
              className={["word", inClip ? "in-clip" : "", isNow ? "now" : ""].join(" ")}
              onPointerDown={(event) => {
                event.preventDefault();
                setDrag({ from: word.start, to: word.start });
              }}
              onPointerEnter={() => {
                if (drag) setDrag((d) => ({ ...d, to: word.end }));
              }}
            >
              {word.text}{" "}
            </span>
          );
        })}
      </div>
      <p className="transcript-hint">click a word to seek · drag across words to set bounds</p>
    </div>
  );
}
