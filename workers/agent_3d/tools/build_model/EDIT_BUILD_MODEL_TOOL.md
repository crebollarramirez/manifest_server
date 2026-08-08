# EditCadBuildModelTool

`EditCadBuildModelTool` (`tools/build_model/build_model_tools.py`) is a
strict `AgentTool` that replaces the body of `build_model` in the current
edit-scoped candidate source. It follows the same contract described in
`README.md` (`AgentTool`, `StrictToolModel`, `ToolExecutionContext`,
`ToolInputRejected`, the shared `run()` lifecycle) as every other tool in
this package.

## Why this tool exists, and why it's a separate folder

Every other mutating tool in this package explicitly, deliberately never
touches `build_model`:

- `CreateFeatureTool`: *"does not modify build_model or wire the feature
  into the assembly."*
- `EditFeatureTool`/`DeleteFeatureTool`: same boundary, inherited.
- `CreateCadPartTool`: generates a `build_model` that just
  `return cq.Workplane("XY")` — deliberately inert.

That boundary is what makes `requires_assembly_wiring: true` meaningful in
`create_feature`'s output. `EditCadBuildModelTool` is what actually performs
that separate step — the only tool that writes to `build_model` at all. It
gets its own top-level folder (`tools/build_model/`, not `tools/feature/`)
because it isn't feature-scoped or part-scoped — it's a distinct concern
with exactly one tool, the same one-tool-per-folder shape `tools/part/`
already has.

## What it does

Replaces `build_model`'s entire body with `function_body` (statements only —
no `def build_model(params: ModelParams):` line, no decorator, matching
`create_feature`'s `function_body` convention exactly). This is a full
replacement, not an append or merge — provide the complete new body each
time, the same way `edit_parameter`/`edit_feature` require the complete new
value for whichever field changed (there's no partial-body-patch concept
here, since `build_model` is a single unstructured function, not a
collection of independently addressable fields).

Applies the same function-body safety checks `create_feature` applies to a
feature body: must parse as valid Python (via a temporary synthetic wrapper
function, not regex), must contain a `return` statement, must not contain
imports, nested `def`/`class`, `eval`/`exec`/`compile`/`__import__`/`open`,
references to a denylist of OS/network/file/DB/process modules, or
provenance-marker text. There is no `params.<field>` declared-vs-referenced
check here (`build_model` doesn't declare a `parameters` tuple the way a
feature does) and no attempt to statically verify every called name refers
to a real feature function — that would require a level of call-graph
validation none of this package's other tools attempt either, and is
explicitly out of scope for this tool.

## Candidate-scoped mutation

Requires `context.candidate_id` and operates only on
`{project_id}/candidates/cad/{part_id}/{candidate_id}/model.py`
(`feature_generation._candidate_source_path`, imported from
`tools/feature/` — the one cross-family import this tool needs, the same
pattern `tools/parameter/` already uses for the same helper). Never reads or
writes accepted source. Locating `build_model`'s body span
(`feature_generation._build_model_body_span`) and the forbidden-content
checks (`_parse_body_function`, `_reject_forbidden_body_nodes`, `_walk_body`)
are both reused directly from `tools/feature/feature_generation.py` rather
than reimplemented — there is exactly one implementation of "is this
function body safe," shared by `create_feature`, `edit_feature`, and this
tool.

## Common structured failures

`ToolFailure` with `error.code == "TOOL_VALIDATION_FAILED"` and a `details`
dict identifying the reason: `missing_return`, `forbidden_syntax`,
`forbidden_call`, `forbidden_module`, or `missing_candidate`; a syntax error
in `function_body` surfaces with the parser's message. Malformed raw
arguments surface as `TOOL_INPUT_INVALID` before normalization runs.
Unexpected failures return a sanitized `TOOL_EXECUTION_FAILED` with no raw
exception text.

## Unit-test strategy

`tests/test_cad_build_model_unit.py` uses the same in-memory
`FakeRepository` shape as every other tool's tests. Covers: strict input
parsing; body replacement leaves unrelated source (including the feature
functions `build_model` calls) untouched; indentation normalization;
missing-return rejection; forbidden imports/`eval`/`exec`/provenance-marker
rejection; malformed-body rejection; missing-candidate rejection; sanitized
unexpected-failure handling.

## Integration-test strategy

`tests/test_cad_build_model_integration.py` is the concrete proof of this
tool's whole reason for existing: it chains `CreateCadPartTool`'s empty
skeleton → a real `CreateFeatureTool.run()` call (confirming its output says
`requires_assembly_wiring: true` and that `build_model` is still the inert
placeholder afterward) → a real `EditCadBuildModelTool.run()` call that
wires the new feature in — asserting the feature itself is completely
unaffected by the wiring step. A second test confirms a rejected edit leaves
the candidate byte-for-byte unchanged.

## Test dependencies

Same as every other tool in this package: `pydantic` and stdlib `unittest`
(`unittest.IsolatedAsyncioTestCase`) — no `pytest-asyncio` or other
test-only dependency.

## Running the tests locally

From the repository root:

```
pytest tests/test_cad_build_model_unit.py
pytest tests/test_cad_build_model_integration.py
pytest tests/test_cad_build_model_unit.py tests/test_cad_build_model_integration.py
```

To confirm this change did not regress the shared `feature_generation.py`
helpers it reuses:

```
pytest tests/test_cad_create_feature_unit.py tests/test_cad_create_feature_integration.py \
       tests/test_cad_feature_edit_delete_unit.py tests/test_cad_feature_edit_delete_integration.py
```
