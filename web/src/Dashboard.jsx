import { useCallback, useEffect, useState } from "react";
import * as api from "./api";

const METRICS = [
  ["uploads", "Uploads received"],
  ["completed", "Processed"],
  ["processing", "In progress"],
  ["exports", "Exports created"],
];

export default function Dashboard({ onError, canCreate, onNew, onWorkspaces }) {
  const [metrics, setMetrics] = useState(null);

  const refresh = useCallback(() => {
    api.readDashboard().then(setMetrics).catch(onError);
  }, [onError]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <main className="home dashboard">
      <header className="hero">
        <div>
          <p className="eyebrow">Your account</p>
          <h1>Your processing overview</h1>
          <p>Track incoming recordings and move quickly into your next review.</p>
        </div>
        {canCreate && (
          <button className="btn primary" onClick={onNew}>
            Upload a recording
          </button>
        )}
      </header>

      <section className="metric-grid" aria-label="Processing totals">
        {METRICS.map(([key, label]) => (
          <article className="metric-card" key={key}>
            <strong>{metrics ? metrics[key] : "—"}</strong>
            <span>{label}</span>
          </article>
        ))}
      </section>

      <section className="dashboard-panel">
        <div>
          <p className="eyebrow">Workspace library</p>
          <h2>{metrics ? `${metrics.workspaces} total workspaces` : "Loading workspaces…"}</h2>
          <p>
            {metrics?.failed
              ? `${metrics.failed} workspace${metrics.failed === 1 ? "" : "s"} need attention.`
              : "Completed recordings and active jobs are all in one place."}
          </p>
        </div>
        <button className="btn" onClick={onWorkspaces}>
          View workspaces
        </button>
      </section>
    </main>
  );
}
