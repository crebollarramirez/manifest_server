# Agent3D reasoning design

## Purpose

`Agent3D` (`agent_3d.py`) is the model-driven decision component of the CAD
editing workflow. It does not own workflow state and does not execute tools
-- it turns one workflow-state snapshot into one next-action decision.

## Reasoning context priority

Every reasoning turn weighs its inputs in this order:

```text
Goal
    ↓
Plan
    ↓
Active step
    ↓
Current-step observations
    ↓
Recent conversation
    ↓
Available tools
```

The structured goal and plan are authoritative. Conversation is supporting
context only -- it resolves references like "make those bigger" or "same as
before," but it never overrides the structured goal or plan.

## One-turn reasoning

```text
workflow snapshot
    ↓
Agent3D.decide(...)
    ↓
one next action
```

`decide()` makes exactly one model call and returns its raw response
unchanged -- its `function_call` output items *are* the decision, so no
parallel decision type exists. It never executes what it decides.

## The plan-driven loop

The plan is the engine. `EditWorkflowOrchestrator._run_agent_loop()` repeats
one turn at a time:

```text
active step = earliest step still pending or in progress
    ↓ (none left -> loop ends)
Agent3D.decide(goal, plan, active step, recent messages, step observations)
    ↓
orchestrator executes each requested tool call via ToolExecutor
    ↓
results appended to this step's observations
    ↓
agent called request_step_completion?  -> mark step completed, advance
    ↓
repeat
```

`request_step_completion` is gated, not automatically honored: if the active
step addresses a `required` completion criterion and the candidate currently
has zero `@cad_part` features, the orchestrator rejects the call with
`STEP_REQUIRES_A_FEATURE` and feeds that back as an observation instead of
advancing the step -- so an agent that searches, finds nothing, and declares
victory anyway gets a concrete correction on its next turn rather than
silently completing a step that built nothing. The check only looks at
feature *presence* (not `ModelParams` field count, not `build_model` wiring)
and only applies to `required` criteria -- a step addressing only `preserve`
criteria is never gated. It's self-limiting: once any feature exists, later
steps are never blocked by it, including when the gated step is the one that
ends up creating that first feature.

`request_step_completion` is also gated on **validation**: after the
deterministic gates pass, the orchestrator hands the candidate to the
independent CAD validator, and the step completes only if it passes. A
repairable failure keeps the step `in_progress` and feeds the validator's
diagnostics back on the same reasoning chain -- see "Step validation" below.

A step moves `pending → in_progress → completed`. Only the orchestrator
writes those statuses, and each transition is checkpointed to
`edit_jobs.history` as a `plan_updated` entry, so a replacement worker
resumes at the step the previous one reached. **The goal is never mutated by
anything.** The only plan revision that exists is appending a single repair
step after a failed final validation ("Final validation and bounded repair"
below); steps are never reordered, replaced, or deleted, and the agent
cannot request any of it.

The loop ends when no step is pending or in progress. Two caps bound it: at
most `MAX_STEP_TURNS` (20) turns on a single step, and a per-entry turn
budget of `MAX_AGENT_TURNS` (32) -- or `MAX_REPAIR_TURNS` (12) when the loop
is entered on a repair step -- raising `AGENT_STEP_TURN_LIMIT` /
`AGENT_TURN_LIMIT`. A decision containing no tool call fails fast with
`AGENT_NO_ACTION` rather than silently burning the budget or assuming the
step finished.

`edit_cad_build_model` rejects wiring `build_model` to call a function that
doesn't exist yet in the candidate (`undefined_function_call`) -- a tool-level
check, not an orchestrator gate, since it protects the candidate's structural
correctness regardless of caller. It exists because a failed `create_feature`
call followed by wiring the feature it would have created in anyway was an
observed failure mode: the wiring call used to succeed syntactically while
leaving `build_model` calling an undefined name. `agent-reasoning.md` also
tells the agent directly to resolve a failed tool call before doing anything
that depends on it, rather than routing around it.

