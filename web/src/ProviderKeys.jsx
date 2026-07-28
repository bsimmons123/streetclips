import { useState } from "react";
import * as api from "./api";

export default function ProviderKeys({ configured, onError, onSaved }) {
  const [groq, setGroq] = useState("");
  const [anthropic, setAnthropic] = useState("");
  const [busy, setBusy] = useState(false);

  function submit(event) {
    event.preventDefault();
    setBusy(true);
    api
      .saveProviderKeys(groq, anthropic)
      .then(onSaved)
      .catch(onError)
      .finally(() => setBusy(false));
  }

  return (
    <main className="key-setup">
      <div className="key-card">
        <p className="eyebrow">Personal provider keys</p>
        <h1>{configured ? "Update your API keys" : "Connect your API providers"}</h1>
        <p>
          Your keys are encrypted before storage and used only for your processing jobs.
          Server administrator keys are never used for non-admin accounts.
        </p>
        <form onSubmit={submit}>
          <label htmlFor="groq-key">Groq API key</label>
          <input
            id="groq-key"
            type="password"
            autoComplete="off"
            value={groq}
            onChange={(event) => setGroq(event.target.value)}
            placeholder="gsk_…"
            required
          />
          <label htmlFor="anthropic-key">Anthropic API key</label>
          <input
            id="anthropic-key"
            type="password"
            autoComplete="off"
            value={anthropic}
            onChange={(event) => setAnthropic(event.target.value)}
            placeholder="sk-ant-…"
            required
          />
          <button className="btn primary" disabled={busy}>
            {busy ? "Encrypting…" : "Save keys securely"}
          </button>
        </form>
        <p className="privacy-note">
          For security, saved key values are never displayed again.
        </p>
      </div>
    </main>
  );
}
