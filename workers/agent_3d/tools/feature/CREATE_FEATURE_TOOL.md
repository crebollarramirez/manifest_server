# CreateFeatureTool

`CreateFeatureTool` (`tools/feature/feature_tools.py`) is a strict `AgentTool`
that adds one new, isolated semantic CadQuery feature function to the
edit-scoped candidate source. It follows the contract described in
`README.md` (`AgentTool`, `StrictToolModel`, `ToolExecutionContext`,
`ToolInputRejected`, the shared `run()` lifecycle) and adds no new framework
machinery. See `EDIT_DELETE_FEATURE_TOOLS.md` for `edit_feature`/
`delete_feature`, the two tools that manage an existing feature created this
way. All of the generation/validation primitives these three tools share
(and that `EditCadBuildModelTool` partially reuses too) live in
`tools/feature/feature_generation.py`.

## What it creates

Exactly one new top-level function, decorated with `@cad_part(...)`, inserted
into the candidate source immediately before `build_model`:

```python
@cad_part(
    semantic_id="corner_fillets",
    role="aesthetic_features",
    library="cadquery",
    parameters=("plate_width",),
    depends_on=("base_plate",),
    search_keys=("corner fillets", "rounded corners"),
)
def fillet_base_corners(
    params: ModelParams,
    plate,
):
    """Round the base plate's vertical corners."""
    radius = min(params.plate_width * 0.05, 3.0)
    return plate.edges("|Z").fillet(radius)
```

The decorator's keyword order is always `semantic_id`, `role`, `library`,
`parameters`, `depends_on`, `search_keys`. `library` is always the literal
`"cadquery"` -- it is server-owned and not accepted as agent input. Tuple
literals follow Python convention: `()` empty, `("value",)` one item (note the
trailing comma), `("a", "b")` multiple items.

The function signature is always the multi-line form `params: ModelParams,`
followed by one line per declared dependency argument, matching the order
dependencies were declared in. The docstring and function body come from the
agent's `docstring` and `function_body` input, indented under the signature.

## What it intentionally does not do

