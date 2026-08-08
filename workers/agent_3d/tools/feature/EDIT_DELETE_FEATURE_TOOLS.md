# EditFeatureTool and DeleteFeatureTool

Two strict `AgentTool`s (`tools/feature/feature_tools.py`, alongside
`CreateFeatureTool`) that manage an *existing* semantic CAD feature:
`edit_feature`, `delete_feature`. They follow the same contract described in
`README.md` and `CREATE_FEATURE_TOOL.md` (`AgentTool`, `StrictToolModel`,
`ToolExecutionContext`, `ToolInputRejected`, the shared `run()` lifecycle).

## Identifying a feature

Both tools identify the target feature by `semantic_id` and/or
`function_name` — at least one is required. If both are given, they must
resolve to the *same* feature; giving two identifiers that point at
different features is rejected as `conflicting_identifiers` (checked by
probing each identifier independently once the combined lookup fails, so the
error is specific rather than a generic "not found"). Neither identifier is
itself editable through these tools — renaming a feature is out of scope
(it would require rewriting every other feature's `depends_on` reference to
match, a structural change bigger than "edit a feature").

## EditFeatureTool

All edit fields (`role`, `parameters`, `dependencies`, `search_keys`,
`docstring`, `function_body`) are optional — a field left `null` keeps its
current value; only the fields actually provided are changed. At least one
must be provided. Validation mirrors `create_feature` exactly, but against
the *effective* merged state (new value if provided, current value
otherwise) — e.g. if only `function_body` changes, its `params.<field>`
usage is still checked against the *current* (unchanged) `parameters`, and
vice versa if only `parameters` changes.

**Two independently regenerated regions**, touched only when something in
them actually changed:
- The `@cad_part(...)` decorator (`role`/`parameters`/`dependencies`/
  `search_keys`) — decorator fields only.
