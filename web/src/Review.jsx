import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import Transcript from "./Transcript";
import { category, clock, stamp } from "./format";

const NUDGES = [-1, -0.25, 0.25, 1];

function Meter({ score }) {
  const lit = Math.round(score);
  return (
    <div className="meter">
      {Array.from({ length: 10 }, (_, i) => (
        <i key={i} className={i < lit ? (i >= 8 ? "lit hot" : "lit") : ""} />
      ))}
      <span className="value">{score.toFixed(1)}</span>
    </div>
  );
}

function Bound({ label, value, onNudge, disabled }) {
  return (
    <div className="bound">
      <div className="section-label">{label}</div>
      <div className="nudge">
        {NUDGES.slice(0, 2).map((delta) => (
          <button key={delta} disabled={disabled} onClick={() => onNudge(delta)}>
            {delta}
          </button>
        ))}
        <span className="stamp">{clock(value)}</span>
        {NUDGES.slice(2).map((delta) => (
          <button key={delta} disabled={disabled} onClick={() => onNudge(delta)}>
            +{delta}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function Review({ canExport = true, job, onPatchClip, onExport, exporting, onBack }) {
  const clips = job.clips;
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const video = useRef(null);
  const [words, setWords] = useState([]);

  // Loaded once per workspace; the panel windows it down per clip.
  useEffect(() => {
    api
      .getTranscript(job.id)
      .then((t) => setWords(t.words || []))
      .catch(() => setWords([]));
  }, [job.id]);

  const current = clips[Math.min(index, clips.length - 1)];

  // Seek to the head of whichever clip is under review, so the preview always
  // matches the row the operator is looking at. Seeking before metadata has
  // loaded is silently ignored by the browser, hence the onLoadedMetadata pair.
  const seekToStart = useCallback(() => {
    if (video.current && current) {
      video.current.currentTime = current.start;
      setPosition(current.start);
    }
  }, [current?.id, current?.start]);

  useEffect(seekToStart, [seekToStart]);

  const play = useCallback(() => {
    const el = video.current;
    if (!el || !current) return;
    if (playing) {
      el.pause();
      return;
    }
    if (el.currentTime < current.start || el.currentTime >= current.end) {
      el.currentTime = current.start;
    }
    el.play();
  }, [playing, current]);

  const keep = useCallback(() => {
    if (current) onPatchClip(current.id, { selected: !current.selected });
  }, [current, onPatchClip]);

  useEffect(() => {
    function onKey(event) {
      if (event.target.tagName === "INPUT") return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setIndex((i) => Math.min(i + 1, clips.length - 1));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setIndex((i) => Math.max(i - 1, 0));
      } else if (event.key === " ") {
        event.preventDefault();
        play();
      } else if (event.key === "k" || event.key === "K") {
        keep();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [clips.length, play, keep]);

  function onTimeUpdate() {
    const el = video.current;
    if (!el || !current) return;
    setPosition(el.currentTime);
    // Stop at the out point rather than running on into the rest of the source.
    if (el.currentTime >= current.end) el.pause();
  }

  function nudge(field, delta) {
    const next = Math.max(0, current[field] + delta);
    if (field === "start" && next >= current.end) return;
    if (field === "end" && next <= current.start) return;
    onPatchClip(current.id, { [field]: next });
  }

  const kept = clips.filter((clip) => clip.selected);
  const rendered = clips.filter((clip) => clip.rendered);

  if (!current) {
    return (
      <div className="progress">
        <h2>No candidates came back</h2>
        <p className="stage">nothing scored high enough to propose</p>
        <p>
          <button className="btn" onClick={onBack}>
            Pick another recording
          </button>
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="workbench">
        <div className="queue">
          <div className="queue-head">
            <span className="section-label">Queue</span>
            <span className="count">
              {kept.length}/{clips.length} kept
            </span>
          </div>
          {clips.map((clip, i) => (
            <button
              key={clip.id}
              className={[
                "queue-row",
                i === index ? "current" : "",
                clip.selected ? "kept" : "",
              ].join(" ")}
              onClick={() => setIndex(i)}
            >
              <span className="rank">{clip.selected ? "keep" : String(i + 1).padStart(2, "0")}</span>
              <span>
                <span className="hook">{clip.hook_title}</span>
                <span className="meta">
                  <span>{clip.score.toFixed(1)}</span>
                  <span>{Math.round(clip.duration)}s</span>
                  <span>{clock(clip.start)}</span>
                </span>
              </span>
            </button>
          ))}
        </div>

        <div className="stage-area">
          <div className="clip-head">
            <div>
              <div className="category">{category(current.category)}</div>
              <h2>{current.hook_title}</h2>
            </div>
            <Meter score={current.score} />
          </div>

          <div className="stage-grid">
            <div className="player">
              <video
                ref={video}
                src={`/api/workspaces/${job.id}/source`}
                preload="metadata"
                onLoadedMetadata={seekToStart}
                onTimeUpdate={onTimeUpdate}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
              />
              <div className="transport">
                <button className="btn" onClick={play}>
                  {playing ? "Pause" : "Play"}
                </button>
                <span className="time">
                  {stamp(position - current.start)} / {stamp(current.duration)}
                </span>
              </div>
            </div>

            <div>
              {words.length > 0 ? (
                <Transcript
                  words={words}
                  clip={current}
                  position={position}
                  onSeek={(t) => {
                    if (video.current) video.current.currentTime = t;
                  }}
                  onSetBounds={({ start, end }) => onPatchClip(current.id, { start, end })}
                />
              ) : (
                <p className="excerpt">{current.excerpt}</p>
              )}
              {current.reason && <p className="reason">{current.reason}</p>}

              <div className="bounds">
                <Bound
                  label="In"
                  value={current.start}
                  disabled={exporting}
                  onNudge={(delta) => nudge("start", delta)}
                />
                <Bound
                  label="Out"
                  value={current.end}
                  disabled={exporting}
                  onNudge={(delta) => nudge("end", delta)}
                />
              </div>

              <div className="decide">
                <button
                  className={current.selected ? "btn keep on" : "btn keep"}
                  onClick={keep}
                >
                  {current.selected ? "Kept" : "Keep"}
                </button>
                <button
                  className="btn ghost"
                  onClick={() => setIndex(Math.min(index + 1, clips.length - 1))}
                >
                  Next
                </button>
                <span className="hint">↑↓ move · space play · K keep</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="export-bar">
        <button className="btn ghost" onClick={onBack}>
          ← Sources
        </button>
        <span className="tally">
          {kept.length} selected · {Math.round(kept.reduce((sum, c) => sum + c.duration, 0))}s total
        </span>
        {rendered.length > 0 && (
          <div className="downloads">
            {rendered.map((clip) => (
              <a key={clip.id} href={`/api/clips/${clip.id}/download`}>
                {clip.hook_title}
              </a>
            ))}
          </div>
        )}
        <span className="spacer" />
        {canExport ? (
          <button
            className="btn primary"
            disabled={exporting || kept.length === 0}
            onClick={onExport}
          >
            {exporting ? "Exporting…" : `Export ${kept.length || ""}`.trim()}
          </button>
        ) : (
          <span className="read-only-note">Admin access is required to export.</span>
        )}
      </div>
    </>
  );
}
