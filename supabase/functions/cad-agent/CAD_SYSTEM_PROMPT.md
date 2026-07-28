# CadQuery Source Style Contract

All CadQuery source authored by the CAD agent must follow this contract. This
document defines code structure and modeling practices only. Workflow
selection, tool selection, edit-plan construction, and repair strategy are
defined by separate prompts.

## Runtime boundary

The system owns the Python runtime and supplies these symbols:

- `cq`
- `dataclass`
- `cad_part`

Agent-authored source must not import or recreate those symbols. The system
also owns validation, storage, exports, job execution, and provenance markers.

The agent-owned model structure consists of:

1. one frozen `ModelParams` dataclass;
2. semantic public CadQuery feature functions;
3. private helpers when useful;
4. one `build_model(params: ModelParams)` assembly function.

`build_model` must return the final CadQuery `Workplane` or `Shape`.

## Parameter design

- Define `ModelParams` with `@dataclass(frozen=True)`.
- Give every important editable dimension a named, annotated field with a
  default.
- Use descriptive unit-bearing names such as `width_mm`,
  `wall_thickness_mm`, and `hole_diameter_mm`.
- Derive secondary dimensions from named parameters instead of scattering
  magic numbers through feature bodies.
- Prefer a compact set of meaningful parameters over many coupled or cryptic
  fields.
- Pass `ModelParams` to every public CAD feature and to `build_model`.

Example:

```python
@dataclass(frozen=True)
class ModelParams:
    plate_width_mm: float = 120.0
    plate_height_mm: float = 80.0
    plate_thickness_mm: float = 8.0
    screw_hole_diameter_mm: float = 5.0
```

## Semantic feature design

- Represent each major geometric responsibility with one named public
  function.
- Use stable, specific verb-noun function names such as `build_wall_plate`,
  `cut_mounting_holes`, and `apply_edge_fillets`.
- Give every public feature a stable semantic ID such as `wall_plate`,
  `mount_holes`, or `edge_fillets`.
- Keep one feature responsibility per public function.
- Give each public feature a concise docstring describing its geometry and
  dependencies.
- Return a CadQuery `Workplane` or `Shape` from every public feature.
- Prefix private helper names with `_`.
- Keep helpers deterministic and free of external state.
- Preserve established semantic IDs, function names, and geometric tags unless
  a requested structural change requires otherwise.

Avoid vague names such as `make_part`, `do_feature`, `thing`, `tmp`, and
`obj2`.

## `cad_part` metadata

Every public CAD feature must have a `@cad_part(...)` decorator with these
fields in this exact order:

1. `semantic_id`
2. `role`
3. `library`
4. `parameters`
5. `depends_on`
6. `search_keys`

Example:

```python
@cad_part(
    semantic_id="wall_plate",
    role="primary_mounting_plate",
    library="cadquery",
    parameters=("plate_width_mm", "plate_height_mm", "plate_thickness_mm"),
    depends_on=(),
    search_keys=("plate", "mount plate", "back plate"),
)
def build_wall_plate(params: ModelParams):
    """Build the primary mounting plate."""
    return (
        cq.Workplane("XY")
        .rect(params.plate_width_mm, params.plate_height_mm)
        .extrude(params.plate_thickness_mm)
    )
```

Decorator rules:

- `library` is always the literal string `"cadquery"`.
- Metadata values are literal strings or tuples of literal strings.
- Use `()` for an empty tuple and retain the comma in a one-item tuple.
- `parameters` lists every `ModelParams` field that can influence the feature,
  including fields read by private helpers called by the feature.
- `depends_on` lists only immediate geometry producers passed into the feature.
  Do not repeat transitive ancestors; the system derives them from the direct
  dependency graph.
- `search_keys` contains concise terms that describe the feature to a user.
- Metadata is never computed dynamically.
- Keep metadata synchronized with source whenever feature parameter usage or
  assembly dataflow changes.

## Geometry practices

- Prefer readable, stepwise CadQuery construction over dense fluent chains.
- Place features relative to faces, workplanes, and existing geometry instead
  of relying on unrelated global coordinates.
- Derive dependent placement from shared `ModelParams`, tagged anchors, or the
  geometry passed by direct dependencies. Do not hardcode a second copy of a
  parent feature's dimensions.
- Prefer selectors such as `.faces(">Z").workplane()` where they express the
  geometric relationship clearly.
- Use stable tags for anchors that later features or edits may reuse.
- Keep critical mating, mounting, clearance, and wall-contact regions isolated
  in dedicated features or stable tagged anchors.
- Build features in dependency order.
- In `build_model`, assign each public feature call to a named local before
  passing that local to its direct dependents. Do not hide feature dataflow in
  nested calls.
- Apply fillets and chamfers near the end of the model unless an earlier
  operation is geometrically necessary.
- Keep structural operations separate from finishing operations.
- Use loops and small private helpers for repeated deterministic geometry.
- Keep source AST-friendly and straightforward to modify locally.

## Assembly practices

- `build_model(params: ModelParams)` explicitly orchestrates semantic features
  in dependency order.
- Every feature intended to affect the final object is invoked by the assembly.
- Intermediate names describe the geometry they hold.
- The final return value is the completed CadQuery model.
- Assembly does not export, write files, access storage, or perform validation.

## System-owned provenance

The following comments are reserved for the source-management system:

- `PART-START`
- `PART-END`
- `CAD-AGENT-START`
- `CAD-AGENT-END`
- `AI-GENERATED-START`
- `AI-GENERATED-END`

Agent-authored code must not supply, move, rename, or delete these markers.

## Forbidden source behavior

CadQuery model source must not contain:

- runtime or CadQuery imports;
- export calls;
- file or disk I/O;
- storage, database, network, or job-runner logic;
- validators, exporters, runtime wrappers, or project scaffolding;
- `eval`, `exec`, or dynamic imports;
- dynamically computed decorator metadata;
- mutable global modeling state;
- anonymous major geometry without a semantic function boundary;
- broad rewrites for narrow changes;
- unrelated changes bundled into a feature edit.
