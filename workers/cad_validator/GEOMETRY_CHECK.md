# Geometry checking (`check_geometry`)

This document covers the `check_geometry` Agent3D tool and the geometry-check
job infrastructure that backs it. It does not cover full CAD validation
(`validate_cad_job.py`) except where the two share primitives.

## What it does

`check_geometry` is an Agent3D tool with no caller-provided arguments. It
inspects the current edit-scoped candidate's *actual executed geometry* --
volume, bounding box, center of mass, solid/face/edge counts, the largest
planar faces with their inclination from horizontal, and how many edges still
meet at a corner -- and compares it against the candidate immediately before
the latest mutation. It never modifies CAD source.

The face census and the sharp-edge count exist because the rest of that list
describes extent rather than shape. A bounding box is identical for two parts
whose support faces differ by six degrees, and rounding four of a part's
forty-eight edges moves its volume by four hundredths of a percent -- both are
shipped defects the earlier vocabulary could not describe.

The result is deterministic geometric evidence, nothing more. For example, if
Agent3D attempts to cut a cavity but the result shows `volume delta = 0`,
unchanged bounding box, and unchanged solid/face count, that is evidence the
mutation had no geometric effect -- Agent3D decides what that means for its
plan; `check_geometry` only reports what changed.

## Why it is separate from full validation

Full CAD validation (`validate_cad_job.py`) is the final acceptance gate for
a committed candidate: it runs once per candidate, right before commit, and
its pass/fail result gates whether the edit is allowed to land. `check_geometry`
is a repeatable, lightweight probe Agent3D can call after any mutation,
*during* the agent loop, to get mid-loop feedback -- it never gates anything
and has no opinion on whether a candidate is acceptable.

Both share the same underlying geometry-measurement primitives
(`geometry_inspection.py`) and the same sandboxed subprocess execution model
(`subprocess_sandbox.py`), but they are entirely separate job types with
separate result schemas, so a change to one never changes the other's
behavior.

## Job flow

```
Agent3D calls check_geometry (no arguments)
  -> tool reads the current candidate text and computes its source hash
  -> tool calls the queue_geometry_check RPC with (edit_job_id, candidate_hash)
       - server resolves the candidate's storage path from edit_job_id alone
       - server resolves the immediately previous source hash from
         edit_jobs.last_checked_source_sha256, falling back to
         accepted_source_sha256/original_storage_path on the first check
       - server inserts a generation_jobs row (type = 'geometry_check')
       - server advances last_checked_source_sha256 to the new hash
  -> tool polls the generation_jobs row (bounded timeout, small backoff)
  -> the validation container's worker loop (cad_validation_worker.py) claims
     the job through the same claim_next_supported_generation_job RPC used
     for full validation
  -> geometry_check_job.py processes it:
       - re-downloads and re-hashes the current candidate to confirm it has
         not changed since the job was queued
       - reuses a cached GeometrySnapshot if one already exists for the exact
         (source_sha256, geometry_checker_version); otherwise executes the
         source in a sandboxed subprocess (geometry_check_runner.py) and
         persists the resulting snapshot
       - resolves the previous source's snapshot the same way
       - compares the two snapshots (geometry_comparison.py) and derives
         warnings
       - completes the job via the complete_geometry_check RPC
  -> tool reads the completed job, verifies the returned source hash still
     matches what it requested, and returns a structured CheckGeometryOutput
```

## Where it runs

All CadQuery/OpenCascade execution happens inside the `cad_validator`
container -- the same container and worker process that runs full CAD
validation. Agent3D's Python process never imports CadQuery or executes CAD
source directly; it only queues a job and polls for its result.

For the geometry layer's architecture -- how a build result becomes a native
B-rep artifact, what owns what, and how the pieces fit together -- see
[`geometry/README.md`](geometry/README.md). This document covers the
`check_geometry` job and tool contract specifically.

## GeometrySnapshot fields

Persisted in the dedicated `geometry_snapshots` table (created in
`supabase/migrations/20260712070000_geometry_snapshots.sql`, extended by
`..._geometry_snapshot_diagnostics.sql`,
`..._geometry_snapshot_shape_census.sql`,
`..._geometry_artifacts.sql`, and
`..._geometry_snapshot_surface_metrics.sql`). Each row is one immutable
measurement of one exact source version.

Every field is **derived from that version's native B-rep artifact** -- the
snapshot is an observation of the geometry, not the geometry itself.
`geometry_artifact_id` names the artifact it was derived from. See
[`geometry/README.md`](geometry/README.md).

Every name in `geometry.analyzer.SNAPSHOT_FIELDS` (re-exported as
`geometry_check_job._GEOMETRY_FIELDS`) is used verbatim as a column in both
directions, so a field with no column fails the whole insert
with `PGRST204` and takes the worker down with it.
`tests/test_geometry_check_migration.py` pins the two together.

