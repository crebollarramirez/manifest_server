# 3D Agent microservice

`workers/agent_3d` is the Python CAD-agent runtime. Implemented today: goal
creation, read-only semantic planning, a plan-driven `Agent3D` reasoning loop
that executes CAD tools against an edit-scoped candidate, and -- once every
plan step completes -- validating that candidate, committing it to canonical
source with a hash-guarded write, reindexing, and queuing export (see
"Current agent-loop mode" below and `AGENT_REASONING.md`). A repair loop on
validation failure, the concrete `ToolPlan`/`CadToolExecutor` stage sections
5-6 describe, and post-completion storage cleanup are not implemented --
sections 4-6 and 9 below describe that remaining target design, not current
behavior.

`services/cad_agent` is the lightweight NestJS control plane. Nest validates
WebSocket payloads and project/part linkage, creates idempotent `edit_jobs`, returns the job
ID immediately, and serves status/progress. It does not claim CAD jobs, create
CAD goals or plans, expose CAD mutation contracts to OpenAI, execute CAD tools,
or wait for a job to finish. Linked mesh generation remains a separate NestJS
path and does not enter this worker.

This separation allows multiple `agent-3d` containers to process independent
jobs without increasing the amount of work performed by the API process.

## System boundary

```text
Client
  │  cad.edit.submit
  ▼
NestJS control plane
  │  validate linkage + submit_cad_edit_job
  ▼
Supabase edit_jobs queue and edit_job_events journal
  │  atomic lease claim
  ▼
Python CAD Editor
  ├─ goal creation
  ├─ read-only semantic planning
  ├─ bounded source-context preparation
  ├─ concrete ToolPlan reasoning
  ├─ local deterministic tool execution
  ├─ validator/indexer/exporter coordination
  └─ hash-guarded canonical commit
```

The database is the durable handoff. Nest and Python do not depend on a
long-lived request or an in-memory connection between them. A WebSocket may
disconnect at any time; the job continues, and the client can replay ordered
events from its last acknowledged sequence.

## Current agent-loop mode

A worker currently:

1. claims and starts the durable job;
2. creates and checkpoints the structured goal;
3. ensures the semantic index needed by the planning read tools is fresh;
4. creates and checkpoints the high-level `CadPlan`;
5. copies accepted CAD source into an edit-scoped candidate, backing up the
   pre-edit canonical source alongside it;
6. runs the plan-driven agent loop until every plan step is completed;
7. validates the final candidate, commits it to canonical source, reindexes,
   and queues export;
8. atomically completes the job with `outcome: "committed"`.

Step 6 is the loop documented in `AGENT_REASONING.md`: for the earliest
unfinished step, `Agent3D` is asked for one decision, its requested tool calls
are executed against the candidate, and the results become that step's
observations. When the agent calls `request_step_completion` the orchestrator
marks the step completed and advances. The orchestrator is the only writer of
plan state, and **the goal is never mutated**. Plan revision -- adding,
replacing, or reordering steps -- is not implemented; only step status changes.

The loop is bounded by ten turns per step and 24 turns overall, and a decision
requesting no tool call fails the job with `AGENT_NO_ACTION`. It also refuses
to let a step complete when that step addresses a required criterion but the
candidate still has no CAD features, and `edit_cad_build_model` refuses to
wire in a function that doesn't exist in the candidate yet -- see
`AGENT_REASONING.md`.

CAD tools change source only within
`{project}/candidates/cad/{part}/{edit_job_id}/model.py`; canonical source is
never written while the loop runs. Step 7 (detailed in `AGENT_REASONING.md`)
only starts once every plan step is completed -- a loop that fails on a turn
limit or `AGENT_NO_ACTION` never reaches it, so nothing gets committed.
Validation and reindex failures fail the job outright (`VALIDATION_FAILED`,
`REINDEX_FAILED`, rolling canonical back to its pre-edit backup first); export
failure only adds a warning to the terminal result. On success the terminal
result's `changed_files` is the canonical part path, `source_sha256` is the
committed hash, and `index_job_id`/`export_job_id` are real job IDs.

`CAD_EDITOR_PLANNING_ONLY` is vestigial -- no code reads it.

### Planning and reasoning debug logs

