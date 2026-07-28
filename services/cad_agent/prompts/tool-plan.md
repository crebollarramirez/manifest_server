# Registered CAD Tools

The reasoner may use only the tools in this catalog. The tool executor validates
every argument, applies the complete plan transactionally, and independently
validates the resulting candidate.

## `write_initial_model`

Writes the complete agent-owned body for an exact blank linked CAD part.

Arguments:

- `model_body`: complete source body containing one frozen `ModelParams`, one or
  more decorated public CAD features, and one `build_model`.

The system prepends
`from cadquery_runtime import cad_part, cq, dataclass`. The body cannot contain
imports or system-owned provenance markers. This tool is legal only when the
server-authorized workflow mode is `initial_design`, and it must be the plan's
only operation.

## `replace_parameter_field`

Replaces one existing annotated `ModelParams` field.

Arguments:

- `target_id`: exact `model_parameter` target from the matching
  `existing_parameters` entry;
- `target_fingerprint`: exact editable fingerprint from that same
  `existing_parameters` entry;
- `replacement_source`: one annotated field with a default.

`replacement_source` contains only the annotated field, for example
`leg_height_mm: float = 8.0`. Never use an `owned_model_parameter` target and
never include `CAD-AGENT-*` or `PART-*` provenance markers. Ownership targets
are reserved for deletion.

## `update_cad_part_metadata`

Replaces the bounded metadata of one existing `@cad_part` feature.

Arguments:

- `target_id`: exact metadata target from `allowed_targets`;
- `target_fingerprint`: exact current target fingerprint;
- `role`, `parameters`, `depends_on`, and `search_keys`: complete replacement
  metadata.

The semantic ID and `library="cadquery"` remain immutable.

## `replace_cad_feature_body`

Replaces the statements inside one existing public CAD feature.

Arguments:

- `semantic_id`: stable ID from `existing_features`;
- `target_fingerprint`: fingerprint of that feature's resolved internal
  function-body target;
- `replacement_source`: body statements beginning at column zero.

The server resolves the semantic ID to the current Python function and owns
indentation, the function signature, decorator, and provenance markers.

## `replace_function_body`

Replaces the statements inside one existing private helper.

Arguments:

- `target_id`: exact private-helper function-body target;
- `target_fingerprint`: exact current target fingerprint;
- `replacement_source`: body statements beginning at column zero.

This tool cannot target a public CAD feature or add a new function.

## `add_model_parameter`

Adds one new annotated field to `ModelParams`.

Arguments:

- `name`: new unique parameter name;
- `field_source`: one annotated field with a default.

Imports, decorators, duplicate fields, and invalid Python shapes are rejected.

## `add_private_helper`

Adds one new private synchronous helper.

Arguments:

- `function_name`: new unique name beginning with `_`;
- `function_source`: one complete undecorated function definition.

Imports, decorators, asynchronous functions, and duplicate symbols are
rejected.

## `add_cad_feature`

Adds one new public semantic CAD feature.

Arguments:

- `semantic_id`: new unique stable feature ID;
- `function_name`: new unique public Python function name;
- `role`, `parameters`, `depends_on`, and `search_keys`: semantic metadata;
- `function_source`: one complete undecorated function definition containing
  the feature's first implementation.

The server renders the strict `@cad_part` decorator and provenance markers.
Imports, supplied decorators, unknown parameters, unknown dependencies, and
duplicate identities are rejected.

## `replace_build_model_body`

Replaces only the statements inside the existing `build_model`.

Arguments:

- `target_id`: exact `build_model_target` ID;
- `target_fingerprint`: exact current target fingerprint;
- `replacement_source`: body statements beginning at column zero.

The function signature and system-owned boundaries remain unchanged.

## `delete_model_parameter`

Deletes one model parameter explicitly marked deletable.

Arguments:

- `target_id`: exact `owned_model_parameter` target from `deletable_targets`;
- `target_fingerprint`: exact current target fingerprint.

Required or referenced parameters cannot be deleted.

## `delete_private_helper`

Deletes one private helper explicitly marked deletable.

Arguments:

- `target_id`: exact deletable helper target from `deletable_targets`;
- `target_fingerprint`: exact current target fingerprint.

Referenced or human-owned helpers cannot be deleted.

## `delete_cad_feature`

Deletes one public CAD feature explicitly marked deletable.

Arguments:

- `target_id`: exact deletable whole-feature target from `deletable_targets`;
- `target_fingerprint`: exact current target fingerprint.

Referenced, required, or out-of-scope features cannot be deleted.