Tool calls run against an edit-scoped **candidate** copy of the CAD source
(`{project}/candidates/cad/{part}/{edit_job_id}/model.py`), which the
orchestrator bootstraps from accepted source before the first turn and reuses
untouched on resume. Canonical source is never modified while the loop runs.

## Step validation

For the conceptual guide to both validation boundaries and the repair loop --
why they exist and how they fit together -- see
[`VALIDATION_AND_REPAIR.md`](VALIDATION_AND_REPAIR.md). This section and the
next are the mechanical detail.

Every normal step's completion is verified before it is honored:

```text
request_step_completion
    ↓
deterministic gates (STEP_REQUIRES_A_FEATURE)
    ↓ pass
candidate validation  (skipped when the bytes are already proven)
    ├── passed        -> step completed, proof retained
    ├── repairable    -> step stays in_progress, diagnostics returned
    └── infrastructure-> VALIDATION_FAILED, job fails
```

A repairable failure is *not* a state transition. The step stays
`in_progress` and the diagnostics come back as the
`CANDIDATE_VALIDATION_FAILED` result of the agent's own
`request_step_completion` call, on the same `previous_response_id` chain --
so the agent keeps everything it already established and simply corrects the
source. No new plan step, no new reasoning stage.

Three guards keep this from wasting model turns or validator runs:

- **Provably-redundant runs are skipped.** If the candidate's hash equals the
  hash of a validation that already passed -- a pure-discovery step that
  changed nothing -- no run is queued.
- **`STEP_VALIDATION_NO_CHANGE`.** Requesting completion again on bytes that
  just failed is rejected deterministically. This is load-bearing, not an
  optimization: the queueing RPC reuses the existing child for an unchanged
  `(path, hash)`, so re-validating would return the same failure instantly.
- **`MAX_STEP_VALIDATION_REJECTIONS` (2).** After two rejections the gate
  stands down and lets the completion through. Without this, a stubborn
  mid-plan step would burn `MAX_STEP_TURNS` and fail the whole job *before*
  ever reaching the final boundary, which is where repair lives -- strictly
  worse than having no step validation at all. Step validation is early
  feedback; the final gate is the authoritative one.

Set `CAD_EDITOR_STEP_VALIDATION=0` to disable only the step gate. The final
gate and the repair loop are unconditional.

The verdict of every run is written to `edit_jobs.history` as a
`candidate_validation` entry, so a replacement worker re-seeds the failing
hash, the rejection count, and the diagnostics rather than paying for them
again.

## Final validation and bounded repair

Once every step is completed, the whole candidate is validated. If the last
step's own gate already proved the current bytes, that proof is reused
instead of running an identical validation again.

```text
final validation
    ├── passed        -> commit
    ├── infrastructure-> VALIDATION_FAILED
    └── repairable
         ↓
       append ONE repair step (kind="repair")
         ↓
       ordinary agent loop, fresh chain, same 12 tools
         ↓
       request_step_completion -> full validation again
         ├── passed  -> repair step completed -> commit
         └── failed  -> stays in_progress, updated diagnostics returned
                        (up to MAX_REPAIR_ATTEMPTS, then
                         REPAIR_BUDGET_EXHAUSTED)
```

The repair step is appended by deterministic orchestrator code alone -- the
agent cannot request one, and `kind` lives on `PlanStepState`, a
`CadPlan`-only subclass, precisely so the planner is never asked to emit it.
It carries `addresses_criteria: []` (the normal plan already covers the
goal's criteria, and this also keeps the missing-geometry gate from treating
repair like a first-feature step) and `depends_on` the last existing step.

**One repair step holds every attempt.** A failed repair does not append
another step; it returns updated diagnostics and the same step keeps
working. `MAX_REPAIR_ATTEMPTS` (3) counts those attempts, rebuilt on resume
from the `candidate_validation` history rather than stored in a column, so it
cannot drift from what actually happened.

Three history entries are written per append, in this order:
`repair_step_appended` (the trigger, keyed by the failing hash) →
`candidate_validation` re-scoped to the new step → `plan_updated` **last**,
as the commit point. A worker that dies before that last write leaves a plan
with no repair step, so the resumed worker re-validates the same unchanged
bytes, gets the same verdict from the RPC's reuse branch, and appends
cleanly. A worker that dies after it finds the repair step already active and
simply runs it.