After a high-level plan is checkpointed, the worker writes one text file per
job to `workers/agent_3d/logs/<edit_job_id>.txt`. The file contains:

- job, project, requested/resolved part, and workflow identifiers;
- the original request and bounded conversation;
- the complete structured goal JSON;
- the complete high-level plan JSON;
- a UTC generation timestamp.

The `write()` call above uses a temporary file followed by an atomic
replacement, and the final file is created with owner-only `0600`
permissions. A reclaimed job rewrites the same file instead of creating
mixed or duplicate logs. The terminal result includes the absolute
`planning_log_path`. Failure to write it terminates the job with
`PLANNING_LOG_WRITE_FAILED` before any CAD execution.

The agent loop's own execution -- every model request/response and tool
call, not just the goal/plan snapshot above -- is recorded separately in the
**structured agent trace**, `workers/agent_3d/logs/<edit_job_id>.trace.jsonl`
(one append-only JSONL file per job, not per turn). See "Agent trace" in
`AGENT_REASONING.md` for the event types, correlation fields, failure
severity, and how to inspect one turn with `jq`. It replaces what used to be
a per-turn, per-step, overwritten-each-turn prompt snapshot file with a
complete, ordered, machine-readable record of the whole job.

Both log kinds share the same directory: git-ignored (requests and
conversations may be sensitive), visible on the host via the Compose bind
mount, and relocatable in a multi-host deployment with
`CAD_EDITOR_LOG_DIRECTORY`.

## Request admission

The WebSocket gateway accepts:

```json
{
  "project_id": "project UUID",
  "part_id": "optional linked CAD-part UUID",
  "request_text": "Make the mounting holes 6 mm",
  "messages": [
    {"role": "user", "content": "Make the mounting holes 6 mm"}
  ],
  "client_request_id": "optional idempotency UUID"
}
```

Nest verifies that the project exists and that a linked part belongs to that
project and has type `cad`. It then calls `submit_cad_edit_job`. The RPC creates
the queued row and sequence-1 `job.queued` event, or returns the existing row
when the same `client_request_id` and request fingerprint are submitted again.
Reusing that ID for different content is rejected.

Nest responds with `cad.edit.accepted`, including the durable job ID. It does
not inspect CAD source to choose a workflow. Exact-blank initial-design
detection is a Python CAD-domain decision and is rechecked against storage when
the job runs.

## Worker leasing and horizontal scaling

Every Python replica has a unique `CAD_EDITOR_WORKER_ID`. If no ID is supplied,
the process creates one. A worker calls `claim_next_edit_job`, whose
`FOR UPDATE SKIP LOCKED` claim returns one queued job or one running job whose
lease expired. Only one replica can claim a given row.

While a job runs, a heartbeat extends its lease. State updates, private history
checkpoints, and public progress appends are scoped to the current worker ID
and unexpired lease. If a worker crashes, another instance can claim the job
after expiration and resume from persisted checkpoints. If an old instance
continues after losing its lease, guarded mutations fail with `EDIT_LEASE_LOST`.

Scale the worker independently from Nest:

```bash
docker compose \
  --env-file workers/agent_3d/.env \
  -f workers/agent_3d/docker-compose.yml \
  up --build --scale agent-3d=4
```

Each worker handles one claimed job at a time. More replicas increase job
parallelism; they do not divide a single CAD job across processes. The
validator, indexer, and exporter retain their own queues and may be scaled
separately for CPU-heavy work.

A linked `part_id` is reserved on the queued row before any replica performs a
model call. A second non-idempotent request for the same active part receives
`PART_EDIT_IN_PROGRESS` and should be retried after the first job finishes.
Replicas therefore add useful parallelism across independent parts/projects,
not concurrent mutation of one part. Unlinked requests cannot be reserved until
Python resolves them, so clients should avoid intentionally submitting several
overlapping unlinked edits at once.

## End-to-end job lifecycle

Sections 1-3 describe current behavior. Execution then runs the agent loop
("Current agent-loop mode" above), then validates, commits, reindexes, and
queues export for the loop's final candidate before returning the terminal
result -- sections 7 and 8 below (minus the repair sub-loop) now describe real
behavior, ported from the pre-Python-rewrite TS orchestrator
(`git show HEAD:services/cad_agent/src/orchestrator.service.ts`).

