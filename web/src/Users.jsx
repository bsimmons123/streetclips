import { useCallback, useEffect, useState } from "react";
import * as api from "./api";

export default function Users({ currentUserId, onError }) {
  const [rows, setRows] = useState([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    return api.listUsers().then(setRows).catch(onError);
  }, [onError]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function act(promise) {
    setBusy(true);
    promise
      .then(refresh)
      .catch(onError)
      .finally(() => setBusy(false));
  }

  function add(event) {
    event.preventDefault();
    act(api.createUser(email, password));
    setEmail("");
    setPassword("");
  }

  function remove(user) {
    if (window.confirm(`Delete ${user.email} and all of their workspaces?`)) {
      act(api.deleteUser(user.id));
    }
  }

  return (
    <div className="home">
      <div className="home-head">
        <h1>Accounts</h1>
      </div>

      <form className="new-user" onSubmit={add}>
        <input
          type="email"
          placeholder="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
        />
        <input
          type="password"
          placeholder="password (8+ characters)"
          value={password}
          minLength={8}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
        <button className="btn primary" type="submit" disabled={busy}>
          Create
        </button>
      </form>

      <div className="file-list">
        {rows.map((user) => (
          <div key={user.id} className="file-row account-row">
            <span className="name">{user.email}</span>
            <span className="size">
              {user.disabled ? "disabled" : user.approved ? "approved" : "pending"}
              {user.is_admin && " · admin"}
            </span>
            {!user.approved && !user.disabled && (
              <button
                className="btn ghost"
                disabled={busy}
                onClick={() => act(api.approveUser(user.id))}
              >
                Approve
              </button>
            )}
            {user.approved && !user.is_admin && (
              <button
                className="btn ghost"
                disabled={busy}
                onClick={() => act(api.revokeUser(user.id))}
              >
                Revoke
              </button>
            )}
            {!user.is_admin && !user.disabled && (
              <button
                className="btn ghost"
                disabled={busy}
                onClick={() => act(api.disableUser(user.id))}
              >
                Disable
              </button>
            )}
            {!user.is_admin && user.id !== currentUserId && (
              <button
                className="btn danger"
                disabled={busy}
                onClick={() => remove(user)}
              >
                Delete
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
