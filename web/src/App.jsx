import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import AdminDashboard from "./AdminDashboard";
import Dashboard from "./Dashboard";
import Login from "./Login";
import Pending from "./Pending";
import ProviderKeys from "./ProviderKeys";
import Review from "./Review";
import SourcePicker from "./SourcePicker";
import Users from "./Users";
import WorkspaceList from "./WorkspaceList";

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
  const [session, setSession] = useState(undefined);
  const [page, setPage] = useState("workspaces");
  const [jobId, setJobId] = useState(null);
  const [job, setJob] = useState(null);
  const [renderId, setRenderId] = useState(null);
  const [error, setError] = useState(null);
  const [picking, setPicking] = useState(false);

  const fail = useCallback((err) => setError(err.message || String(err)), []);

  const refreshSession = useCallback(() => {
    return api
      .readSession()
      .then(setSession)
      .catch(() => setSession(null));
  }, []);

  useEffect(() => {
    refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    if (session?.id) setPage("dashboard");
  }, [session?.id, session?.is_admin]);

  const refresh = useCallback(() => {
    if (jobId === null) return Promise.resolve();
    return api.getWorkspace(jobId).then(setJob).catch(fail);
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
    return api.watchWorkspace(jobId, (update) => {
      setJob((prev) => (prev ? { ...prev, ...update } : prev));
      if (update.status === "done") refresh();
    });
  }, [jobId, refresh]);

  // Render job lives alongside the analysis job; when it settles, the clip rows
  // have rendered paths worth refetching.
  useEffect(() => {
    if (renderId === null) return;
    return api.watchWorkspace(renderId, (update) => {
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
      .renderWorkspace(jobId)
      .then((render) => setRenderId(render.id))
      .catch(fail);
  }

  function back() {
    setJobId(null);
    setJob(null);
    setRenderId(null);
    setPicking(false);
  }

  function signOut() {
    api
      .logOut()
      .then(() => {
        back();
        setPage("workspaces");
        setSession(null);
      })
      .catch(fail);
  }

  if (session === undefined) {
    return (
      <div className="progress">
        <p className="stage">checking session…</p>
      </div>
    );
  }
  if (session === null) return <Login onSignedIn={setSession} />;
  if (!session.approved) {
    return (
      <Pending email={session.email} onRefresh={refreshSession} onSignOut={signOut} />
    );
  }
  let body;
  if (page === "keys" && !session.is_admin) {
    body = (
      <ProviderKeys
        configured={session.keys_configured}
        onBack={() => setPage("workspaces")}
        onError={fail}
        onSaved={() => refreshSession().then(() => setPage("workspaces"))}
        onSignOut={signOut}
      />
    );
  } else if (page === "dashboard") {
    body = (
      <Dashboard
        onError={fail}
        canCreate={session.is_admin || session.keys_configured}
        onNew={() => {
          setPage("workspaces");
          setPicking(true);
        }}
        onWorkspaces={() => setPage("workspaces")}
      />
    );
  } else if (page === "admin" && session.is_admin) {
    body = <AdminDashboard onError={fail} />;
  } else if (page === "users" && session.is_admin) {
    body = <Users currentUserId={session.id} onError={fail} />;
  } else if (jobId === null && picking) {
    body = (
      <SourcePicker
        onStarted={(created) => {
          setPicking(false);
          setJobId(created.id);
        }}
        onError={fail}
      />
    );
  } else if (jobId === null) {
    body = (
      <WorkspaceList
        canCreate={session.is_admin || session.keys_configured}
        onOpen={setJobId}
        onNew={() => setPicking(true)}
        onError={fail}
      />
    );
  } else if (job === null) {
    body = (
      <div className="progress">
        <p className="stage">loading…</p>
      </div>
    );
  } else if (job.status !== "done") {
    body = <Progress job={job} onBack={back} />;
  } else {
    body = (
      <Review
        canExport={session.is_admin || session.keys_configured}
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
        <button
          className="wordmark"
          onClick={() => {
            back();
            setPage("dashboard");
          }}
        >
          street<span>clip</span>
        </button>
        {job && <span className="source">{job.title || job.source_name}</span>}
        <nav className="main-nav" aria-label="Main navigation">
            <button
              className={page === "dashboard" ? "active" : ""}
              onClick={() => {
                back();
                setPage("dashboard");
              }}
            >
              Dashboard
            </button>
            <button
              className={page === "workspaces" ? "active" : ""}
              onClick={() => {
                back();
                setPage("workspaces");
              }}
            >
              Workspaces
            </button>
            {!session.is_admin && (
              <button
                className={page === "keys" ? "active" : ""}
                onClick={() => setPage("keys")}
              >
                API keys
              </button>
            )}
            {session.is_admin && (
              <button
                className={page === "admin" ? "active" : ""}
                onClick={() => {
                  back();
                  setPage("admin");
                }}
              >
                Admin
              </button>
            )}
            {session.is_admin && (
              <button
                className={page === "users" ? "active" : ""}
                onClick={() => {
                  back();
                  setPage("users");
                }}
              >
                Accounts
              </button>
            )}
        </nav>
        <span className="spacer" />
        <span className="source account-email">{session.email}</span>
        <button className="btn ghost" onClick={signOut}>
          Sign out
        </button>
        {page === "workspaces" && (job || picking) && (
          <button className="btn ghost" onClick={back}>
            Workspaces
          </button>
        )}
      </div>
      {body}
      {error && <div className="error-toast">{error}</div>}
    </div>
  );
}
