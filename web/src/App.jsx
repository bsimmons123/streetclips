import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import Review from "./Review";
import SourcePicker from "./SourcePicker";

const STAGES = {
  probe: "reading the container",
  audio: "extracting audio",
  transcribe: "transcribing",
  signals: "measuring energy",
  score: "reading the transcript",
  render: "rendering shorts",
};

function Progress({ job, onBack }) {
  const failed = job.status === "failed";
  return (
    <div className="progress">
      <h2>{job.source_name}</h2>
      <p className="stage">{failed ? "failed" : STAGES[job.stage] || job.stage || "queued"}</p>
      {!failed && (
        <div className="bar">
          <div style={{ width: `${Math.round((job.progress || 0) * 100)}%` }} />
        </div>
      )}
      {failed && <div className="failure">{job.error}</div>}
      {failed && (
        <p>
          <button className="btn" onClick={onBack}>
            Back to sources
          </button>
        </p>
      )}
    </div>
  );
}

export default function App() {
  const [jobId, setJobId] = useState(null);
  const [job, setJob] = useState(null);
  const [renderId, setRenderId] = useState(null);
  const [error, setError] = useState(null);

  const fail = useCallback((err) => setError(err.message || String(err)), []);

  const refresh = useCallback(() => {
    if (jobId === null) return Promise.resolve();
    return api.getJob(jobId).then(setJob).catch(fail);
  }, [jobId, fail]);

  useEffect(() => {
    if (error) {
      const timer = setTimeout(() => setError(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [error]);

  // Analysis job: refetch on every progress event so clips appear the moment
  // the worker writes them.
  useEffect(() => {
    if (jobId === null) return;
    refresh();
    return api.watchJob(jobId, (update) => {
      setJob((prev) => (prev ? { ...prev, ...update } : prev));
      if (update.status === "done") refresh();
    });
  }, [jobId, refresh]);

  // Render job lives alongside the analysis job; when it settles, the clip rows
  // have rendered paths worth refetching.
  useEffect(() => {
    if (renderId === null) return;
    return api.watchJob(renderId, (update) => {
      if (update.status === "done") {
        setRenderId(null);
        refresh();
      } else if (update.status === "failed") {
        setRenderId(null);
        setError(update.error || "render failed");
      }
    });
  }, [renderId, refresh]);

  const patchClip = useCallback(
    (clipId, patch) => {
      api
        .updateClip(clipId, patch)
        .then((updated) =>
          setJob((prev) =>
            prev
              ? { ...prev, clips: prev.clips.map((c) => (c.id === updated.id ? updated : c)) }
              : prev,
          ),
        )
        .catch(fail);
    },
    [fail],
  );

  function exportKept() {
    api
      .renderJob(jobId)
      .then((render) => setRenderId(render.id))
      .catch(fail);
  }

  function back() {
    setJobId(null);
    setJob(null);
    setRenderId(null);
  }

  let body;
  if (jobId === null || job === null) {
    body = <SourcePicker onStarted={(created) => setJobId(created.id)} onError={fail} />;
  } else if (job.status !== "done") {
    body = <Progress job={job} onBack={back} />;
  } else {
    body = (
      <Review
        job={job}
        onPatchClip={patchClip}
        onExport={exportKept}
        exporting={renderId !== null}
        onBack={back}
      />
    );
  }

  return (
    <div className="shell">
      <div className="topbar">
        <span className="wordmark">
          street<span>clip</span>
        </span>
        {job && <span className="source">{job.source_name}</span>}
        <span className="spacer" />
        {job && (
          <button className="btn ghost" onClick={back}>
            Sources
          </button>
        )}
      </div>
      {body}
      {error && <div className="error-toast">{error}</div>}
    </div>
  );
}
