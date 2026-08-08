# Parameter tools: CreateParameterTool, EditParameterTool, DeleteParameterTool

Three strict `AgentTool`s (`tools/parameter_tools.py`) that manage `ModelParams`
fields on a part's edit-scoped candidate source: `create_parameter`,
`edit_parameter`, `delete_parameter`. They follow the same contract described
in `README.md` (`AgentTool`, `StrictToolModel`, `ToolExecutionContext`,
`ToolInputRejected`, the shared `run()` lifecycle) as `CreateFeatureTool` and
`CreateCadPartTool` — see `CREATE_FEATURE_TOOL.md` / `CREATE_CAD_PART_TOOL.md`
for that shared foundation.

## Why these tools exist

`CreateFeatureTool` requires every `parameters` entry it declares to already
exist as a `ModelParams` field, but nothing in `tools/` could create, adjust,
or remove one. These three tools close that gap, the same way
`CreateCadPartTool` closed the "no `ModelParams`/`build_model` yet" gap for a
brand-new part.

## What each tool does

- **`create_parameter`** — adds one new field (`parameter_name`, numeric
  `value`) to `ModelParams`, wrapped in `# CAD-AGENT-START: model_parameter:<name>`
  / `# CAD-AGENT-END: ...` provenance markers (agent-owned). Rejects a name
  that already exists.
- **`edit_parameter`** — changes an existing field's default value. Works on
  **any** existing field, agent-owned or hand-authored. The field is never
  renamed — only the value changes, and any existing provenance markers
  around it are left exactly as they were.
- **`delete_parameter`** — removes an existing field, but **only** if it is
  agent-owned (marker-wrapped) **and** not currently referenced anywhere.

This create/edit/delete asymmetry (edit = any field, delete = only
agent-created fields) is deliberate, not an oversight — see "Migration
notes" below for where it comes from.

## What none of them do

- Create, modify, or wire semantic features — use `create_feature` after
  `create_parameter` to declare and use a new parameter.
- Touch `build_model` or any feature body.
- Touch accepted source — all three read and write only the candidate
  identified by `context.candidate_id`.
- Rename a field (`edit_parameter`) or delete a hand-authored field
  (`delete_parameter`).

## Value shape: float-only

Every `ModelParams` field in every fixture, test, and prompt in this repo is
`<name>: float = <default>` — confirmed, no exceptions found anywhere.
`CreateParameterInput`/`EditParameterInput` take a structured `value: float`
(`Field(gt=0)`) rather than exposing the legacy free-form
`field_source: str`. The tool formats `f"{name}: float = {value!r}"` itself
and runs that through the same AST-validating generation logic the legacy
path used, so the safety net is identical even though the agent-facing
contract is narrower.

## The delete safety check — stricter than legacy, on purpose

