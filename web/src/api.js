async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // Non-JSON error body; the status text is all we have.
    }
    throw new Error(detail);
  }
  return response.json();
}

const json = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const readSession = () => request("/api/session");
export const logIn = (email, password) =>
  request("/api/session", json({ email, password }));
export const listUsers = () => request("/api/users");
export const createUser = (email, password) =>
  request("/api/users", json({ email, password }));
export const approveUser = (id) =>
  request(`/api/users/${id}/approve`, { method: "POST" });
export const revokeUser = (id) =>
  request(`/api/users/${id}/revoke`, { method: "POST" });
export const disableUser = (id) =>
  request(`/api/users/${id}/disable`, { method: "POST" });
export const setQuotaUnlimited = (id, unlimited) =>
  request(`/api/users/${id}/quota`, json({ unlimited }));

export async function saveProviderKeys(groq, anthropic) {
  const response = await fetch("/api/session/keys", {
    ...json({ groq, anthropic }),
    method: "PUT",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "could not save provider keys");
  }
}

export async function deleteUser(id) {
  const response = await fetch(`/api/users/${id}`, { method: "DELETE" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "could not delete that account");
  }
}

export async function logOut() {
  const response = await fetch("/api/session", { method: "DELETE" });
  if (!response.ok) throw new Error("could not sign out");
}

export const listInputs = () => request("/api/inputs");
export const readDashboard = () => request("/api/dashboard");
export const readAdminDashboard = () => request("/api/admin/dashboard");
export const listWorkspaces = () => request("/api/workspaces");
export const getWorkspace = (id) => request(`/api/workspaces/${id}`);
export const renameWorkspace = (id, title) =>
  request(`/api/workspaces/${id}`, { ...json({ title }), method: "PATCH" });
export const createWorkspace = (path) => request("/api/workspaces", json({ path }));
export const renderWorkspace = (id) => request(`/api/workspaces/${id}/render`, { method: "POST" });

export function uploadWorkspace(file, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/workspaces/upload");
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onerror = () => reject(new Error("upload failed"));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(1);
        resolve(xhr.response);
        return;
      }
      reject(new Error(xhr.response?.detail || xhr.statusText || "upload failed"));
    };
    xhr.send(form);
  });
}

export const getTranscript = (id) => request(`/api/workspaces/${id}/transcript`);

/** Delete returns 204 with no body, so it can't go through request(). */
export async function deleteWorkspace(id) {
  const response = await fetch(`/api/workspaces/${id}`, { method: "DELETE" });
  if (!response.ok) throw new Error("could not delete that workspace");
}

export const updateClip = (id, patch) =>
  request(`/api/clips/${id}`, { ...json(patch), method: "PATCH" });

/** Subscribe to job progress. Returns an unsubscribe function. */
export function watchWorkspace(id, onUpdate) {
  const source = new EventSource(`/api/workspaces/${id}/events`);
  source.onmessage = (event) => onUpdate(JSON.parse(event.data));
  // The server closes the stream once a job settles; without this the browser
  // would reconnect forever against an endpoint that has nothing left to say.
  source.onerror = () => source.close();
  return () => source.close();
}
