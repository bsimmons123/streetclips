# Workspaces, transcript review, and a home screen

Date: 2026-07-27

## Problem

streetclip already persists every analysis: a job row, its clips, a report, and
a `data/job_NNNNN/` directory. But the app presents that as a "Recent" list at
the bottom of an upload page. There is no home, nothing is nameable or
deletable, and the operator cannot tell one session from another at a glance.

Two further gaps:

- The **full word-level transcript is stored and never shown**. The review pane
  displays only a per-clip `excerpt`. Burned-in captions on export work; there
  is nothing readable in the app.
- Every analysis leaves ~125 MB of intermediate audio on disk forever. Twenty
  sessions is several gigabytes of files nothing reads again.

## Approach

A workspace *is* an analyze job. No new container type, no schema for grouping.
The work is surfacing what already exists and making the transcript usable.

Rejected: a workspace holding multiple scoring passes over one video, and a
workspace holding many videos. Both add a layer the operator did not ask for
and neither addresses the actual complaint, which is that the app has no home.

## Data model

`jobs` stays the table name — it is genuinely a work queue, and render jobs
live in it too. Three columns are added:

| Column | Type | Purpose |
|---|---|---|
| `title` | `TEXT` | Operator-supplied name. Null means fall back to `source_name`. |
| `duration` | `REAL NOT NULL DEFAULT 0` | Denormalized from the report at completion. |
| `poster_path` | `TEXT` | Frame grabbed at analyze time. |

**Why `duration` is denormalized:** `report_json` contains the entire
transcript — roughly 1-2 MB for a 65-minute session. The home list must not
parse that per row to render a runtime.

Clip counts (total, kept, rendered) are computed with an aggregate over
`clips`, not stored. They change on every keep/skip and denormalizing them
would mean invalidation logic for no gain at this scale.

## API

Analyze jobs are exposed as `workspaces`, because that is the noun the operator
sees. Render jobs remain internal and keep their existing queue semantics.

```
GET    /api/workspaces                   list + counts, never the transcript
GET    /api/workspaces/{id}              detail + clips
PATCH  /api/workspaces/{id}              rename ({title})
DELETE /api/workspaces/{id}              rows + directory
GET    /api/workspaces/{id}/events       SSE progress (unchanged behavior)
GET    /api/workspaces/{id}/source       ranged video (unchanged behavior)
GET    /api/workspaces/{id}/transcript   {words, segments} only
GET    /api/workspaces/{id}/poster       image/jpeg
POST   /api/workspaces/{id}/render       enqueue a render of selected clips
```

This renames the existing `/api/jobs` routes. The SPA is the only consumer, so
the change is mechanical, but it does churn the existing API tests. The naming
is the point of the feature — leaving the URLs as `jobs` would keep the API
describing a queue while the UI describes a workspace.

`GET /api/workspaces` returns per row: `id`, `title`, `source_name`, `status`,
`stage`, `progress`, `duration`, `clip_count`, `kept_count`, `rendered_count`,
`created_at`, `has_poster`.

The transcript endpoint returns words and segments only — not the media block,
not the candidates. Those are already on the detail response.

## Home screen

The workspace list becomes the landing page. Each row carries poster, title,
duration, and counts, with a live status line for anything still analyzing.
Rename is inline. Delete is behind a confirm, because it destroys rendered
exports.

`+ New recording` opens the existing input-directory picker and dropzone as a
panel rather than occupying the entire first screen.

## Transcript panel

Replaces the static excerpt in the review pane.

**It renders a window, not the session.** The clip's words plus roughly 30
seconds either side. Eleven thousand word spans in the DOM would be slow to
render and the operator would never scroll them.

Interactions:

- Current word highlights during playback, driven by the existing `timeupdate`
  handler.
- Click a word to seek to it.
- Drag across words to set the clip's in and out points. Bounds snap to word
  edges, which is what the existing `snap()` already enforces server-side. The
  `PATCH` fires on release, not during the drag.
- The nudge buttons stay, for adjustment finer than a word.

## Cleanup

`work/audio.wav` is deleted when analysis finishes. It is 125 MB per session,
regenerable from the source in seconds, and nothing downstream reads it —
`signals.compute_energy` runs during analysis only, and rendering works from
the source video.

Only that file is purged, not the `work/` directory, which the renderer reuses
for temporary crop and caption files.

Deleting a workspace removes its directory and its rows (clips cascade). An
uploaded source under `data/uploads/` is removed only when no other workspace
references that path.

## Testing

| Area | Tests |
|---|---|
| `db` | title falls back to `source_name`; rename persists; count aggregate is correct across kept/rendered states; delete cascades clips |
| `worker` | `audio.wav` is purged after a successful analysis; **a render still succeeds after the purge**; poster is written |
| `api` | list shape and counts; rename; delete removes the directory; delete leaves a shared upload alone; transcript returns words without the report; poster 404s when absent |

The purge-then-render test is the important one — it is the only thing standing
between this change and silently breaking exports.

The web app has no test runner. The transcript interaction is verified in a
browser rather than standing up vitest as part of this change.

## Out of scope

- Multiple scoring passes per workspace
- Grouping workspaces into projects
- Searching the transcript, or creating a clip from an arbitrary passage
- A vitest suite for the SPA