- `source_storage_path`, `source_sha256` -- exactly which source version this
  row describes.
- `geometry_checker_version` -- the measurement logic's version; see caching
  below.
- `execution_ok` -- whether the source executed without raising, independent
  of whether the geometry it produced is valid.
- `geometry_valid` -- whether the produced geometry is usable (non-degenerate
  solids). `null` when `execution_ok` is `false`, since validity cannot be
  assessed for source that never finished executing.
- `geometry_artifact_id` -- the native B-rep artifact this row observed.
  `null` when the build produced no usable geometry or the artifact could not
  be persisted; a measurement outlives its artifact.
- `volume_mm3` -- cubic millimeters.
- `surface_area_mm2` -- total area of every face. Separates two shapes a volume
  delta calls unchanged: hollowing a part raises area sharply while barely
  moving volume.
- `bounding_box` -- `{"min": [x, y, z], "max": [x, y, z], "size": [x, y, z]}`
  in millimeters. Position is preserved, not just size, so an accidental
  translation is distinguishable from a size change.
- `center_of_mass` -- `[x, y, z]` in millimeters, same coordinate convention
  as the candidate model.
- `solid_count`, `face_count`, `edge_count`, `vertex_count` -- topology counts.
  These are diagnostic signals, not proof of semantic correctness.