## After the loop: validate, commit, reindex, export

Once every plan step reaches a terminal status, the orchestrator hands the
final candidate to a commit pipeline that ports the pre-Python-rewrite TS
orchestrator's hash-guarded commit design (`git show
HEAD:services/cad_agent/src/orchestrator.service.ts`), now backed by RPCs and
schema (`edit_jobs.state`, `edit_job_events.event_type`) that were already
provisioned for it:

```text
proven candidate (from the final gate, or a reused step proof)
hash-guarded commit to canonical source
    ↓ canonical hash == accepted_source_sha256 -> read-verify-write-reverify
    ↓ canonical hash == candidate hash already -> idempotent no-op (resume)
    ↓ neither -> SOURCE_CHANGED, job fails, canonical untouched
mandatory reindex
    ↓ fails -> roll back canonical from its pre-edit backup, REINDEX_FAILED
best-effort export queue
    ↓ fails -> warning only, job still completes
outcome: "committed"
```

The agent loop's candidate lives at a stable path with no run concept, but
`queue_edit_candidate_validation_run`'s RPC enforces a `validation-{run}`
path server-side (`validation_run_count` on the job row) -- so each
validation copies the candidate's current content there first; the content,
and therefore the hash, is identical to what the loop produced. A run's
snapshot is written once and never rewritten, so a passing verdict keeps
proving the exact bytes it saw even after the agent edits the live candidate
again. That is what makes reusing a step's proof at commit time sound, and
`_commit_candidate` re-verifies the proof independently regardless of which
gate produced it.

Before the first agent turn, `_prepare_candidate` also backs up canonical
source unmodified to `.../original/model.py` and records its hash as
`edit_jobs.accepted_source_sha256`, so a reindex failure after commit can
restore exactly what was there before this job started.

Reindex failure fails the job outright. Validation failure now depends on its
classification: a *repairable* failure drives the step or repair loop above,
while an *infrastructure* failure (timeout, validator crash, a report that
does not describe our bytes) fails the job with `VALIDATION_FAILED` and is
never shown to the agent as if it were a CAD problem. Export failure is the
one non-fatal step: it only adds a warning to the terminal result.

### Classifying a validation verdict

`validate_candidate` returns one of three outcomes, in this evaluation order.
Identity is checked first, because a report that does not describe our bytes
tells us nothing about them -- its `repairable_hint` is meaningless and its
diagnostics point at code this job never wrote.

| Condition | Outcome |
|---|---|
| path / hash / `edit_job_id` mismatch | `infrastructure` |
| `completed` + `status: passed` + `valid: true` | `passed` |
| `completed` + `status: passed` + `valid` not true | `infrastructure` (validator defect) |
| `status: failed` + `repairable_hint: true` + `schema_version >= 2` + non-empty diagnostics | `repairable` |
| everything else | `infrastructure` |

One rule runs *after* that table: a verdict that came out `passed` is
downgraded to `repairable` when `build_artifacts.solid_count > 1`, carrying a
`DISCONNECTED_SOLIDS` diagnostic. **A part is exactly one connected printable
solid.** A project made of several physical pieces is several *parts*, each
with its own source, its own agent, and its own assembly interfaces — so a
part that silently split in two is a piece nothing downstream can see or bind.

The split of responsibility is deliberate: the validator *measures*
(`solid_count` comes straight from its report), and the orchestrator
*judges*, because "one connected solid" is this system's product invariant
rather than a general fact about valid CadQuery. A report with no measurement
at all is never failed on this rule — reports predating it must not fail a
candidate that otherwise validated.

This gate exists because of a real run that split into two solids at PS-4,
validated clean four times, and then spent its next step's entire turn budget
trying to fillet two disjoint bodies together.

`repairable_hint` is set by the validator itself: true for AST/decorator
failures, `BUILD_MODEL_RETURN_ERROR`, `GEOMETRY_BUILD_ERROR`, and CadQuery
runtime exceptions that are not import errors; false for `IMPORT_ERROR`,
`VALIDATION_TIMEOUT`, `VALIDATION_WORKER_ERROR`, hash mismatches, and worker
crashes. The `schema_version` and non-empty-diagnostics requirements are
deliberate: a schema-1 report has no `repairable_hint` at all, and a
repairable verdict with nothing to say would burn the agent's budget
reproducing the identical failure.

## Dynamic context

Every call rebuilds the context from scratch; `Agent3D` holds no state
between turns -- all loop state lives in the orchestrator:

- the goal and plan are supplied every turn, in full, never summarized (the
  plan reflects current step statuses);
- only the currently active step is included -- `Agent3D` is not asked to
  reason about the whole plan at once;
- at most 8 recent conversation messages are included, chronologically
  ordered -- a plain trailing slice (`messages[-8:]`), not a summary; fewer
  than 8 available messages are passed through as-is, since there is no
  padding logic;
- tool observations are scoped to the active step: they accumulate across
  that step's turns and are cleared the moment the active step changes, so a
  later step never inherits an earlier step's tool transcript;
- `validation_feedback` carries the diagnostics from a validation this
  candidate already failed -- this step's earlier attempt after a restart,
  or, for a repair step, the whole-plan failure that created it. It is a
  distinct field rather than a synthetic observation entry: `observations`
  means "tool calls you made", `_redundant_call_rejection` dispatches on
  `observation["tool_id"]`, and misrepresenting a validator verdict's
  provenance to the model is exactly how confused repair attempts start.
  Empty dict when there is none, so the shape stays stable;
- the tool catalog `Agent3D` was constructed with is passed through the
  model client's native tool-calling interface (`tools=...`), never
  duplicated as text inside the reasoning prompt;
- `cad-system.md` (the CadQuery source-authoring contract) is included in
  every call because CadQuery is currently the only supported modeling
  target. It is concatenated after the reasoning policy, unmodified --
  workflow reasoning and source-authoring rules stay two separate documents.

Every turn is recorded in the structured agent trace -- see "Agent trace"
below -- rather than a per-turn snapshot file.

## Agent trace

`workers/agent_3d/planning/agent_trace.py` (`AgentTraceWriter`) is debugging
infrastructure, not a chain-of-thought log: it records what actually
happened, not an approximation of it. One append-only JSONL file per edit
job, `logs/{edit_job_id}.trace.jsonl`, holds every event from both `Agent3D`
and the orchestrator for that job, interleaved in the order they occurred
(both hold the same injected `AgentTraceWriter` instance -- see
`edit_worker.py::build_runtime()`). A model response may include an
API-provided `reasoning_summary`; the trace never contains private reasoning
tokens, which the Responses API does not expose.

Event types, all sharing correlation fields (`edit_job_id`, and where
applicable `goal_id`, `plan_id`, `step_id`, `agent_turn`, `step_turn`):

- `llm.request` -- the **exact** dict `Agent3D.decide()` passes to
  `responses.create(**request)`, logged immediately before that call.
  `Agent3D` never reconstructs a separate diagnostic version of the prompt:
  the same `request` dict is both logged and sent, so the two cannot
  diverge. Also carries `instructions_sha256` /
  `reasoning_instructions_sha256` / `cad_system_prompt_sha256` /
  `tool_catalog_sha256` -- deterministic content hashes making two traces
  comparable without diffing full prompt text.
- `llm.response` -- the raw response object (serialized in full, not reduced
  to the selected tool call), plus promoted `response_id`, `status`,
  `input_tokens`, `output_tokens`, `reasoning_tokens`, `reasoning_summary`
  for quick filtering.
- `tool.started` / `tool.completed` -- one pair per requested tool call,
  correlated to the `llm.response` that requested it via `response_id` and
  to each other via `tool_call_id`. `tool.completed`'s `result` is the actual
  `ToolSuccess`/`ToolFailure` the candidate/canonical state changed on --
  including when the `STEP_REQUIRES_A_FEATURE` gate rewrites an otherwise-ok
  `request_step_completion` result into a failure, the trace reflects the
  rewritten result, not the original.
- `step.started` / `step.changed` / `step.completed` -- orchestrator-owned
  plan progress, with `previous_step_id` and observation counts.
- `validation.started` / `validation.completed` -- one pair per validation
  run, carrying `purpose` (`step` or `final`), `step_id`, `validation_run`,
  `source_sha256`, and on completion the `outcome`, `stage`, and
  `diagnostic_codes`. A run that was skipped as provably redundant emits
  neither, which is exactly the signal that it was skipped.
- `repair.appended` -- the orchestrator appending a repair step, with
  `step_id`, `repair_attempt`, the triggering `source_sha256`, and
  `diagnostic_codes`. Tool calls made *during* a repair stay ordinary
  `tool.started`/`tool.completed`; they are already correlated by `step_id`,
  so a separate repair-scoped tool event would only duplicate that.
- `agent_loop.completed` / `agent_loop.failed` -- the loop's terminal
  outcome. `agent_loop.completed` carries `validated`, whether the loop
  finished holding a proof for the candidate's current bytes.
  `agent_loop.failed` includes the `error_code` (`AGENT_TURN_LIMIT` /
  `AGENT_STEP_TURN_LIMIT` / `AGENT_NO_ACTION`) and the `turn_budget` in
  force, which distinguishes a normal loop's 32 from a repair loop's 12.

**Failure severity is deliberately asymmetric.** Only `llm.request` is
mandatory: a write failure there raises `WorkflowFailure` with code
`AGENT_PROMPT_LOG_WRITE_FAILED` and the model is never called, mirroring the
precedent the old per-turn prompt log already established (a request the
trace never recorded must never be sent). Every event logged *after* a model
call or tool execution already happened -- `llm.response`, `tool.*`,
`step.*`, `agent_loop.*` -- is best-effort: a write failure there is caught,
logged as a warning, and never fails the job, since discarding real
(possibly billed) work over a logging hiccup is the wrong tradeoff. The
orchestrator centralizes this in one private `_trace()` helper; `Agent3D`
does the equivalent inline around its two log calls.

To inspect one turn, filter the file by `agent_turn` (and `step_turn` for a
specific retry within a step) with `jq`, e.g.
`jq 'select(.agent_turn == 3)' logs/<edit_job_id>.trace.jsonl`, or reconstruct
a whole run's tool sequence with
`jq -s '[.[] | select(.event == "tool.completed") | {step_id, tool_id, ok}]' logs/<edit_job_id>.trace.jsonl`.
There is no separate rendering tool -- the JSONL file is the source of truth
and is small/flat enough for `jq`/`grep` to be sufficient.

## Separation of concerns

```text
Orchestrator
    owns workflow state: which goal/plan/step is active, plan mutation
    (step status only), tool execution, loop control, termination

