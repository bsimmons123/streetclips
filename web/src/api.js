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
export const listJobs = () => request("/api/jobs");
export const getJob = (id) => request(`/api/jobs/${id}`);
export const createJob = (path) => request("/api/jobs", json({ path }));
export const renderJob = (id) => request(`/api/jobs/${id}/render`, { method: "POST" });

export function uploadJob(file) {
  const form = new FormData();
  form.append("file", file);
  return request("/api/jobs/upload", { method: "POST", body: form });
}

export const updateClip = (id, patch) =>
  request(`/api/clips/${id}`, { ...json(patch), method: "PATCH" });

/** Subscribe to job progress. Returns an unsubscribe function. */
export function watchJob(id, onUpdate) {
  const source = new EventSource(`/api/jobs/${id}/events`);
  source.onmessage = (event) => onUpdate(JSON.parse(event.data));
  // The server closes the stream once a job settles; without this the browser
  // would reconnect forever against an endpoint that has nothing left to say.
  source.onerror = () => source.close();
  return () => source.close();
}