The legacy planning prompt (`prompts/tool-plan.md`) claims *"required or
referenced parameters cannot be deleted,"* but the actual code
(`tool_executor.py`'s `apply_plan`) never enforced that — the only related
check applies to a newer plan schema version and only requires *disclosing*
impacted consumers, not preventing the delete. An in-use field could be
deleted and would only be caught later (if at all) by a separate,
asynchronous validator worker this package never calls.

`delete_parameter` genuinely fixes this: `validate_input` hard-blocks
deletion if the field is still referenced, checking both:
- any `cad_part(parameters=(...))` decorator tuple declaring it, and
- any `params.<field>` usage in any function body, resolved transitively
  through private-helper calls (via
  `workers.indexer.indexer.semantic_graph.effective_parameter_references`,
  already imported into this package by `tool_executor.py`).

The rejection's `details.referenced_by` lists every function name still
using the field, so the caller knows exactly what to remove first.

## The last-field-becomes-`pass` behavior

If the field being deleted is the only statement in `ModelParams`'s body,
removing it outright would leave `class ModelParams:` with no body at all —
a `SyntaxError`. `delete_parameter` detects this and replaces the field with
a bare `pass` instead, reusing the same empty-`ModelParams`-is-valid
convention `CreateCadPartTool` established (and that `feature_generation.
_source_layout` was loosened to accept). The result is immediately valid for
a subsequent `create_parameter`/`create_feature` call, exactly like a
freshly created part.

The reverse also matters: `create_parameter` detects a bare `pass`
placeholder and replaces it entirely for the *first* real field, instead of
leaving a dangling `pass` next to the new field.

## A bug found and fixed while building this: marker-aware insertion

`feature_generation._source_layout`'s `parameter_insert` offset (used by the
legacy `add_model_parameter` `EditPlan` operation) is computed from the last
field's bare `AnnAssign` line — it doesn't know about a preceding field's own
trailing `# CAD-AGENT-END` comment. That's harmless for legacy's usage
pattern (every `add_model_parameter` operation in a plan is batched and
spliced in one shot against markers that don't exist yet), but calling
`create_parameter` a second time against an already marker-wrapped field
would splice the new field's block *inside* the previous field's own
markers. `parameter_generation._parameter_insertion_span` fixes this for the
new tools specifically (extends the insertion point past a trailing owned
marker block when the last field has one) without touching
`_source_layout`'s existing behavior or the legacy path that relies on it.

## Candidate-scoped mutation

All three require `context.candidate_id` and operate only on
`{project_id}/candidates/cad/{part_id}/{candidate_id}/model.py`
(`feature_generation._candidate_source_path`, shared with every tool in
`tools/feature/` so it isn't defined twice). None of them ever read or write
accepted source.

## Common structured failures

Expected failures surface as `ToolFailure` with `error.code ==
"TOOL_VALIDATION_FAILED"` and a `details` dict identifying the field/reason
— `duplicate` (create), `not_found` (edit/delete), `not_owned` (delete),
`in_use` (delete, with `referenced_by`), or `missing_candidate` (all three).
Malformed raw arguments surface as `TOOL_INPUT_INVALID` before normalization
runs. Unexpected failures (e.g. a storage error) return a sanitized
`TOOL_EXECUTION_FAILED` with no raw exception text.

## Migration notes

Legacy parameter logic lives in two different, only partially overlapping
systems:

- **Create** and **edit** had real, migratable generation-primitive
  functions: `applier._added_parameter` and `applier._parameter_replacement`.
  Both are now `tools/parameter_generation.py`'s `_added_parameter_block` and
  `_replacement_field_source` — decoupled from the `AddModelParameter`/
  `ReplaceParameterField` operation types (they take `name`/`field_source`/
  `indent` directly instead), but otherwise identical validation logic.
  `applier.py` no longer defines these functions itself; it imports them
  back and its `add_model_parameter`/`replace_parameter_field` `EditPlan`
  handling is otherwise unchanged.
- **Delete** had no equivalent function to migrate — `tool_executor.py`
  implements it as a raw text splice locating `# CAD-AGENT-START`/`-END`
  marker comments inline, not as a reusable function. `delete_parameter`'s
  marker-location logic (`_owned_parameter_marker_span`) is new, adapted in
  spirit from that scanning approach (regex-matching the same marker
  format), not literally extracted from it. This is also where the
  edit-vs-delete asymmetry comes from: legacy `delete_model_parameter`
  already restricted deletion to owned/marker-wrapped fields
  (`DELETE_NOT_OWNED` otherwise) while `replace_parameter_field` never had
  that restriction — both behaviors are preserved here.
- `_find_parameter_span`, `_parameter_insertion_span`, and
  `_parameter_usage_owners` are new (not migrated): the legacy span-finder
  for an *existing* field is `targets.py`'s `collect_target_spans`, which
  stays untouched — a separate, later migration, same as it was for
  `create_feature`. These tools do their own narrow AST-aware location
  instead, the same pattern `CreateFeatureTool`/`CreateCadPartTool` already
  use rather than routing through `targets.py`'s heavier general-purpose
  machinery.

## Unit-test strategy

`tests/test_cad_parameter_tools_unit.py` uses the same in-memory
`FakeRepository` shape as `create_feature`'s tests, with three fixtures: one
with a hand-authored used field and an agent-owned unused field (the base
case), one with an agent-owned *in-use* field (for the delete-rejection
test), and one with a single agent-owned field (for the last-field-becomes-
`pass` test). Covers, per tool: strict input parsing and rejection of
unknown fields/types; normalization; duplicate-name rejection (create);
missing-parameter rejection (edit/delete); exact marker-block format;
editing both hand-authored and agent-owned fields; delete rejecting a
non-owned field; delete rejecting an in-use field with `referenced_by` in
the details and confirming the candidate is unchanged; the last-field-
becomes-`pass` behavior; missing-candidate rejection; and sanitized
unexpected-failure handling.

## Integration-test strategy

`tests/test_cad_parameter_tools_integration.py` starts from
`CreateCadPartTool`'s own empty skeleton (`_skeleton_source()`) and runs the
complete lifecycle: create a parameter → edit its value → declare and use it
via a real `CreateFeatureTool.run()` call → confirm deleting it while still
referenced is rejected with the correct `referenced_by` and leaves the
candidate byte-for-byte unchanged → remove the feature's usage → confirm the
delete then succeeds and `ModelParams` correctly returns to `pass` — with
`build_model` and all unrelated source asserted unchanged at every single
step. A second test confirms an immediate create-then-delete-while-
referenced is rejected without mutation; a third confirms a plain
create-then-delete of an unused field leaves the candidate identical to
before either call.

## Test dependencies

Same as `create_feature`/`create_cad_part`: `pydantic` and stdlib
`unittest` (`unittest.IsolatedAsyncioTestCase`) — no `pytest-asyncio` or
other test-only dependency.

## Running the tests locally

From the repository root:

```
pytest tests/test_cad_parameter_tools_unit.py
pytest tests/test_cad_parameter_tools_integration.py
pytest tests/test_cad_parameter_tools_unit.py tests/test_cad_parameter_tools_integration.py
```

To confirm this change did not regress the shared code it touches
(`applier.py`'s parameter operations, `feature_generation.py`'s
`_candidate_source_path` relocation) or the tools it builds on:

```
pytest tests/test_cad_editor_core.py tests/test_cad_tool_framework.py \
       tests/test_cad_create_feature_unit.py tests/test_cad_create_feature_integration.py \
       tests/test_cad_create_part_unit.py tests/test_cad_create_part_integration.py
```