Sections 4-6, 9, and the "Plans, contracts, and source of truth" / "Impact and
dependency safety" sections below still describe the **target design** and are
largely not implemented. Of section 4, the candidate copy and the
original-source backup are real; the bounded source inventory and blank-part
`initial_design` classification are not. Sections 5-6 are entirely
unimplemented: `agent_3d/tool_contracts.py` and `CadToolExecutor`, cited
below, do not exist in this repository, and the agent loop replaces the
concrete-ToolPlan stage they describe. Section 7's validation is real; its
repair sub-loop (diagnostic-driven replanning, up to three attempts) is not --
a failed validation fails the job outright. Section 9's terminal-result
reporting is real; its cleanup of temporary candidate/backup storage objects
is not. Treat the remaining unimplemented material as a design spec for
future work, not a description of current behavior.

### 1. Claim and start

`edit_worker.py` polls `edit_jobs`, claims one row, starts a lease heartbeat,
and passes the row to `EditWorkflowOrchestrator`. The orchestrator emits
`job.started`; all later public milestones are persisted in
`edit_job_events`.

### 2. Build the goal

The goal creator receives only the original request. It produces a strict
`CadGoalDefinition` describing what must become true, not how to edit code.
Python then supplies authoritative metadata:

- a new `goal_id` UUID;
- sequential criterion IDs `GC-1`, `GC-2`, and so on;
- the original request;
- required-versus-preserve criteria;
- explicit constraints, assumptions, and clarification state.

An empty criterion list is legal only when clarification is required. The
validated goal is checkpointed in `edit_jobs.history`, so a reclaimed worker
does not make another goal call. A required clarification ends the job with
`CLARIFICATION_REQUIRED`, including the bounded question and reason; planning
and mutation do not continue on an ambiguous goal.

### 3. Create the high-level semantic plan

After the index is fresh, Python resolves one authoritative part before the
planning agent runs. Linked jobs retain their requested part. Unlinked jobs use
the existing deterministic confidence and score-margin rules; missing or
ambiguous targets fail before any model-controlled tool arguments are accepted.

The planning agent receives the authoritative goal and can call exactly two
read-only tools through the strict Python tool framework:

- `index_search` searches sanitized feature metadata in the resolved part;
- `index_get_feature` returns one feature's role, relevant parameter names and
  types, direct dependencies, and direct dependents.

Neither tool returns Python source, storage credentials, default expressions,
line positions, or mutation capabilities. Project, part, run, repository, and
candidate identity come from server-owned `ToolExecutionContext`; they are not
present in the LLM argument schemas. Target bindings are retained only when
their exact semantic IDs were observed in successful tool results.

Every callable tool inherits `AgentTool`, declares one canonical API-safe
`tool_id`, a version and description, and dedicated frozen strict Pydantic input
and output models. `ToolRegistry` validates definitions at worker startup,
`Toolbox` derives the native OpenAI definitions from those classes, and
`ToolExecutor` resolves the exact returned name and invokes only the final
`AgentTool.run()` pipeline. The pipeline validates input, runs optional domain
validation, executes the concrete behavior, validates output, and returns a
shared `{ok, data}` or `{ok, error}` envelope. No aliases or provider-specific
tool-name translations exist.

The planner may use at most eight tool-call rounds. Its final `CadPlan` has a
worker-owned `plan_id`, the goal ID, version 1, sequential `PS-1` steps,
dependencies that reference earlier steps only, and `pending` status on every
new step. Collectively, the steps must address every goal criterion. This plan
describes outcomes; it does not contain source edits or executable tool calls.
It is also stored as a private durable checkpoint.

### 4. Prepare authoritative CAD context

The deterministic executor resolves the target against accepted storage and a
fresh semantic index. A linked part is authoritative. An unlinked request must
produce a single high-confidence indexed target; ambiguous requests fail
instead of editing an arbitrary part.

For established source, context preparation builds a bounded inventory of:

- exact source chunks for relevant features;
- stable semantic IDs;
- model parameters;
- direct and transitive dependency information;
- reverse dependents and parameter consumers;
- allowed target IDs and SHA-256 fingerprints;
- deletable CAD-agent-owned targets;
- build-model and feature metadata;
- source path and accepted-source hash.

