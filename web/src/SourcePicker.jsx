import { useEffect, useRef, useState } from "react";
import * as api from "./api";
import { bytes } from "./format";

export default function SourcePicker({ onStarted, onError }) {
  const [inputs, setInputs] = useState(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(null);
  const fileInput = useRef(null);

  useEffect(() => {
    api.listInputs().then(setInputs).catch(onError);
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

  function upload(file) {
    setUploadProgress(0);
    start(api.uploadWorkspace(file, setUploadProgress)).finally(() =>
      setUploadProgress(null),
    );
  }

  function onDrop(event) {
    event.preventDefault();
    setOver(false);
    const file = event.dataTransfer.files[0];
    if (file) upload(file);
  }

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
                onClick={() => start(api.createWorkspace(file.path))}
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
              if (file) upload(file);
            }}
          />
          {uploadProgress !== null ? (
            <span className="upload-status">
              <span>{Math.round(uploadProgress * 100)}% uploaded</span>
              <span className="upload-bar" aria-hidden="true">
                <span style={{ width: `${uploadProgress * 100}%` }} />
              </span>
            </span>
          ) : (
            "Drop a video here, or click to choose one"
          )}
        </label>
      </div>

    </div>
  );
}
