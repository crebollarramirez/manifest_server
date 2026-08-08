# Geometry checking (`check_geometry`)

This document covers the `check_geometry` Agent3D tool and the geometry-check
job infrastructure that backs it. It does not cover full CAD validation
(`validate_cad_job.py`) except where the two share primitives.

## What it does

`check_geometry` is an Agent3D tool with no caller-provided arguments. It
inspects the current edit-scoped candidate's *actual executed geometry* --
volume, bounding box, center of mass, and solid/face/edge counts -- and
compares it against the candidate immediately before the latest mutation. It
never modifies CAD source.

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

## GeometrySnapshot fields

Persisted in the dedicated `geometry_snapshots` table (see
`supabase/migrations/20260808000000_add_geometry_check.sql`). Each row is one
immutable measurement of one exact source version:

- `source_storage_path`, `source_sha256` -- exactly which source version this
  row describes.
- `geometry_checker_version` -- the measurement logic's version; see caching
  below.
- `execution_ok` -- whether the source executed without raising, independent
  of whether the geometry it produced is valid.
- `geometry_valid` -- whether the produced geometry is usable (non-degenerate
  solids). `null` when `execution_ok` is `false`, since validity cannot be
  assessed for source that never finished executing.
- `volume_mm3` -- cubic millimeters.
- `bounding_box` -- `{"min": [x, y, z], "max": [x, y, z], "size": [x, y, z]}`
  in millimeters. Position is preserved, not just size, so an accidental
  translation is distinguishable from a size change.
- `center_of_mass` -- `[x, y, z]` in millimeters, same coordinate convention
  as the candidate model.
- `solid_count`, `face_count`, `edge_count` -- topology counts. These are
  diagnostic signals, not proof of semantic correctness.

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
measurement -- is `null`, never a fabricated zero. Tolerances
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

Before executing CadQuery, `geometry_check_job.py` checks whether a
`geometry_snapshots` row already exists for the exact
`(source_sha256, geometry_checker_version)` pair and reuses it if so.
`geometry_checker_version` (currently `1`, defined in `geometry_inspection.py`)
must be bumped whenever the measurement logic changes in a way that could
change its interpretation, so an old snapshot is never silently treated as
equivalent to a new one.

## Failure behavior

- Candidate source not found (never mutated / storage error): the tool
  returns `status: "source_not_found"` without queuing a job.
- The candidate changed after the job was queued: the job completes with
  `status: "cancelled"` server-side; the tool surfaces `status: "job_failed"`.
- CAD execution failure, geometry extraction failure, or any other job
  failure: the tool surfaces `status: "job_failed"` with a diagnostic
  `message`. For a CAD runtime exception, `geometry_check_runner.py` walks
  the traceback for the last frame belonging to the candidate's `model.py`
  (mirroring `cad_validation_runner.py`'s `exception_result`) and appends
  `(in <function_name>, model.py:<line>)` to the message -- without this, an
  exception raised deep inside one feature's dependency chain (e.g. a helper
  feature two calls below the one currently being edited) is easy to
  misattribute to the wrong feature, which can send the agent back to edit
  code that was never actually broken.
- The job does not reach a terminal state before the tool's bounded timeout
  (default 45s, well under the edit job's 300s lease): `status: "timeout"`.
- A returned job whose `source_sha256` does not match what was requested is
  treated the same as a failure (`status: "job_failed"`) -- it is never
  returned as evidence for the current candidate.

Every one of these is returned as a normal `ToolSuccess` with a specific
`status` field (mirroring the existing `IndexSearchTool`/`IndexGetFeatureTool`
`status: "unavailable"` pattern), not a generic tool failure -- this preserves
the specific reason for Agent3D to reason about, since `AgentTool.run()`
otherwise reduces any exception to one generic `TOOL_EXECUTION_FAILED`
message with no detail. Missing candidate scope
(`ToolExecutionContext.candidate_id` unset) is the one precondition rejected
before execution, via the standard `TOOL_VALIDATION_FAILED` path.

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
  tests/test_cad_geometry_check_job.py \
  tests/test_cad_check_geometry_unit.py \
  tests/test_geometry_check_migration.py \
  tests/test_check_geometry_catalog.py \
  tests/test_cad_editor_repository.py -v
```

`tests/test_geometry_inspection.py` and `tests/test_cad_geometry_check_job.py`
execute real CadQuery models (including real sandboxed subprocess runs), so
they take longer than the rest of the suite.
