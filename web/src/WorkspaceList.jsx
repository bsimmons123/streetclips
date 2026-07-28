import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { clock } from "./format";

const STATUS_LABEL = {
  queued: "queued",
  running: "analyzing",
  failed: "failed",
};

export default function WorkspaceList({ canCreate, onOpen, onNew, onError }) {
  const [rows, setRows] = useState(null);
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");

  const refresh = useCallback(() => {
    api.listWorkspaces().then(setRows).catch(onError);
  }, [onError]);

  useEffect(refresh, [refresh]);

  // One poll for the whole list rather than an SSE connection per row.
  const working = rows?.some((w) => w.status === "running" || w.status === "queued");
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [working, refresh]);

  function commitRename(id) {
    const title = draft.trim();
    setEditing(null);
    if (!title) return;
    api.renameWorkspace(id, title).then(refresh).catch(onError);
  }

  function startRename(workspace) {
    setDraft(workspace.title);
    setEditing(workspace.id);
  }

  function remove(workspace) {
    // Deleting takes the exported shorts with it, so say so.
    if (!window.confirm(`Delete "${workspace.title}" and its exported shorts?`)) return;
    api.deleteWorkspace(workspace.id).then(refresh).catch(onError);
  }

  return (
    <div className="home">
      <div className="home-head">
        <h1>Workspaces</h1>
        {canCreate && (
          <button className="btn primary" onClick={onNew}>
            + New recording
          </button>
        )}
      </div>

      {rows && rows.length === 0 && (
        <div className="empty-state">
          <strong>No workspaces yet</strong>
          <p>
            {canCreate
              ? "Upload a recording to begin finding clips."
              : "This account has read-only access and no existing workspaces."}
          </p>
          {canCreate && (
            <button className="btn primary" onClick={onNew}>
              Upload your first recording
            </button>
          )}
        </div>
      )}

      <div className="workspace-grid">
        {(rows || []).map((w) => (
          <div key={w.id} className="workspace-card">
            <button className="poster" onClick={() => onOpen(w.id)} aria-label={w.title}>
              {w.has_poster ? (
                <img src={`/api/workspaces/${w.id}/poster`} alt="" />
              ) : (
                <span className="poster-fallback" />
              )}
            </button>

            <div className="workspace-body">
              {editing === w.id ? (
                <input
                  className="rename"
                  autoFocus
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onBlur={() => commitRename(w.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") commitRename(w.id);
                    if (event.key === "Escape") setEditing(null);
                  }}
                />
              ) : (
                <button
                  className="workspace-title"
                  onClick={() => onOpen(w.id)}
                  onDoubleClick={() => startRename(w)}
                  title={w.title}
                >
                  {w.title}
                </button>
              )}

              <div className="workspace-meta">
                {w.status === "done" ? (
                  <>
                    <span>{clock(w.duration)}</span>
                    <span>{w.clip_count} clips</span>
                    <span>{w.kept_count} kept</span>
                    {w.rendered_count > 0 && <span>{w.rendered_count} exported</span>}
                  </>
                ) : (
                  <span className={w.status === "failed" ? "bad" : "working"}>
                    {STATUS_LABEL[w.status] || w.status}
                    {w.status === "running" && ` ${Math.round((w.progress || 0) * 100)}%`}
                  </span>
                )}
              </div>

              <div className="workspace-actions">
                <button className="btn ghost" onClick={() => startRename(w)}>
                  Rename
                </button>
                <button className="btn ghost" onClick={() => remove(w)}>
                  Delete
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
