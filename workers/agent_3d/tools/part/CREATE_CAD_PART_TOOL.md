# CreateCadPartTool

`CreateCadPartTool` (`tools/part_tools.py`) is a strict `AgentTool` that
creates one new CAD part in the current project, with a minimal, valid,
empty `model.py`. It follows the same contract described in `README.md`
(`AgentTool`, `StrictToolModel`, `ToolExecutionContext`, `ToolInputRejected`,
the shared `run()` lifecycle) as `CreateFeatureTool` — see
`CREATE_FEATURE_TOOL.md` for that shared foundation.

## Why this tool exists

`CreateFeatureTool` can only add a feature to a part whose source already has
a `ModelParams` class and a `build_model` function. A brand-new part has
neither — its accepted source is either nonexistent or the blank
runtime-import-only placeholder. `CreateCadPartTool` closes that gap: it
creates the part and writes it a skeleton that `create_feature` can
immediately build on, without going through the LLM-driven initial-design
workflow (`WriteInitialModel` in `tool_contracts.py`/`tool_executor.py`,
which assumes the part already exists and is a separate, heavier subsystem
this tool does not touch or replace).

## What it creates

Exactly one new row in the `parts` table, and exactly one new file at the
part's canonical accepted-source path
(`{project_id}/parts/cad/{part_id}/model.py`, from
`cad_source_storage_path()` in `workers/indexer/indexer/repository.py`):

```python
from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    pass


def build_model(params: ModelParams):
    return cq.Workplane("XY")
```

`ModelParams` is genuinely empty — no placeholder field. `_source_layout`
(the shared scan `create_feature` uses to validate parameters and find the
insertion point) was loosened to fall back to inserting after the class's
own body (the `pass` statement) when there are no field nodes, instead of
requiring at least one field. This means a feature declaring no `parameters`
validates cleanly against the empty part; a feature declaring any parameter
correctly fails as unknown, exactly as it should when none exist yet.

Unlike `CreateFeatureTool`, this tool writes directly to the **canonical**
source path, not a candidate path. `CreateFeatureTool`'s "never touch
accepted source" rule protects *existing* content — a part that doesn't
exist yet has no accepted source to protect, and "candidate" is inherently
edit-job-scoped for an *existing* part. This is a deliberate, narrow
exception to that rule, not a change to it.

## What it intentionally does not do

- Does not create model parameters — `ModelParams` is always empty. Adding
  real parameters is a separate, not-yet-implemented tool's job
  (`CreateParameterTool`, still a stub in `tools/parameter_tools.py`).