If a linked part contains exactly the system runtime import and nothing else,
Python changes the job to `initial_design`. After that classification is
persisted, a resume accepts only the same exact blank marker; a mismatch raises
`INITIAL_SOURCE_CHANGED`. If source changes before the first classification,
Python evaluates the source that actually exists as an established edit.

Before mutation, the accepted source is copied to the job's original-source
path and hash-verified. That backup is used only for guarded rollback. This
part is real: `_prepare_candidate` does it on first bootstrap, alongside
`edit_jobs.accepted_source_sha256`.

### 5. Generate a concrete ToolPlan

The reasoner receives the request, conversation, validated goal, high-level
plan, bounded source context, attempt number, and any repair diagnostics. It
returns strict ToolPlan schema version 2 from the Pydantic contract in
`agent_3d/tool_contracts.py`.

The high-level `CadPlan` answers “what outcomes are needed?” The concrete
`ToolPlan` answers “which bounded source operations produce those outcomes
against this exact source hash?” Keeping them separate makes user intent
auditable while retaining an atomic execution transaction.

For an established model, the plan must target the resolved part and current
base hash. It contains at most 12 registered operations and an exact
`impact_review`. For an initial design, it must contain exactly one
`write_initial_model` operation and an empty impact review. Whole-model
replacement is never allowed for established source.

The exact plan is checkpointed before execution. A worker recovering after an
LLM call reuses it rather than generating an untraceable replacement.

### 6. Execute tools locally and atomically

`CadToolExecutor` validates the ToolPlan again and invokes its registered
Python implementations directly. There is no Nest-to-Python tool-call round
trip and no nested queue required for new jobs. Every operation is applied in
memory against the same base source; only a completely successful transaction
uploads one candidate.

Supported operations are:

- `confirm_no_change`;
- `write_initial_model`;
- `replace_parameter_field`;
- `update_cad_part_metadata`;
- `replace_function_body`;
- `replace_cad_feature_body`;
- `add_model_parameter`;
- `add_private_helper`;
- `add_cad_feature`;
- `replace_build_model_body`;
- `delete_model_parameter`;
- `delete_private_helper`;
- `delete_cad_feature`.

Target IDs and fingerprints are checked against the source inventory.
Operations cannot add imports, edit another part, delete required runtime
contracts, or silently overwrite human-owned regions. Added parameters,
helpers, and features receive explicit ownership markers.

`confirm_no_change` must be the only operation. It includes semantic evidence
whose feature fingerprints are independently verified. A valid no-change
result completes immediately without candidate upload, validation, commit,
reindex, or export.

### 7. Validate and repair

Validation is real (`_validate_candidate`); repair is not -- a failed
validation raises `VALIDATION_FAILED` and fails the job.

A changed candidate is written under the job-specific candidate prefix and
re-read to verify its SHA-256. The orchestrator queues the independent CAD
validator with the exact candidate path, hash, job ID, and attempt.

Validation proof is accepted only when all of these match:

- the validation child completed successfully;
- its type and source kind identify candidate CAD validation;
- `edit_job_id` is the current job;
- `source_storage_path` is the current candidate path;
- `source_sha256` is the current candidate hash;
- the structured report says `status: passed` and `valid: true`.

Repairable tool-preflight or validation failures may produce a new concrete
plan. Validation repair receives the exact prior ToolPlan, failed candidate
source and hash, structured report, and diagnostic codes. There are at most
three attempts total. Non-repairable diagnostics or an exhausted limit finish
the job as failed.

### 8. Guarded commit, reindex, and export

This section is real (`_commit_candidate`, `_rollback_commit`,
`_reindex_after_commit`, `_queue_export_after_commit`).

Immediately before commit, the candidate is hash-verified again. Canonical
source must still have the accepted base hash. If it already has the candidate
hash during recovery, commit is treated as idempotently complete; any third
hash is a `SOURCE_CHANGED` conflict.

After writing, canonical source is re-read and must equal the candidate hash.
A mandatory project reindex then proves that the accepted source still
produces a fresh semantic index. If reindexing fails, rollback is allowed only
when canonical source still equals this job's candidate and the original
backup still equals the accepted hash.

