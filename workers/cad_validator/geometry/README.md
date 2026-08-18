# Geometry layer

Owns everything between "a candidate's CAD source ran" and "here are bounded
geometric facts about it": normalizing a build result into one root shape,
serializing that shape as a native B-rep, persisting it, deriving measurements
from it, and comparing two candidates.

It knows nothing about the agent, its tools, its prompts, its plan, or the
semantic index. Nothing in it reads CAD source as text.

---

## Sources of truth

The distinction this layer exists to keep straight:

| Thing | What it is |
| --- | --- |
| `model.py` | **Canonical design source.** The reproducible, parametric, agent-authored definition. Editing geometry means editing this. |
| B-rep artifact (`.brep`) | **Authoritative built geometry.** Exactly what one exact version of `model.py` produced on one run. |
| `GeometrySnapshot` | **Derived observation.** A compact, deterministic summary of one artifact. A cache, not a source. |
| Mesh (STL/GLB) | **Not the authoritative CAD representation.** Produced downstream by `cad_exporter` for display and print. Nothing in this layer reads one. |

The artifact does not replace `model.py`. It is what `model.py` produced. If
the two ever disagree, `model.py` is right and the artifact is stale.

Before this layer existed, `GeometrySnapshot` *was* the geometry: the CadQuery
shape lived inside a sandboxed subprocess and was destroyed when that process
exited, so a handful of numbers was the only surviving record of what a
candidate physically was.

---

## Candidate geometry lifecycle

```
                       candidate model.py
                              │
                    static safety validation        cad_ast_validator
                              │
                    execute in sandbox              subprocess_sandbox
                              │
                       build_model(params)
                              │
                     Workplane | Shape
                              │
                    CadGeometryExtractor            extraction.py
                              │
                       root cq.Shape
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
       serialize_root                  GeometryAnalyzer
       (artifact.py)                    (analyzer.py)
              │                               │
         model.brep                    GeometrySnapshot
              │                               │
   ─ ─ ─ ─ ─ ─│─ ─ ─ ─ sandbox boundary ─ ─ ─ │─ ─ ─ ─ ─ ─
              │                               │
     GeometryArtifactStore           GeometrySnapshotStore
              │                               │
   geometry_artifacts row ◄───────── geometry_snapshots row
   + object in 3dProjects              (geometry_artifact_id)
              │                               │
              └───────────────┬───────────────┘
                              ▼
                       GeometryEngine                engine.py
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
            snapshot                  comparison
                 │                         │
                 └────────────┬────────────┘
                              ▼
             geometry_check_job / validate_cad_job
                              │
                  generation_jobs.result (JSON)
                              │
                        CheckGeometry
                              │
                           Agent3D
```

Geometry is produced by the **candidate build lifecycle**, not by agent
reasoning. Both job types run the sequence above:

- **`validate_cad`** runs on every candidate mutation and now persists the
  artifact and snapshot it produced.