- `planar_faces` -- the largest flat faces, biggest first, each as
  `{"normal": [x, y, z], "angle_from_horizontal_deg": d, "area_mm2": a,
  "centroid": [x, y, z]}`. The angle is derived here rather than left to the
  reader because it is the form a requirement is written in ("a 65-degree
  viewing angle"); a horizontal face reports 0 and a vertical face 90. The
  centroid is what distinguishes two parallel faces. Capped at
  `MAX_PLANAR_FACES`, so a complete list is one whose length equals
  `face_count - non_planar_face_count`.
- `non_planar_face_count` -- cylinders, fillets, lofts: the faces a planar
  census cannot describe.
- `sharp_edge_count` -- edges whose two faces meet at more than
  `SMOOTH_EDGE_TOLERANCE_DEG`. Reported alongside `edge_count` rather than
  instead of it because the two move in opposite directions: rounding an edge
  removes a corner by adding a face, so the total rises exactly when the
  corner count falls. `null` above `MAX_CENSUS_EDGES`, which is a different
  claim from zero.

## Source-hash binding

Every snapshot is tied to an exact `source_sha256`. A geometry result for
hash `A` is never returned as the result for hash `B`: the cache lookup keys
on `(source_sha256, geometry_checker_version)`, and the job's own current
source is always re-downloaded and re-hashed against the hash it was queued
with before anything downstream is trusted -- if they no longer match (the
candidate changed while the job was queued or running), the job is marked
`cancelled` rather than returning stale geometry as evidence.

## Immediate previous-candidate comparison

Given a sequence of mutations `A -> B -> C -> D` (where `A` is the accepted
source this edit started from), each `check_geometry` call compares the
*immediately preceding* checked state, not always `A`:

- First call after `A -> B`: compares `A -> B` (seeded from
  `edit_jobs.accepted_source_sha256` / `original_storage_path`).
- After another mutation, `B -> C`: compares `B -> C`, not `A -> C`.
- After `C -> D`: compares `C -> D`.

This is tracked by one column, `edit_jobs.last_checked_source_sha256`,
updated each time `queue_geometry_check` runs. If a candidate has genuinely
no previous state to compare against, the result has `previous_source_hash:
null` and `delta: null` rather than a fabricated comparison.

## Geometry-delta semantics

`GeometryDelta` fields (`geometry_comparison.py`) report `after - before`
(volume, counts) or an explicit boolean/distance for bounding box and center
of mass. Any field that could not be computed -- because one side lacks a
measurement -- is `null`, never a fabricated zero.

`sharp_edge_count` is the delta that says whether an edge treatment landed. A
fillet that reached every edge shows a large negative value here and a
*positive* `edge_count`; a fillet that reached one face's edges shows a
positive `edge_count` and roughly zero here. No delta is derived for
`planar_faces` -- a face census is compared by reading it, not by
differencing it. Tolerances
(`VOLUME_TOLERANCE_MM3`, `LENGTH_TOLERANCE_MM`,
`LARGE_BBOX_CHANGE_FRACTION`) are centralized as module constants in
`geometry_comparison.py` rather than scattered through the codebase.

Warnings are derived only from measured geometry, never from semantic IDs,
feature names, or plan/goal text:

- `NO_GEOMETRIC_CHANGE` -- nothing measurable changed.
- `GEOMETRY_BECAME_INVALID` -- geometry was valid before and is not valid (or
  did not execute) after.
- `NO_SOLIDS` -- the current geometry has zero solids.
- `SOLID_COUNT_CHANGED` -- the solid count changed.
- `LARGE_BOUNDING_BOX_CHANGE` -- any bounding-box axis size changed by more
  than `LARGE_BBOX_CHANGE_FRACTION` of its prior size.

## Caching / reuse behavior

Before executing CadQuery, `GeometryEngine` checks whether a
`geometry_snapshots` row already exists for the exact
`(source_sha256, geometry_checker_version)` pair and reuses it if so.
`geometry_artifacts` is keyed identically, so an artifact and its snapshot are
found or missed together.

Reuse now usually hits. Full CAD validation runs on every candidate mutation and
persists the geometry it produced, so by the time the agent calls
`check_geometry` the measurement generally exists already and the candidate is
not executed a second time. Geometry is a consequence of *building* a candidate,
not of asking about one.

`geometry_checker_version` (currently `3`, defined in `geometry/runtime.py`)
must be bumped whenever the measurement logic changes in a way that could
change its interpretation, so an old snapshot is never silently treated as
equivalent to a new one. Adding a measured field is exactly that case: it moved
to `2` when the planar-face census and sharp-edge count were added, and to `3`
when snapshots became derived observations of a persisted B-rep artifact and
gained `surface_area_mm2` / `vertex_count` -- each bump retiring every row
measured under the smaller vocabulary rather than serving one as if it answered
the new questions.

## Failure behavior

Every failure below reaches the agent as a `GEOMETRY_CHECK_FAILED` tool
failure carrying a `reason` in its details, not as a `ToolSuccess` with a
status field. `CheckGeometryOutput.status` is `Literal["completed"]`: a
result that exists describes geometry that was actually measured, and there
is no such thing as a successful check with nothing to report.

- Candidate source not found (never mutated / storage error):
  `reason: "source_not_found"`, without queuing a job.
- The candidate changed after the job was queued: the job completes with
  `status: "cancelled"` server-side; the tool reports `reason: "job_failed"`.
- CAD execution failure, geometry extraction failure, or any other job
  failure: `reason: "job_failed"` with a diagnostic
  `message`. For a CAD runtime exception, `geometry_check_runner.py` walks
  the traceback for the last frame belonging to the candidate's `model.py`
  (mirroring `cad_validation_runner.py`'s `exception_result`) and appends
  `(in <function_name>, model.py:<line>)` to the message -- without this, an
  exception raised deep inside one feature's dependency chain (e.g. a helper
  feature two calls below the one currently being edited) is easy to
  misattribute to the wrong feature, which can send the agent back to edit
  code that was never actually broken.
- The job does not reach a terminal state before the tool's bounded timeout
  (default 45s, well under the edit job's 300s lease): `reason: "timeout"`.
- A returned job whose `source_sha256` does not match what was requested is
  treated the same as a failure (`reason: "job_failed"`) -- it is never
  returned as evidence for the current candidate.

The `reason` is what preserves the specific cause for Agent3D to act on:
`AgentTool.run()` otherwise reduces any escaping exception to one generic
`TOOL_EXECUTION_FAILED` with no detail, so `_failure` raises
`ToolExecutionRejected` explicitly instead. Structured validator diagnostics
travel with it, which is what lets a static-safety rejection name the rule,
the function, and the line rather than only that something was wrong. Missing
candidate scope (`ToolExecutionContext.candidate_id` unset) is the one
precondition rejected before execution, via the standard
`TOOL_VALIDATION_FAILED` path.

## How Agent3D receives the result

`check_geometry` is registered like any other tool in
`workers/agent_3d/edit_worker.py::build_runtime()`. Once registered, the
existing agent loop (`EditWorkflowOrchestrator._run_agent_loop`) calls it like
any other tool and appends its result to the active step's observations --
there is no special-cased handling for this tool anywhere in the orchestrator
or prompts.

## Running the tests

```
python -m pytest tests/test_geometry_inspection.py \
  tests/test_geometry_comparison.py \
  tests/test_geometry_extraction.py \
  tests/test_geometry_artifact.py \
  tests/test_geometry_engine.py \
  tests/test_cad_geometry_check_job.py \
  tests/test_cad_validation_geometry_artifact.py \
  tests/test_cad_check_geometry_unit.py \
  tests/test_geometry_check_migration.py \
  tests/test_check_geometry_catalog.py \
  tests/test_cad_editor_repository.py -v
```

`tests/test_geometry_inspection.py`, `tests/test_cad_geometry_check_job.py`, and
`tests/test_cad_validation_geometry_artifact.py` execute real CadQuery models
(including real sandboxed subprocess runs), so they take longer than the rest of
the suite.

What each file covers is tabulated in
[`geometry/README.md`](geometry/README.md#testing).
