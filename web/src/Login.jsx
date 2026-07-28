import { useState } from "react";
import * as api from "./api";

export default function Login({ onSignedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    api
      .logIn(email, password)
      .then(onSignedIn)
      .catch((err) => setError(err.message))
      .finally(() => setBusy(false));
  }

  return (
    <form className="login" onSubmit={submit}>
      <h1>
        street<span>clip</span>
      </h1>
      <label className="section-label" htmlFor="email">
        Email
      </label>
      <input
        id="email"
        type="email"
        autoComplete="username"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        required
      />
      <label className="section-label" htmlFor="password">
        Password
      </label>
      <input
        id="password"
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        required
      />
      {error && <p className="login-error">{error}</p>}
      <button className="btn primary" type="submit" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
