import { useMemo, useRef, useState } from "react";

// Seconds of context shown either side of the clip.
const PAD = 30;

// A drag shorter than this is a click, not a selection.
const CLICK_THRESHOLD = 0.05;

export default function Transcript({ words, clip, position, onSeek, onSetBounds }) {
  // The drag lives in a ref as well as state: state drives the highlight, but
  // the handlers must read the *current* drag synchronously. Reading it from
  // the closure loses the first words of a fast drag, and — worse — lets
  // pointerup see a null drag and return without ever clearing it, which
  // strands the panel mid-selection with nothing highlighted.
  const dragRef = useRef(null);
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

  function begin(word) {
    dragRef.current = { from: word.start, to: word.start };
    setDrag(dragRef.current);
  }

  function extend(word) {
    if (!dragRef.current) return;
    dragRef.current = { ...dragRef.current, to: word.end };
    setDrag(dragRef.current);
  }

  function finish() {
    const d = dragRef.current;
    dragRef.current = null;
    setDrag(null);
    if (!d) return;

    const start = Math.min(d.from, d.to);
    const end = Math.max(d.from, d.to);
    if (end - start < CLICK_THRESHOLD) onSeek(start);
    else onSetBounds({ start, end });
  }

  return (
    <div className="transcript" onPointerUp={finish} onPointerLeave={finish}>
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
                begin(word);
              }}
              onPointerEnter={() => extend(word)}
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
