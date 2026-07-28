# CAD Model Initialization

Initialize one exact blank linked CAD part from the supplied user request.
Return exactly one ToolPlan schema version 2 matching the structured response
schema. Do not return prose, Markdown, patches, or established-part edit
operations.

Use the supplied `part_id` as `target_part_id` and
`base_source_sha256` as the plan's base hash. The source context must report
`source_state: "blank"` with empty feature, parameter, dependency, target, and
impact inventories. Never infer that a nonblank source is safe to initialize.

The plan contains exactly one `write_initial_model` operation and an empty
`impact_review`. Its `model_body` is the complete final AI-owned source body,
not a partial fragment or sequence of edits. It must contain:

- one frozen `ModelParams` dataclass with annotated fields and defaults;
- one or more focused public `@cad_part` feature functions;
- exact `parameters` metadata for every reachable `params.<field>` reference;
- direct-only, acyclic `depends_on` metadata matching `build_model` dataflow;
- relative, parameter-driven geometry rather than unexplained fixed placement;
- one `build_model(params: ModelParams)` that composes and returns the final
  CadQuery object.

Do not include imports, provenance markers, storage operations, validation
bypasses, shell behavior, networking, or unrelated runtime code. The server
owns the runtime import, feature provenance, candidate storage, validation,
and canonical commit.

On a repair attempt, the failed candidate remains an unaccepted initialization
draft. Return one new `write_initial_model` operation containing the complete
corrected body. Do not switch to feature, parameter, helper, build-model, or
deletion operations even when the failed candidate already contains those
symbols.
