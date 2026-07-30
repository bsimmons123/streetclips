import { useCallback, useEffect, useState } from "react";
import * as api from "./api";

const number = new Intl.NumberFormat();

function bytes(value) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** exponent).toFixed(exponent > 1 ? 1 : 0)} ${units[exponent]}`;
}

export default function AdminDashboard({ onError }) {
  const [data, setData] = useState(null);

  const refresh = useCallback(() => {
    api.readAdminDashboard().then(setData).catch(onError);
  }, [onError]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <main className="home dashboard admin-dashboard">
      <header className="hero">
        <div>
          <p className="eyebrow">Administration</p>
          <h1>Platform overview</h1>
          <p>Account-level processing, storage, and billing readiness.</p>
        </div>
        <button className="btn" onClick={refresh}>Refresh</button>
      </header>

      <section className="metric-grid" aria-label="Platform totals">
        {[
          [data?.accounts, "Total accounts"],
          [data?.active_accounts, "Active accounts"],
          [data?.workspaces, "Workspaces"],
          [data?.processing_minutes, "Processing minutes"],
        ].map(([value, label]) => (
          <article className="metric-card" key={label}>
            <strong>{value == null ? "—" : number.format(value)}</strong>
            <span>{label}</span>
          </article>
        ))}
      </section>

      <section className="admin-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">API usage</p>
            <h2>Usage by account</h2>
          </div>
          <span>{data ? bytes(data.storage_bytes) : "—"} uploaded</span>
        </div>
        <div className="usage-table" role="table" aria-label="Usage by account">
          <div className="usage-row usage-head" role="row">
            <span>Account</span><span>Workspaces</span><span>Minutes</span>
            <span>Exports</span><span>Storage</span>
          </div>
          {(data?.users ?? []).map((user) => (
            <div className="usage-row" role="row" key={user.id}>
              <span>
                <strong>{user.email}</strong>
                <small>{user.disabled ? "Disabled" : user.approved ? "Active" : "Pending"}</small>
              </span>
              <span>{number.format(user.workspaces)}</span>
              <span>{number.format(user.processing_minutes)}</span>
              <span>{number.format(user.exports)}</span>
              <span>{bytes(user.storage_bytes)}</span>
            </div>
          ))}
          {data && data.users.length === 0 && <p className="empty-table">No accounts yet.</p>}
        </div>
      </section>

      <section className="admin-section billing-section">
        <div>
          <p className="eyebrow">Billing</p>
          <h2>Billing provider</h2>
          <p>{data?.billing.message ?? "Checking billing configuration…"}</p>
        </div>
        <span className={`status-pill ${data?.billing.configured ? "connected" : ""}`}>
          {data?.billing.configured ? "Connected" : "Not connected"}
        </span>
      </section>
    </main>
  );
}