Export is best-effort after commit and reindex. Failure to enqueue export is
returned as a warning rather than undoing a valid CAD edit.

### 9. Finish and clean up

The terminal-result reporting in this section is real (`_complete_agent_run`);
the cleanup is not -- temporary `attempt-{n}`/`original` candidate objects are
left in storage after a job finishes, whether it completed or failed.

The terminal result records the resolved target, attempt count, changed files
and symbols, source hash, validation report, index/export job IDs, `goal`,
`high_level_plan`, and warnings. One database transaction changes the terminal
row and appends `job.completed` or `job.failed`, so clients cannot observe a
terminal event for a still-running row.

Temporary candidate and backup objects are removed only after terminalization.
Cleanup failure is written to worker logs and the in-process return value; it
does not rewrite the already durable result. If automatic rollback is attempted
but cannot be proven safe, original and candidate artifacts are retained for
manual recovery.

## Plans, contracts, and source of truth

Implemented today:

- `planning/agent_contracts.py` defines goals, high-level plans, IDs,
  sequencing, and cross-field validation;
- `tools/index/index_tools.py` defines the strict planning-tool input and
  output contracts together with their concrete behavior;
- `failures.py` contains `WorkflowFailure`, the shared expected-failure type.

Pydantic generates the JSON Schema supplied to OpenAI and validates the parsed
response for every implemented stage (goal, plan, `Agent3D` reasoning).

**Not implemented yet** (target design): a `tool_contracts.py` defining an
executable ToolPlan envelope, operation arguments, no-change evidence, schema
versions, and impact review, with the same model validating plans immediately
before execution so the model-facing schema and execution boundary can't
drift. Nest intentionally has no duplicate Zod copy of CAD goals, plans, or
tools regardless; it knows only the submission, public job, and progress
transport contracts.

Behavioral rules that cannot be expressed by JSON Schema are meant to remain
deterministic Python checks: part scope, source hashes, target fingerprints,
provenance, dependency impact, initial-versus-established mode, candidate
proof, and commit preconditions -- none of this exists yet either.

## Impact and dependency safety (target design, not implemented)

ToolPlan version 2 requires `impact_review` to cover exactly the independently
derived impact set. This includes directly edited features, consumers of
changed parameters, and transitive dependents. Missing, extra, duplicated, or
contradictory decisions are rejected.

`depends_on` lists immediate geometry producers. Context preparation derives
transitive paths and reverse relationships. Static `params.<field>` analysis
finds effective parameter consumers, including fields read through private
helpers. Decorator parameter metadata must agree with those effective reads.

Create-versus-update semantics are also checked before editing: an existing
semantic feature must be replaced rather than added; a missing feature must be
added rather than replaced; every added feature must be called by the same
plan's `build_model` replacement.

## Checkpoints and crash recovery

`edit_job_events` is the ordered public progress journal. `edit_jobs.history`
is the private workflow journal; today it contains the validated goal and
high-level plan checkpoints. The owner-guarded history RPC appends atomically
instead of performing a read-modify-write race.

On lease recovery, the orchestrator examines the durable row and history:

- existing goal or plan checkpoints skip duplicate LLM calls;
- a persisted candidate is reloaded and reused untouched (`_prepare_candidate`);
- an existing validation child is polled rather than duplicated, as long as
  the candidate hash matches what the prior attempt validated -- `edit_jobs.
  current_candidate_sha256` (set by the validation RPC itself) is compared
  against the fresh candidate hash to decide whether to reuse the last
  attempt number or start a new one;
- canonical source already equal to the candidate is handled idempotently at
  commit (`_commit_candidate`).

The rest of this list is target design, not implemented: a persisted ToolPlan
reused for its attempt (no `ToolPlan` exists in the agent-loop design), and
validation/repair feedback resuming against the exact failed source/context
(there is no repair loop). A resume that lands between a successful commit and
a completed reindex re-queues reindexing rather than detecting the prior
attempt is still in flight.

The cutover migration drops the historical `cad_tool_jobs` table and its RPCs.
New requests are claimed directly from `edit_jobs`, and the Python orchestrator
handles them in process (mutation execution, once implemented, will not
reintroduce a nested tool-call queue). The migration refuses to run while
queued or running legacy CAD work exists; see the drain procedure below.

