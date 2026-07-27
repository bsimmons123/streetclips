import { useEffect, useRef, useState } from "react";
import * as api from "./api";
import { bytes } from "./format";

export default function SourcePicker({ onStarted, onError }) {
  const [inputs, setInputs] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const fileInput = useRef(null);

  useEffect(() => {
    api.listInputs().then(setInputs).catch(onError);
    api.listJobs().then(setJobs).catch(onError);
  }, [onError]);

  async function start(work) {
    setBusy(true);
    try {
      onStarted(await work);
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  }

  function onDrop(event) {
    event.preventDefault();
    setOver(false);
    const file = event.dataTransfer.files[0];
    if (file) start(api.uploadJob(file));
  }

  const analyzed = jobs.filter((job) => job.kind === "analyze");

  return (
    <div className="picker">
      <h1>Find the moments worth posting.</h1>
      <p className="lede">
        Point streetclip at a recording. It transcribes, reads the whole thing, and hands back
        ranked candidates for you to approve.
      </p>

      <div className="block">
        <div className="section-label">From the input folder</div>
        <div className="file-list">
          {inputs && inputs.files.length > 0 ? (
            inputs.files.map((file) => (
              <button
                key={file.path}
                className="file-row"
                disabled={busy}
                onClick={() => start(api.createJob(file.path))}
              >
                <span className="name">{file.name}</span>
                <span className="size">{bytes(file.size)}</span>
              </button>
            ))
          ) : (
            <div className="empty">
              {inputs ? (
                <>
                  Nothing here yet. Drop videos into <code>{inputs.dir}</code> and reload.
                </>
              ) : (
                "Loading…"
              )}
            </div>
          )}
        </div>
      </div>

      <div className="block">
        <div className="section-label">Or upload</div>
        <label
          className={over ? "dropzone over" : "dropzone"}
          onDragOver={(event) => {
            event.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
        >
          <input
            ref={fileInput}
            type="file"
            accept="video/*"
            disabled={busy}
            onChange={(event) => {
              const file = event.target.files[0];
              if (file) start(api.uploadJob(file));
            }}
          />
          {busy ? "Uploading…" : "Drop a video here, or click to choose one"}
        </label>
      </div>

      {analyzed.length > 0 && (
        <div className="block">
          <div className="section-label">Recent</div>
          <div className="job-history">
            {analyzed.slice(0, 8).map((job) => (
              <button key={job.id} onClick={() => onStarted(job)}>
                <span className="name">{job.source_name}</span>
                <span className="size">{job.status}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
