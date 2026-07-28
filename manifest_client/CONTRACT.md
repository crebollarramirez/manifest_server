# Frontend–Backend Interface Contract

**Scope:** Requirements for the frontend preview layer (v1). Verified against `supabase/functions/cad-agent/index.ts`, all current migrations, `workers/cad_exporter/run_export_job.py`, and `supabase/config.toml`.

## 1. Frontend scope assumptions

Each part in a project renders independently in a three.js viewer. No combined-scene placement or inter-part transforms are required in v1. Per part, the frontend requires: the artifact file(s) to load, a mechanism to determine export readiness, and a mechanism to download artifacts from the private `3dProjects` bucket.

## 2. Verified current state

- **Export formats require no changes.** CAD exports produce `model.step` and `model.stl`; mesh exports produce `model.stl` and `model.glb`. The frontend will load STL for CAD parts and GLB for mesh parts. STL carries no material data; the frontend applies materials client-side.
- **Artifact paths are deterministic:** `<project_id>/exports/<part_id>/model.*`. No path-recording change is required.
- **`get_edit_job` exposes `export_job_id`,** making the edit-to-export chain fully traceable.
- **Direct PostgREST access is not viable.** All tables have row-level security enabled with no policies defined; anon and authenticated clients are denied all reads. Any frontend read path must therefore go through the `cad-agent` Edge Function.

## 3. Identified gap

No mechanism exists to check export-job status or retrieve completed artifacts. `export_part` returns only a `job_id`; read actions exist for `index_jobs` (`get_index_job`) and `edit_jobs` (`get_edit_job`) but not for `generation_jobs`, and the private bucket prevents direct file access.

## 4. Required addition: `get_export_job` action

A read-only action following the existing `get_index_job` pattern, reading `generation_jobs`.

**Request:**

```json
{ "action": "get_export_job", "job_id": "<uuid>" }
```

**Response fields (bounded):** `id, project_id, part_id, type, status, source_sha256, error_message, result, created_at`.

When `status = "completed"` for `export_cad` or `export_mesh`, the response must additionally include signed URLs for the part's artifacts, minted via the service-role client (`createSignedUrl`) against the deterministic paths above. A TTL of approximately one hour is sufficient.

```json
{
  "status": "completed",
  "job": { ... },
  "artifacts": [
    { "file": "model.stl", "url": "<signed-url>" },
    { "file": "model.glb", "url": "<signed-url>" }
  ]
}
```

**Estimated scope:** ~60 lines in `index.ts` following the `get_index_job` pattern, plus one entry in the `Action` union and handler map. No migrations, worker changes, or RLS work. `completed_at` is intentionally excluded, as `generation_jobs` has no such column; `status` and `created_at` are sufficient.

### 4.1 Optional: `get_part_artifacts`

An action accepting `part_id` and returning the latest terminal export job for that part in the same response shape. This removes the need for the frontend to persist job IDs across sessions. This is a convenience only; `get_export_job` alone unblocks all v1 work.

## 5. Frontend-side conventions (informational)

The following conventions will be adopted by the frontend and are documented here for visibility. No backend changes are requested; objections should be raised before implementation.

- **Artifact version/cache key:** `source_sha256` for CAD artifacts; `job_id` for mesh artifacts. Mesh exports are not hash-bound (`export_part` passes a null hash for mesh parts and `run_mesh_export_job` performs no hash verification). This asymmetry is assumed to be intentional; if mesh hash-binding is added later, the convention will be revisited.
- **Part identity in chat responses:** `chat` on an established linked CAD part returns `part_id: null`. The frontend tracks the part ID it submitted and reads `resolved_part_id` from `get_edit_job`. *(Corrected by Amendment A1.)*
- **Superseded exports:** an export job cancelled due to a source-hash mismatch is treated as "refetch latest artifact," not as an error state.
- **Committed-but-unexported edits:** a completed edit whose export failed or was not queued displays the previous artifact alongside a manual re-export affordance calling `export_part`.
- **Authentication:** the frontend API client will be structured to accept a session token later, given that `verify_jwt = false` is a documented temporary state and Supabase Auth is enabled in configuration.

## 6. Open questions

1. Are there any objections to `get_export_job` as specified in section 4, including the signed-URL response field? This is the sole blocking item for frontend data-layer work.
2. Does CAD generation currently assign color or appearance data to solids, or is output geometry-only? Non-blocking; this determines whether authored (non-cosmetic) color is ultimately a rendering-side or generation-side concern.

---

## Amendments

Confirmed deviations between this contract and the code, found during frontend implementation. Each entry cites the evidence. The original text above is preserved unmodified.

### A1 (2026-07-28) — `chat` echoes the sent `part_id`; it is not null for established CAD edits

§5 states `chat` on an established linked CAD part returns `part_id: null`. The handler returns `part_id: part?.id ?? null` (`index.ts:1415`) — the **sent** part id is echoed back; `part_id` is null only for project-scoped chats where no `part_id` was submitted. The frontend convention is unchanged (track the submitted id; treat `get_edit_job.job.resolved_part_id` as the sole authoritative identity for project-scoped edits), but the response field must not be documented as always-null.

### A2 (2026-07-28) — mesh `chat` is not idempotent

`client_request_id` is accepted but unused on the mesh path and is absent from the mesh response (`index.ts:1466-1473`); the OpenAI call and source overwrite happen synchronously before the response. The frontend must never auto-retry a mesh `chat` request; retries are explicit user actions only.

### A3 (2026-07-28) — the one-active-edit guard is part-scoped only

The unique index is on `(project_id, resolved_part_id) where resolved_part_id is not null and status in ('queued','running')` (`20260725000000_add_cad_edit_workflow.sql:78-81`). Client-side send-disabling therefore applies only to part-scoped chats with a live edit job. Project-scoped chats submit with a null target; conflicts there resolve backend-side during target resolution and surface as job-level failures, not submission 409s.

### A4 (2026-07-28) — `max_rows = 1000` is the PostgREST global cap, not an API parameter

`config.toml:22` sets PostgREST's `max_rows = 1000`. `list_parts` has no pagination parameters; a project with more than 1000 parts would be **silently truncated**. Virtualized lists remain required frontend-side; server-side pagination is a future backend need.

### A5 (2026-07-28) — canonical enums from migrations (single source for frontend types)

`edit_jobs.state` is exactly 21 values (`20260726000000_add_initial_cad_design.sql`): `received, ensuring_index, resolving_target, retrieving_context, planning_edit, validating_plan, applying_edit, planning_initial_design, planning_initial_repair, applying_initial_design, validating_candidate, classifying_error, retrieving_repair_context, planning_repair, applying_repair, committing, reindexing, queueing_export, completed, failed, cancelled`. Note `planning_initial_repair` is a **repair** state (UI phase "Checking the result"), not an initial-design state. `edit_job_events.event_type` is a 21-value enum with non-empty `message` ≤ 500 chars and object `metadata` (`20260727000000_add_cad_agent_progress_and_tools.sql:21-62`).

### A6 (2026-07-28) — artifact content types are nonstandard

Uploads use `model/step`, `model/stl`, `model/gltf-binary` (`run_export_job.py:131-303`). Geometry decoding must key off the requested filename/format, never the response `Content-Type`.