- Does not create semantic features — use `create_feature` after this tool.
- Does not write `params.json` or queue a semantic-index build job, both of
  which the legacy NestJS `create_part` action does (see "Legacy relationship
  and migration" below) — out of scope for what was asked of this tool.
- Does not accept `part_type` as input — always creates a `cad` part (the
  tool's whole purpose is CAD parts; a `mesh`-part equivalent, if ever
  needed, would be a different tool).
- Does not perform full geometry validation — only structural/AST validation
  of the fixed skeleton template (a self-check; the template is constant, so
  this should never actually fail).

## Input schema (`CreateCadPartInput`)

| Field | Type | Notes |
| --- | --- | --- |
| `part_name` | `str` | 1–200 chars; trimmed during normalization; must not already exist (case-insensitively) in the current project. |

Project identity comes only from `context.project_id` — never from input.
`part_id` does not appear in the input schema at all: it's an *output* of
this tool, not an identity the caller supplies.

## Output schema (`CreateCadPartOutput`)

| Field | Meaning |
| --- | --- |
| `status` | Always `"created"` on success. |
| `part_id` | The new part's database id. |
| `project_id`, `part_name`, `part_type` | Echoed from the created row (`part_type` is always `"cad"`). |
| `source_path` | The canonical storage path the skeleton was written to. |
| `content_hash` | SHA-256 of the generated skeleton source. |
| `summary` | One-line human-readable description of what was created. |

## `part_id` and `ToolExecutionContext`

This tool is **project-scoped**, not part-scoped — `part_id` is what it
produces, not an identity it's given. `ToolExecutionContext.__post_init__`
previously required a non-blank `part_id` for every tool; it was loosened to
only require `run_id`/`project_id` (still non-blank), while `part_id` stays
type `str` but may now be blank. `CreateCadPartTool` never reads
`context.part_id`. This is the only change to the shared context — every
part-scoped tool (`create_feature`, the read-only index tools) is
unaffected, since callers still choose what to pass for `part_id` and
nothing about their behavior changed.

## Candidate-scoped mutation vs. this tool's mutation

There is no candidate here — see "What it creates" above for why a direct
canonical write is the correct, narrow exception rather than a rule change.
The mutation itself is two steps, made atomic by a compensating rollback:

1. Insert the `parts` row (`repository.create_part`).
2. Write the skeleton to the canonical path (`repository.write_text`). If
   this fails, the just-created row is deleted (`repository.delete_part`)
   before the failure propagates, so a storage error never leaves an
   orphaned part row with no source.

## Normalization vs. validation

`normalize_input` trims `part_name`; nothing else (no syntax to validate at
normalization time — there's no free-form source in this tool's input at
all).

`validate_input` raises `ToolInputRejected` for:
- a blank `part_name` after trimming;
- a `part_name` that already exists (case-insensitively) in
  `context.project_id` — checked via the existing `find_part_by_name`
  SQL RPC (`supabase/migrations/20260712000000_create_generation_jobs.sql:61-76`,
  already granted to `service_role`), reused as a read-only pre-check rather
  than inventing new query logic. This is what makes a duplicate name a
  clean `TOOL_VALIDATION_FAILED` instead of a generic execution failure —
  the same pattern `create_feature` uses for duplicate `semantic_id`.

The database's own case-insensitive unique index on `(project_id,
part_name)` is still the authoritative constraint; the RPC check exists to
give a clean, specific, agent-facing rejection instead of surfacing a raw
constraint violation. `SupabaseEditRepository.create_part` also translates a
unique-violation (Postgres `23505`) into `WorkflowFailure("PART_EXISTS",
...)` and a foreign-key violation (`23503`, an invalid `project_id`) into
`WorkflowFailure("PROJECT_NOT_FOUND", ...)`, in case of a race between the
pre-check and the insert.

## Common structured failures

Expected failures surface as `ToolFailure` with `error.code ==
"TOOL_VALIDATION_FAILED"` (blank or duplicate `part_name`) and a `details`
dict identifying the field/reason. Malformed raw arguments (unknown fields,
wrong types) surface as `TOOL_INPUT_INVALID` before normalization runs.
Unexpected failures (e.g. a storage error after the row was already created)
trigger the compensating rollback and then return a sanitized
`TOOL_EXECUTION_FAILED` with no raw exception text — inherited unchanged
from `AgentTool.run()`.

## Legacy relationship and migration

There is no *Python* legacy part-creation code to migrate — part creation
today is entirely owned by the NestJS control plane
(`services/cad_agent/src/cad-actions.service.ts`, `CadActionsService.createPart`,
backed by `CadAgentRepository.createPart`/`deletePart`). That TS
implementation is the closest behavioral reference (insert row → write
source → delete-on-failure rollback) and this tool's `execute()` mirrors its
shape, but nothing was migrated or deprecated — the TS action keeps working
exactly as it does today; this tool is a new, independent Python path with
one behavioral difference (a real `ModelParams`/`build_model` skeleton
instead of TS's blank runtime-import-only placeholder). Calling the existing
NestJS endpoint instead of writing directly to Supabase was considered and
rejected for now: that service isn't always running, its actions endpoint
has no auth guard yet, and it would give the strict tool framework a new
HTTP-client capability it doesn't have today. `SupabaseEditRepository`
already holds a full `service_role` client (the same one `create_feature`
uses), so writing directly keeps this tool self-contained.

`_source_layout` (`tools/feature_generation.py`) was loosened as part of
this change (see "What it creates" above) — this also fixes a latent gap in
the legacy `add_model_parameter` `EditPlan` operation, which previously
could not add a first field to an empty-`ModelParams` part either.

## Unit-test strategy

`tests/test_cad_create_part_unit.py` uses a small in-memory `FakeRepository`
that, in addition to the storage `read_text`/`write_text` fake from
`create_feature`'s tests, simulates the `parts` table
(`find_part_by_name`/`create_part`/`delete_part` over an in-memory list).
Covers: strict input parsing and rejection of unknown fields/types;
`part_name` trimming; duplicate-name rejection; correct output fields on
success; the generated source parses with `ast` and contains an empty
`ModelParams` and a valid `build_model`; a storage-write failure triggers
the rollback (`delete_part` called, row removed) and returns a sanitized
`TOOL_EXECUTION_FAILED`; and a direct test of the loosened `_source_layout`
confirming it no longer rejects a zero-field `ModelParams`.

## Integration-test strategy

`tests/test_cad_create_part_integration.py` runs the complete
`CreateCadPartTool.run(...)` lifecycle and verifies the part row and source
file are created correctly and the source parses with `ast`. The key test —
the whole point of this tool — chains `CreateCadPartTool` into a real
`CreateFeatureTool.run(...)` call against the newly created part's source
(seeding a candidate path from the new canonical source first, the same way
a real edit workflow would) and confirms `create_feature` succeeds: this is
the concrete proof that the empty skeleton is sufficient to unblock feature
creation on a brand-new part. A duplicate-name test confirms a second
`create_cad_part` call with the same name (case-insensitive) leaves no new
row or file behind.

## Test dependencies

Same as `create_feature`: `pydantic` and stdlib `unittest`
(`unittest.IsolatedAsyncioTestCase`) — no `pytest-asyncio` or other
test-only dependency.

## Running the tests locally

From the repository root:

```
pytest tests/test_cad_create_part_unit.py
pytest tests/test_cad_create_part_integration.py
pytest tests/test_cad_create_part_unit.py tests/test_cad_create_part_integration.py
```

To confirm this change did not regress the shared code it touches
(`ToolExecutionContext`, `feature_generation._source_layout`) or the tool it
unblocks:

```
pytest tests/test_cad_editor_core.py tests/test_cad_tool_framework.py tests/test_cad_create_feature_unit.py tests/test_cad_create_feature_integration.py
```