Agent3D
    makes the next decision: one model call per turn, given the current
    snapshot. Holds no loop state and mutates nothing.

ToolExecutor
    executes tools: resolves the registered implementation, builds
    ToolExecutionContext, invokes AgentTool.run()

ToolRegistry
    owns available tool definitions and implementations: the one source of
    truth Agent3D's catalog and ToolExecutor both resolve against

CAD_SYSTEM_PROMPT (cad-system.md)
    owns CadQuery source-authoring rules: structure, parameters, semantic
    features, geometry/assembly practices, forbidden behavior
```

## Future work

Not implemented yet, intentionally:

- general plan revision -- reordering, replacing, or deleting steps, or
  marking one `blocked`. The only revision that exists is appending a single
  repair step, and only the orchestrator may do it;
- a repair loop for *reindex* failures -- those still fail the job outright;
  only validation diagnostics are fed back to the agent;
- storage cleanup of scratch `validation-{run}`/`original` candidate objects
  after a job finishes (completed or failed) -- the legacy TS orchestrator
  did this (`cleanupPaths`/`removePaths`); the Python repository has no
  equivalent yet, and per-run snapshots mean there are now more of them;
- final goal-completion evaluation: today the loop trusts the agent's
  `request_step_completion` calls and stops when the plan runs out, without
  independently checking the goal's completion criteria;
- observation truncation -- a step with large tool results grows its context
  unbounded within that step's turn budget.