- Does not create `ModelParams` fields -- referenced parameters must already exist.
- Does not modify any existing feature body, `ModelParams`, or unrelated source.
- Does not modify `build_model` or wire the new feature into the assembly. The
  output's `requires_assembly_wiring` is always `true`: a separate assembly
  step (out of this tool's scope) must call the new function for it to affect
  the final model.
- Does not run exports, commit accepted source, or touch project files outside
  the current candidate.
- Does not perform full geometry validation (only structural/AST validation).
- Does not touch system-owned provenance markers (`# PART-START: ...`,
  `# PART-END: ...`, `# CAD-AGENT-START: ...`, `# CAD-AGENT-END: ...`).

## Input schema (`CreateFeatureInput`)

All fields are agent-supplied; project, part, run, and candidate identity come
only from `ToolExecutionContext` (`context.project_id`, `context.part_id`,
`context.candidate_id`), never from the input model.

| Field | Type | Notes |
| --- | --- | --- |
| `semantic_id` | `str` | snake_case, must not already exist. |
| `function_name` | `str` | public snake_case `verb_noun[_...]` name (e.g. `cut_mounting_holes`); must not already exist. |
| `role` | `str` | free-text role label. |
| `parameters` | `tuple[str, ...]` | must exactly match the `params.<field>` attributes referenced in `function_body`; every entry must be an existing `ModelParams` field. |
| `dependencies` | `tuple[FeatureDependencyInput, ...]` | each entry is `{semantic_id, argument_name}`; every `semantic_id` must already exist in the current part. |
| `search_keys` | `tuple[str, ...]` | at least one non-empty string; duplicates are removed (order preserved) during normalization. |
| `docstring` | `str` | inserted as the function's docstring; must not contain `"""`. |
| `function_body` | `str` | statements only -- no `def`, decorator, or import; must contain a `return`. |

`FeatureDependencyInput` fields:

| Field | Type | Notes |
| --- | --- | --- |
| `semantic_id` | `str` | the dependency's existing semantic ID. |
| `argument_name` | `str` | public Python identifier; becomes the generated function's positional argument name for that dependency. Cannot be `params`. |

Because `StrictToolModel` disables coercion, container fields require actual
tuples in `raw_arguments` (a JSON array decoded to a Python `list` is rejected
as an invalid type, not silently coerced).

## Output schema (`CreateFeatureOutput`)

| Field | Meaning |
| --- | --- |
| `status` | Always `"created"` on success. |
| `semantic_id`, `function_name` | Echoed from the (normalized) input. |
| `candidate_id` | The edit-scoped candidate that was mutated (`context.candidate_id`). |
| `content_hash` | SHA-256 of the resulting candidate source, for downstream integrity checks. |
| `source_path` | Repository-relative path of the mutated candidate file. |
| `parameters` | Declared `ModelParams` fields the new feature uses. |
| `dependency_semantic_ids` | Resolved semantic IDs of direct dependencies, in declaration order. |
| `requires_assembly_wiring` | Always `true` -- signals that a separate step must call the new function from `build_model` (or another assembly point) for it to take effect. |
| `summary` | One-line human-readable description of the change. |

## Dependency-to-argument mapping

Each entry in `dependencies` becomes one positional argument in the generated
signature, in the same order as declared, using that entry's `argument_name`.
`depends_on` in the generated decorator is the tuple of those entries'
`semantic_id` values, in the same order. There is no dependency argument for
`parameters` -- those are read from `params.<field>` inside the body instead.

## Candidate-scoped mutation

The tool reads and writes exactly one file:
`{project_id}/candidates/cad/{part_id}/{candidate_id}/model.py`, resolved from
`ToolExecutionContext`. It never reads or writes accepted source. Mutation is
a narrow, AST-aware insertion -- the tool locates the offset immediately
before `build_model` (via the same source-layout scan `apply_edit_plan` uses)
and splices in the generated block; it does not regenerate or reformat the
rest of the file, so unrelated source and existing provenance markers are
byte-for-byte preserved.

`ToolRepository` gained one narrow extension for this: `write_text(path,
content)`, alongside the existing `read_text(path)`. No general-purpose
filesystem API was introduced.

## Normalization vs. validation

`normalize_input` (best-effort, never rejects for syntax reasons):
- Trims `semantic_id`, `function_name`, `role`, `docstring`, and each `parameters`/search-key entry.
- Removes duplicate `search_keys` while preserving first-seen order.
- Normalizes `function_body` line endings, tab width, and common leading indentation.

`validate_input` (raises `ToolInputRejected` with a stable `details` payload
for every rejection):
- An edit-scoped candidate (`context.candidate_id`) is required and its source must be readable.
- `semantic_id` is valid snake_case and does not already exist.
- `function_name` is a public verb-noun snake_case identifier and does not already exist.
- `parameters` are unique, valid identifiers, and exist as `ModelParams` fields.
- `dependencies` have unique `semantic_id`s and unique, valid `argument_name`s; every `semantic_id` exists in the current part; the feature does not depend on itself.
- `search_keys` are non-empty after normalization.
- `docstring` contains no `"""`.
- `function_body` parses as valid Python (via a temporary synthetic wrapper function, not regex), contains a `return`, contains no imports, nested `def`/`class`, `eval`/`exec`/`compile`/`__import__`/`open`, or references to a denylist of OS/network/file/DB/process modules, and contains no provenance-marker text.
- The set of `params.<field>` attributes referenced in `function_body` exactly matches the declared `parameters`.

## Common structured failures

All expected failures surface as `ToolFailure` with `error.code ==
"TOOL_VALIDATION_FAILED"` and a `details` dict describing the specific rule
(e.g. `{"field": "semantic_id", "reason": "duplicate"}`). Malformed raw
arguments (unknown fields, wrong types, a JSON list where a tuple is required)
surface as `TOOL_INPUT_INVALID` before normalization ever runs. Unexpected
internal errors are logged server-side and returned as a sanitized
`TOOL_EXECUTION_FAILED` with no raw exception text -- this is inherited
unchanged from `AgentTool.run()`.

## Feature creation vs. assembly wiring

`CreateFeatureTool` only creates the function and its metadata. The function
is not called from anywhere yet -- `build_model` is untouched, and
`requires_assembly_wiring` is always `true` in the output. Wiring the new
feature into `build_model` (or another feature) and creating any new
`ModelParams` fields it might need are separate, later steps performed by
other tools/operations, not by this one.

## Legacy migration

The pre-existing feature-creation path is the `add_cad_feature` operation on
an `EditPlan` / `ToolPlan`, applied through `applier.apply_edit_plan()` and
driven by the reasoner/orchestrator pipeline (`AddCadFeature` in both
`contracts.py` and `tool_contracts.py`). Both are marked deprecated in favor
of `CreateFeatureTool`:

```python
# DEPRECATED: Legacy feature-creation path.
# Use CreateFeatureTool through the strict agent-tool executor instead.
```

Rather than duplicating decorator/function generation, the generation
primitives themselves -- `_tuple_source`, `_single_function`,
`_normalize_body_source`, `_source_layout`, and `_added_feature` -- live in
`tools/feature_generation.py`, not in `applier.py`. `CreateFeatureTool` uses
them directly as tools/-internal implementation code. `applier.py`'s legacy
`add_cad_feature` handling (inside `apply_edit_plan`, `_metadata_replacement`,
and `_added_helper`) imports the same functions back from
`tools/feature_generation`, so there is exactly one implementation of
tuple/decorator/function-block generation shared by both paths -- the
dependency direction is legacy code depending on `tools/`, not the other way
around. The legacy path is retained (not deleted) because the existing
reasoner/orchestrator pipeline still drives edits through `EditPlan`/
`ToolPlan`, which is unchanged by this task. This is one step in an ongoing,
incremental migration of legacy CAD-editor logic into `tools/`; modules such
as `targets.py` are still legacy-owned and are expected to move in future
steps, not this one.

`CreateFeatureTool` is registered alongside the existing read-only index tools
in `edit_worker.py`'s `build_runtime()`, through the same `ToolRegistry` /
`Toolbox` mechanism -- no second registration system was introduced.

## Unit-test strategy

`tests/test_cad_create_feature_unit.py` uses a small in-memory
`FakeRepository` (`read_text`/`write_text` backed by a dict) and a compact
one-feature `model.py` fixture. Each test exercises one rule in isolation
through the full `AgentTool.run()` lifecycle (or, for pure schema questions,
`CreateFeatureInput.model_validate()` directly): strict parsing, unknown
fields, invalid/coerced types, normalization, decorator generation and field
order, empty/one-item/multi-item tuple rendering, signatures with and without
dependencies, every duplicate/unknown/invalid-identifier rejection, malformed
or import-laden or `eval`/`exec`/IO-laden function bodies, missing `return`,
parameter/body mismatches, output-model field values, and that unexpected
execution errors never leak raw exception text.

## Integration-test strategy

`tests/test_cad_create_feature_integration.py` uses a richer fixture: a frozen
`ModelParams` dataclass, two existing `@cad_part` features (one already
carrying `# PART-START`/`# PART-END` markers as the server would have written
them), and `build_model`, stored at both an "accepted" path and a separate
candidate path. Tests run the complete `CreateFeatureTool.run(...)` lifecycle
and assert: the resulting source parses with `ast`; the decorator is correct
and in the right position (before `build_model`); dependencies appear in both
metadata and the generated signature; declared parameters match output;
unrelated source, `build_model`, the accepted-path file, and existing
provenance markers are byte-for-byte unchanged; the output flags
`requires_assembly_wiring`; invalid input leaves the candidate untouched; the
legacy `add_cad_feature` `EditPlan` path still applies and produces the same
decorator conventions; and the tool is discoverable through `ToolRegistry` /
`Toolbox` by its `create_feature` ID.

## Test dependencies

Only `pydantic` (already a project dependency) and the standard library
`unittest` (`unittest.IsolatedAsyncioTestCase`, the pattern already used by
`tests/test_cad_tool_framework.py`) are required -- no `pytest-asyncio` or
other test-only dependency was added.

## Running the tests locally

From the repository root:

```
pytest tests/test_cad_create_feature_unit.py
pytest tests/test_cad_create_feature_integration.py
pytest tests/test_cad_create_feature_unit.py tests/test_cad_create_feature_integration.py
```

To confirm this change did not regress the existing applier/tool-framework
suites it shares generation logic and a registration mechanism with:

```
pytest tests/test_cad_editor_core.py tests/test_cad_tool_framework.py tests/test_cad_editor_agent.py
```
