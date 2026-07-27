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

export const listInputs = () => request("/api/inputs");
export const listWorkspaces = () => request("/api/workspaces");
export const getWorkspace = (id) => request(`/api/workspaces/${id}`);
export const renameWorkspace = (id, title) =>
  request(`/api/workspaces/${id}`, { ...json({ title }), method: "PATCH" });
export const createWorkspace = (path) => request("/api/workspaces", json({ path }));
export const renderWorkspace = (id) => request(`/api/workspaces/${id}/render`, { method: "POST" });

export function uploadWorkspace(file) {
  const form = new FormData();
  form.append("file", file);
  return request("/api/workspaces/upload", { method: "POST", body: form });
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