- **`geometry_check`** (queued by the agent's `check_geometry` tool) normally
  finds that work already done and reuses it, executing the candidate only when
  nothing has measured that exact source hash.

---

## Major components

| Component | File | Responsibility |
| --- | --- | --- |
| `CadGeometryExtractor` | `extraction.py` | `build_model` result → one normalized root `cq.Shape`. Persists nothing, compares nothing. |
| `GeometryAnalyzer` | `analyzer.py` | root shape → `GeometrySnapshot`. The only code that reads numbers off OCCT topology. |
| `build_geometry` | `build.py` | The in-sandbox sequence: normalize → serialize → measure. Used by both runners. |
| `GeometryArtifact` | `artifact.py` | Artifact provenance; `serialize_root` / `load_root` / `verify_digest`. |
| `GeometryArtifactStore` | `artifact_store.py` | Uploads `.brep` bytes, records rows, reloads and integrity-checks artifacts. |
| `GeometrySnapshotStore` | `snapshot_store.py` | Snapshot cache lookup and insert; owns the persisted column vocabulary. |
| `GeometryEngine` | `engine.py` | The service boundary. Derive, reuse, resolve, load, compare. |
| `execute_and_measure` | `execution.py` | Bridge to this worker's sandbox and AST gate. |
| `compare_geometry` | `comparison.py` | Snapshot-level deltas and warnings, with all tolerances centralized. |
| `CheckGeometry` | `workers/agent_3d/tools/geometry/geometry_tools.py` | Agent-facing interface. Queues a job, polls, shapes the result. Owns no geometry logic. |

`GeometryEngine` is the rule: **other layers do not know how B-rep files are
stored, how they are loaded, or how OCCT topology is represented.**

---

## B-rep extraction

`build_model(params)` returns either a CadQuery `Workplane` or a bare `Shape`.
Normalization:

- **`Workplane`** → `cq.Compound.makeCompound(...)` over **every shape on the
  stack**. Not the first value, and not only the solids. A model that
  legitimately builds two disjoint bodies keeps both.
- **`Shape`** → used directly.
- Stack `Vector`s (from `pushPoints`, `moveTo`) are filtered out — only shapes
  can be compounded.
- A root with no solids raises `GEOMETRY_BUILD_ERROR`; a non-CadQuery return
  raises `BUILD_MODEL_RETURN_ERROR`.

Normalization deliberately does **not** reach up the Workplane parent chain.
`Workplane.findSolid()` would find the body a points-only stack descends from,
but the pre-refactor path measured the stack and this one does too — measuring
a different shape than the build left behind would be a behavior change wearing
a refactor's clothes.

The analyzer measures the **solids of** the root rather than the root itself. A
stack can carry construction wires and sketch faces alongside the bodies;
counting those would make `face_count` describe the build's scaffolding instead
of the part. The root stays the authoritative record of everything the build
produced, and the snapshot describes the material.

### Why `model.py` does not export its own B-rep

Serialization is a **system responsibility**. Agent-authored CAD source defines
geometry and nothing else — `cad_ast_validator` forbids it from calling
`exporters.export` at all. If a candidate wrote its own artifact it could skip,
fake, or corrupt the record of what it built, and the artifact would stop being
evidence. The runtime writes it, from the same root object it measured.

### Crossing the sandbox boundary

Unchanged by this layer: argv in, a JSON result file out, plus files on the
shared filesystem. The parent passes `--brep <path>`; the child writes the file;
the parent uploads it. The B-rep is never pickled, never base64'd into JSON, and
never placed anywhere an agent-facing payload could pick it up.

---

## Persistence and identity

Three identifiers that are routinely confused and must not be:

| Identifier | Identifies | Notes |
| --- | --- | --- |
| `candidate_id` | Which candidate. | Is the **edit-job id** — there is no candidates table. `null` for accepted (committed) source. |
| `source_hash` (`source_sha256`) | Which source produced this geometry. | The cache key, with `geometry_checker_version`. Re-verified against actual stored bytes before anything derived from them counts as evidence. |
| `artifact_digest` | Which exact bytes are stored. | Integrity and identity of the **file**. |

**`artifact_digest` is not a geometry identity.** OCCT's B-rep serialization is
deterministic for a given construction but not canonical across constructions.
A 10 mm cube built with `.box()` and the same cube built with
`.rect().extrude().translate()` are identical in volume, bounding box, and every
count — and serialize to different bytes with different digests (2807 vs 1966
bytes on CadQuery 2.7.0 / OCCT 7.8.1). Nothing may infer *"different digest
therefore different geometry."* `tests/test_geometry_artifact.py` pins this as a
standing counterexample.

### Where things live

Artifact paths are derived from the source path, so one rule covers the live
candidate, the `original/` backup, and accepted part source, and provenance stays
visible in the path:

```
{project}/candidates/cad/{part}/{edit_job}/model.py
    -> {project}/candidates/cad/{part}/{edit_job}/geometry/{source_sha256}.brep

{project}/parts/cad/{part}/model.py
    -> {project}/parts/cad/{part}/geometry/{source_sha256}.brep
```

Bytes go in the `3dProjects` bucket; `geometry_artifacts` holds a **reference**,
never the topology, so no `select *` can put native geometry one join away from
an agent-facing payload.

### Candidate isolation

Isolation is a property of content addressing, not a permission check. Geometry
resolves by source hash; two candidates with byte-identical source share it
deliberately, and two whose source differs at all have different hashes and
cannot reach each other's artifact. Downloaded bytes are re-hashed and required
to match before use, so a path that no longer holds the content it was recorded
for resolves to nothing rather than attributing one candidate's geometry to
another.

---

## Geometry snapshot

Derived from the normalized root, before serialization. (Reloading an artifact
shifts floats by roughly 1e-13 relative, so a re-derived snapshot would not be
byte-identical to the stored one. Far inside the comparison tolerances, but it
is why derivation happens once.)

`execution_ok`, `geometry_valid`, `error_message`, `diagnostics`, `volume_mm3`,
`surface_area_mm2`, `bounding_box`, `center_of_mass`, `solid_count`,
`face_count`, `edge_count`, `vertex_count`, `planar_faces`,
`non_planar_face_count`, `sharp_edge_count`.

Field-by-field meanings, tolerances, and the `PGRST204` hazard around the column
vocabulary are in [`../GEOMETRY_CHECK.md`](../GEOMETRY_CHECK.md).

Two properties worth stating here:

- **Absence is not zero.** A snapshot for source that never executed reports
  `null`, and an empty `planar_faces` with a `null` count is a different claim
  from "this part has no curved faces".
- **Execution and validity are separate.** Source can run cleanly and still
  produce degenerate or absent geometry.

---

## Candidate comparison

`GeometryEngine.compare(previous, current)` diffs two snapshots and derives
warnings. Deltas: `volume_mm3`, `volume_percent`, `bbox_changed`,
`center_of_mass_distance_mm`, `solid_count`, `face_count`, `edge_count`,
`sharp_edge_count`, `validity_changed`. Warnings: `NO_SOLIDS`,
`GEOMETRY_BECAME_INVALID`, `SOLID_COUNT_CHANGED`, `LARGE_BOUNDING_BOX_CHANGE`,
`NO_GEOMETRIC_CHANGE`.

The previous side is the candidate immediately before the latest mutation,
chained through `edit_jobs.last_checked_source_sha256` so B→C compares against
B and not against A. See [`../GEOMETRY_CHECK.md`](../GEOMETRY_CHECK.md).

The comparison is deliberately still **snapshot-level**. What changed is that
both sides now originate from exact candidate-bound native geometry rather than
from independently recomputed numbers. B-rep-to-B-rep change detection is future
work; inventing it now would replace a contract that is understood with one that
is not.

---

## Failure behavior

Structured codes, never raw CadQuery/OCCT stack traces. Full diagnostics stay in
container logs; the agent gets a bounded, located message.

| Code | Meaning |
| --- | --- |
| *(AST report)* | Source failed static safety checks and was not executed. |
| `IMPORT_ERROR` / `CADQUERY_RUNTIME_ERROR` | `build_model` raised, located to a `model.py` line where possible. |
| `BUILD_MODEL_RETURN_ERROR` | Unsupported build result — not a Workplane or Shape. |
| `GEOMETRY_BUILD_ERROR` | No usable geometry: the result contains no solid. |
| `GEOMETRY_INVALID` | A shape exists but OCCT reports it degenerate or unsound. |
| `BREP_EXPORT_FAILED` | Geometry was built and measured; serialization failed. |
| `GEOMETRY_ARTIFACT_UNAVAILABLE` | No artifact row, unreadable object, or a digest mismatch. |
| `SNAPSHOT_DERIVATION_FAILED` | The root exists and measuring it raised. |

Two degradation rules:

- **A failed artifact upload never costs the snapshot.** Persistence failure
  means later bounded queries cannot reach that candidate's topology; it does
  not mean the measurement that succeeded should be thrown away.
- **Geometry persistence never fails a validation.** A source that validated is
  not reported invalid because a bucket was unreachable. This is the direct
  lesson of the `PGRST204` incident recorded in
  `supabase/migrations/20260712130000_geometry_snapshot_diagnostics.sql`, where
  a re-raised persistence error escaped its handler and took the worker down.

---

## Relationship to the semantic index

Two layers, deliberately separate:

```
Semantic index  ->  what does this feature mean?
                    semantic_id, role, parameters, dependencies, dependents

Geometry layer  ->  what physically exists?
                    solids, faces, edges, volume, position
```

Nothing here reads a `semantic_id`, and the index holds no topology. There is
no mapping from `semantic_id` to face or edge indices, and this refactor
deliberately did not introduce one — a future semantic-geometry provenance layer
may connect them, and that is out of scope.

---

## Future direction

**Not implemented.** Described so the architecture is not accidentally shaped in
a way that would prevent it. Nothing below exists today.

The intended model for geometry queries:

```
Agent  ->  bounded structured query  ->  GeometryEngine
                                              │
                                            B-rep
                                              │
                                    small structured answer  ->  Agent
```

**Raw B-rep topology is never dumped into LLM context.** Not now, not later.
A part with a thousand faces yields a query result of three references, not a
thousand entities.

Progressive disclosure, mirroring what the semantic-index tools already do
(search/summarize, then retrieve exactly what is needed):

1. Geometry summary — what the snapshot already provides.
2. Relevant topology search — *"cylindrical faces near 5 mm diameter"* → 3 hits.
3. Specific measurement — *"distance between hit 1 and hit 2"*.
4. Detailed inspection of one named reference.

**Artifact-scoped references.** Future topology results would be scoped to the
artifact they came from:

```
GeometryRef { artifact_id, topology_type, local_reference }
```

`artifact abc123, face 17` means *"face 17 within artifact abc123"* — never
*"the permanent identity of this face"*. B-rep topology renumbers after CAD
operations, so face and edge indices are **not stable across candidates**. No
current API accepts or returns one. Persistent topological naming is not
implemented and is out of scope.

`GeometryEngine.load_root()` is the seam all of this would sit on. It is
implemented and tested today, and **no tool calls it** — it exists so the seam is
proven rather than speculative.

---

## Testing

From the repository root:

```bash
python -m pytest tests/test_geometry_inspection.py tests/test_geometry_comparison.py \
  tests/test_geometry_extraction.py tests/test_geometry_artifact.py \
  tests/test_geometry_engine.py tests/test_cad_geometry_check_job.py \
  tests/test_cad_validation_geometry_artifact.py tests/test_cad_check_geometry_unit.py \
  tests/test_geometry_check_migration.py tests/test_check_geometry_catalog.py -v
```

| File | What it validates |
| --- | --- |
| `test_geometry_inspection.py` | **Parity gate.** Unchanged from before the refactor; it passes through the transitional shim, proving the B-rep path reproduces the old measurement contract exactly. |
| `test_geometry_comparison.py` | **Parity gate.** Same, for comparison and tolerances. |
| `test_geometry_extraction.py` | Workplane/Shape/multi-solid normalization, controlled failures, and metric parity against the legacy path across five model shapes. |
| `test_geometry_artifact.py` | Serialization, digest determinism, the identical-geometry/different-digest counterexample, round-trip fidelity, corrupted bytes. |
| `test_geometry_engine.py` | Cache reuse, candidate isolation, comparison deltas, artifact loading, degraded persistence. |
| `test_cad_geometry_check_job.py` | The geometry-check job end-to-end against real CadQuery source, including artifact persistence and that no raw topology reaches the report. |
| `test_cad_validation_geometry_artifact.py` | That candidate *validation* produces the artifact, that the report shape is unchanged, and that persistence failure never fails a valid candidate. |
| `test_cad_check_geometry_unit.py` | The agent-facing `check_geometry` tool contract. |
| `test_geometry_check_migration.py` | Schema/code contract: every persisted field has a column, the artifact table's keys and constraints, digest-is-not-a-cache-key. |

Full suite:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

Real CadQuery objects are used wherever geometry semantics are under test.
Supabase is always a hand-written fake — there is no `unittest.mock` anywhere in
these files.

---

## Transitional modules

`../geometry_inspection.py` and `../geometry_comparison.py` are **re-export
shims** over this package. They remain deliberately: they let the two parity
test files above stand unchanged as proof of behavioral equivalence, and the
Dockerfile and docs site still name them. Remove them once those references move
here. Nothing new should import them.