## Progress delivery

Python appends every milestone to `edit_job_events`; it does not publish into a
Nest in-memory subject. The Nest WebSocket gateway polls the durable journal for
active subscriptions and fans out new sequences. One database RPC handles up to
100 subscribed jobs per batch with an independent cursor and bounded page per
job, preventing a noisy subscription from starving another. Terminal jobs are
automatically removed from the polling set.

Replay and live delivery therefore use the same database source of truth:

1. subscription reads the job's event high-water mark;
2. persisted events through that mark are sent in `cad.edit.snapshot`;
3. polling begins after the delivered sequence;
4. reconnecting with `after_sequence` replays only missed events.

## Prompts and model selection

Four CAD prompts exist today under `workers/agent_3d/planning/prompts`:

- `goal-creation.md`;
- `planning.md`;
- `cad-system.md`;
- `agent-reasoning.md`.

A concrete ToolPlan-reasoning prompt, an initial-design prompt, an edit-plan
prompt, and a repair prompt are target design (see "End-to-end job lifecycle"
above) and do not exist yet.

The goal stage uses `OPENAI_GOAL_MODEL`, then `OPENAI_MODEL`, then
`gpt-5.4-mini`. The semantic planning stage uses `OPENAI_PLANNING_MODEL`, then
`OPENAI_MODEL`, then the same default. Agent3D reasoning (see
`AGENT_REASONING.md`) uses `OPENAI_AGENT_MODEL`, then `OPENAI_MODEL`, then the
same default, and combines `agent-reasoning.md` with `cad-system.md` as its
model instructions. Prompts are loaded once per process; restart workers after
changing them.

The separate mesh prompt remains in Nest because linked mesh requests still
use the Nest mesh-generation path.

## Security and trust boundaries

- OpenAI receives bounded JSON context, never Supabase credentials.
- The model cannot choose storage paths or commit canonical source.
- Planning tools are read-only and return sanitized semantic metadata.
- Pydantic rejects unknown fields at every agent contract.
- The executor independently derives targets, fingerprints, and dependency
  impact rather than trusting model claims.
- Candidate code is validated by the separate CAD validator before commit.
- Storage paths are constrained to the project, part, job, and attempt.
- Every workflow database mutation and child-queue write is checked against the
  active worker and the database's lease clock.
- Storage writes are constrained by job-specific paths; canonical commit and
  rollback use SHA-256 compare-and-verify guards and idempotent recovery.

## Failure behavior

Expected workflow failures use stable codes and bounded public messages.
Examples include:

- `AI_REFUSAL`, invalid goal/plan output, or planning tool-round exhaustion;
- missing, stale, malformed, or ambiguous semantic index context;
- invalid ToolPlan shape, scope, target, fingerprint, or impact review;
- candidate/path/hash mismatch;
- dependency timeout or lost lease;
- validation proof mismatch or non-repairable validation;
- source conflict before commit;
- reindex failure and guarded rollback failure.

Unexpected exceptions become `WORKFLOW_INTERNAL_ERROR`; the detailed traceback
is retained only in private diagnostics/logging. A failed job never commits an
unvalidated candidate.

## Configuration

Create `workers/agent_3d/.env` for the Compose stack:

```dotenv
SUPABASE_SERVICE_ROLE_KEY=replace-with-local-service-role-key
OPENAI_API_KEY=replace-with-openai-key

# Optional
SUPABASE_URL_DOCKER=http://host.docker.internal:54321
OPENAI_MODEL=gpt-5.4-mini
OPENAI_GOAL_MODEL=gpt-5.4-mini
OPENAI_PLANNING_MODEL=gpt-5.4-mini
OPENAI_AGENT_MODEL=gpt-5.4-mini
CAD_EDITOR_PLANNING_ONLY=true
# Optional; defaults to workers/agent_3d/logs
CAD_EDITOR_LOG_DIRECTORY=workers/agent_3d/logs
CAD_AGENT_PORT=3000
CAD_AGENT_EVENT_POLL_INTERVAL_MS=500
CAD_EDITOR_JOB_POLL_INTERVAL_SECONDS=2
CAD_EDITOR_DEPENDENCY_POLL_INTERVAL_SECONDS=0.5
CAD_EDITOR_DEPENDENCY_TIMEOUT_SECONDS=300
CAD_EDITOR_LEASE_SECONDS=300
```