- The function's signature-through-body (`dependencies`/`docstring`/
  `function_body`) — regenerated together because a dependency change adds
  or removes a *positional argument* in the signature line, not just a
  decorator tuple entry. A `role`-only edit therefore leaves the signature,
  docstring, and body **byte-for-byte** untouched; a `dependencies` edit
  regenerates both regions (the decorator's `depends_on` tuple *and* the
  signature's argument list) since both must stay in sync.

Works on **any** existing feature, agent-created or hand-authored — there is
no ownership restriction on edit (only delete has one; see below).

`updated_fields` in the output lists exactly which input fields were
non-null, so a caller can confirm what actually changed.

## DeleteFeatureTool

Deletes the entire `# PART-START`/`# PART-END`-marked block. Two safety
checks, both `validate_input`-time rejections:

- **Ownership**: only agent-created features (marker-wrapped) may be
  deleted — a hand-authored feature (no markers) is rejected as
  `not_owned`. This mirrors the legacy `tool_executor.py` behavior for
  `delete_cad_feature` exactly (`DELETE_NOT_OWNED`).
- **Usage**: rejected as `in_use` (with `details.referenced_by` listing
  every referencing function name) if the feature is still referenced,
  checked two ways — another feature's `cad_part(depends_on=(...))` tuple
  naming this `semantic_id`, or a direct call to this `function_name`
  anywhere, including `build_model`.

## The usage check is a genuine improvement over legacy, not just a port

Traced `tool_executor.py`'s `apply_plan` deletion path directly: for
`delete_cad_feature`, the only checks are fingerprint staleness and
same-plan modify/delete conflicts — **no reference/usage check exists**.
The planning prompt's implied safety ("don't delete something still
depended on") is not enforced by any code before the delete happens, the
same gap already found and fixed for `delete_parameter` (see
`PARAMETER_TOOLS.md`). `DeleteFeatureTool` genuinely blocks this instead of
allowing it to succeed silently.

## Candidate-scoped mutation

Both require `context.candidate_id` and operate only on
`{project_id}/candidates/cad/{part_id}/{candidate_id}/model.py`
(`feature_generation._candidate_source_path`, shared across every tool in
this package). Neither ever reads or writes accepted source.

## Common structured failures

`ToolFailure` with `error.code == "TOOL_VALIDATION_FAILED"` and a `details`
dict identifying the reason: `missing_identifier`, `not_found`,
`conflicting_identifiers`, `no_changes` (edit only, nothing to change),
`not_owned` (delete only), `in_use` (delete only, with `referenced_by`), or
`missing_candidate`. Malformed raw arguments surface as `TOOL_INPUT_INVALID`
before normalization runs. Unexpected failures return a sanitized
`TOOL_EXECUTION_FAILED` with no raw exception text.

## Legacy migration

`ReplaceCadFeatureBody`/`UpdateCadPartMetadata` (body vs. metadata edits,
two separate legacy operations) and `DeleteCadFeature` (marker-based,
ownership-restricted deletion) are the legacy counterparts, applied via
`tool_executor.py`'s `apply_plan` — a different execution engine from
`applier.py`'s `apply_edit_plan`. Nothing was migrated *out* of that legacy
code this time: unlike `create_feature`'s migration
(`applier._added_feature` → `feature_generation._added_feature`), there was
no single reusable generation-primitive function to extract for edit/delete
— body/metadata replacement there is driven by `targets.py`'s
`collect_target_spans` (general-purpose, multi-target-kind machinery this
package's tools deliberately don't route through), and deletion is an
inline text splice inside a much larger function
(`tool_executor.py`'s `apply_plan`), not a standalone function. Instead,
`edit_feature`/`delete_feature` do their own narrow AST-aware location
(`feature_generation._find_feature`, `_feature_marker_span`) and
regeneration (`_render_feature_decorator`, `_render_feature_definition`),
the same pattern `create_feature`/`create_cad_part` already established,
reusing the *validation* helpers (forbidden-content checks, identifier
rules) that were already shared via `feature_generation.py`.

## Unit-test strategy

`tests/test_cad_feature_edit_delete_unit.py` uses the same in-memory
`FakeRepository` as `create_feature`'s tests, with three fixtures: a base
case (one agent-owned unused feature, one hand-authored feature used by
`build_model`), one where the target is called directly by `build_model`,
and one where the target is referenced via another feature's `depends_on`.
Covers: strict input parsing (all edit fields genuinely optional); missing/
conflicting/not-found identifier rejection; no-op rejection (edit); editing
role-only leaves the body byte-for-byte untouched; editing a hand-authored
feature (edit has no ownership restriction); editing `function_body` and
the declared-vs-referenced parameter cross-check; editing `dependencies`
regenerates the signature; forbidden function-body content; not-owned and
both flavors of in-use rejection (delete); deleting leaves unrelated source
and `build_model` untouched; missing-candidate rejection; sanitized
unexpected-failure handling.

## Integration-test strategy

`tests/test_cad_feature_edit_delete_integration.py` starts from
`CreateCadPartTool`'s empty skeleton and runs a full lifecycle: create a
feature → edit its role → edit its body → create a second feature that
depends on it → confirm delete is blocked with the correct `referenced_by`
and the candidate is unchanged → remove the dependency via `edit_feature` →
confirm delete then succeeds, with `build_model` and all unrelated source
asserted unchanged throughout. Additional tests confirm a deleted
`semantic_id`/`function_name` can be immediately reused by a fresh
`create_feature` call, and that a rejected edit never mutates the candidate.

## Test dependencies

Same as every other tool in this package: `pydantic` and stdlib `unittest`
(`unittest.IsolatedAsyncioTestCase`) — no `pytest-asyncio` or other
test-only dependency.

## Running the tests locally

From the repository root:

```
pytest tests/test_cad_feature_edit_delete_unit.py
pytest tests/test_cad_feature_edit_delete_integration.py
pytest tests/test_cad_feature_edit_delete_unit.py tests/test_cad_feature_edit_delete_integration.py
```

To confirm this change did not regress the shared code it touches
(`feature_generation.py`) or `create_feature`, which now imports its
validation/generation helpers from the same module:

```
pytest tests/test_cad_create_feature_unit.py tests/test_cad_create_feature_integration.py \
       tests/test_cad_editor_core.py tests/test_cad_tool_framework.py
```
