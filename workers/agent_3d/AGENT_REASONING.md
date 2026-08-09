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

A step moves `pending → in_progress → completed`. Only the orchestrator
writes those statuses, and each transition is checkpointed to
`edit_jobs.history` as a `plan_updated` entry, so a replacement worker
resumes at the step the previous one reached. **The goal is never mutated by
anything.** Adding, replacing, or reordering steps -- plan revision -- is not
implemented; only step status changes.

The loop ends when no step is pending or in progress. Two caps bound it: at
most `MAX_STEP_TURNS` (10) turns on a single step, and `MAX_AGENT_TURNS` (24)
turns overall, raising `AGENT_STEP_TURN_LIMIT` / `AGENT_TURN_LIMIT`. A
decision containing no tool call fails fast with `AGENT_NO_ACTION` rather
than silently burning the budget or assuming the step finished.

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

## After the loop: validate, commit, reindex, export

Once every plan step reaches a terminal status, the orchestrator hands the
final candidate to a commit pipeline that ports the pre-Python-rewrite TS
orchestrator's hash-guarded commit design (`git show
HEAD:services/cad_agent/src/orchestrator.service.ts`), now backed by RPCs and
schema (`edit_jobs.state`, `edit_job_events.event_type`) that were already
provisioned for it:

```text
final candidate
    ↓ copy to {project}/candidates/cad/{part}/{edit_job}/attempt-{n}/model.py
    ↓ (queue_edit_candidate_validation_owned -- the RPC enforces this exact path)
CAD validator worker validates it (async job, polled)
    ↓ not passed -> VALIDATION_FAILED, job fails
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

The agent loop's candidate lives at a stable path with no attempt/repair
concept, but `queue_edit_candidate_validation`'s RPC enforces an
`attempt-{n}` path server-side (`attempt_count` on the job row, incremented
per commit attempt) -- so commit-time copies the candidate's current content
there before validating; the content, and therefore the hash, is identical to
what the loop produced. Before the first agent turn,
`_prepare_candidate` also backs up canonical source unmodified to
`.../original/model.py` and records its hash as
`edit_jobs.accepted_source_sha256`, so a reindex failure after commit can
restore exactly what was there before this job started.

Validation failure and reindex failure both fail the job outright -- there is
no repair loop that feeds validator diagnostics back to the agent for another
try. Export failure is the one non-fatal step: it only adds a warning to the
terminal result.

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
- `agent_loop.completed` / `agent_loop.failed` -- the loop's terminal
  outcome; `agent_loop.failed` includes the `error_code`
  (`AGENT_TURN_LIMIT` / `AGENT_STEP_TURN_LIMIT` / `AGENT_NO_ACTION`).

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

- plan revision -- adding, replacing, or reordering steps (only step status
  changes today), including marking a step `blocked`;
- a repair loop -- validation and reindex failures fail the job outright;
  validation diagnostics are never fed back to the agent for another turn;
- storage cleanup of scratch `attempt-{n}`/`original` candidate objects after
  a job finishes (completed or failed) -- the legacy TS orchestrator did this
  (`cleanupPaths`/`removePaths`); the Python repository has no equivalent yet;
- final goal-completion evaluation: today the loop trusts the agent's
  `request_step_completion` calls and stops when the plan runs out, without
  independently checking the goal's completion criteria;
- observation truncation -- a step with large tool results grows its context
  unbounded within that step's turn budget.