`CAD_EDITOR_WORKER_ID` is optional and normally omitted so each replica creates
a unique ID. Configure it only when an external scheduler supplies a unique,
stable instance identity.

Nest still receives `OPENAI_API_KEY` and `OPENAI_MODEL` in this combined stack
because mesh generation remains there. CAD goal, plan, and ToolPlan calls use
the Python worker's environment.

## Deployment cutover

Migration `20260802000000_move_cad_runtime_to_editor.sql` is intentionally a
drain-only boundary. The old Nest orchestrator and `tool_worker.py` cannot hand
an in-flight process-memory plan to the new Python orchestrator.

For an existing deployment:

1. stop CAD request admission, the old Nest CAD-job claimant, and every legacy
   CAD tool worker;
2. let queued/running rows in both `edit_jobs` and `cad_tool_jobs` finish, or
   cancel them explicitly;
3. apply the migration; it fails with
   `CAD_EDITOR_CUTOVER_REQUIRES_DRAIN` if either queue is still active;
4. deploy the lightweight Nest control plane and one or more new
   `agent-3d` replicas;
5. re-enable request admission.

The migration removes the nested tool queue and unguarded progress/child-queue
entry points. Do not run old and new CAD orchestrators concurrently against the
post-cutover schema.

## Running locally

Apply all Supabase migrations. The editor coordinates with the separate
indexer, validator, and exporter queues, so start those workers as well:

```bash
docker compose --env-file workers/agent_3d/.env \
  -f workers/indexer/docker-compose.yml up -d --build
docker compose --env-file workers/agent_3d/.env \
  -f workers/cad_validator/docker-compose.yml up -d --build
docker compose --env-file workers/agent_3d/.env \
  -f workers/cad_exporter/docker-compose.yml up -d --build
```

Then start the control plane and editor:

```bash
docker compose \
  --env-file workers/agent_3d/.env \
  -f workers/agent_3d/docker-compose.yml \
  up --build
```

Nest listens on port `3000` by default unless the Compose port is overridden:

- `POST /v1/cad-agent/actions` for catalog and manual operations;
- `GET /v1/cad-edits/:jobId?after_sequence=0` for durable status/replay;
- `ws://127.0.0.1:3000/v1/cad-edits/ws` for CAD/mesh submission and progress.

Run the Python worker directly from the repository root with
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `OPENAI_API_KEY` defined:

```bash
python workers/agent_3d/edit_worker.py
```

## Verification

From the repository root:

```bash
python -m unittest tests.test_cad_editor_agent
python -m unittest tests.test_cad_editor_orchestration
python -m unittest tests.test_cad_editor_worker
python -m unittest tests.test_cad_tool_framework
python -m unittest tests.test_cad_editor_cutover_migration
python -m unittest tests.test_agent_3d
python -m unittest tests.test_agent_trace
python -m unittest discover -s tests -p 'test_*.py'
```

For the Nest control plane:

```bash
cd services/cad_agent
npm test
npm run build
```

The most important implementation files are:

- `workers/agent_3d/edit_worker.py` — edit-job polling and lease heartbeat;
- `agent_3d/orchestrator.py` — durable planning-only workflow (goal through the
  high-level plan);
- `agent_3d/failures.py` — `WorkflowFailure`, the package's shared expected-
  failure type;
- `agent_3d/planning/goal_creator.py` — strict goal creation;
- `agent_3d/planning/planning_agent.py` — high-level plan and read-tool loop;
- `agent_3d/planning/resolver.py` — natural-language request to CAD-part
  resolution (`ResolvedEditTarget`);
- `agent_3d/planning/agent_contracts.py` — goal and high-level plan source of
  truth;
- `agent_3d/planning/prompts/` — all CAD reasoning prompts;
- `agent_3d/agent_3d.py` — `Agent3D`, the MVP one-turn reasoning decision (see
  `AGENT_REASONING.md`);
- `agent_3d/tools/` — canonical tool interface, registry, toolbox, executor,
  strict contracts, and the mutating/read-only agent tools themselves;
- `agent_3d/repository.py` — Supabase jobs, events, storage, and child queues.
