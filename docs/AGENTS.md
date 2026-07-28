## Development

When starting the dev server, use background mode:

```
astro dev --background
```

Manage the background server with `astro dev stop`, `astro dev status`, and `astro dev logs`.

## Documentation

Full documentation: https://docs.astro.build

Consult these guides before working on related tasks:

- [Adding pages, dynamic routes, or middleware](https://docs.astro.build/en/guides/routing/)
- [Working with Astro components](https://docs.astro.build/en/basics/astro-components/)
- [Using React, Vue, Svelte, or other framework components](https://docs.astro.build/en/guides/framework-components/)
- [Adding or managing content](https://docs.astro.build/en/guides/content-collections/)
- [Adding styles or using Tailwind](https://docs.astro.build/en/guides/styling/)
- [Supporting multiple languages](https://docs.astro.build/en/guides/internationalization/)

## Architecture diagram authoring

Architecture diagrams are data-driven. Add or update a strict JSON document in
`src/data/architecture/` and render it with `ArchitecturePage`; do not create
page-specific diagram markup, icons, connector CSS, or positioning code.

### Semantic design language

Every box represents the physical implementation resource, never a convenient
workflow metaphor. Use the edge label and details to describe what the resource
does in a particular interaction.

| Node type | Use it for | Do not use it for |
| --- | --- | --- |
| `database` | Durable relational tables, database-owned state, and RPC-backed job tables such as `edit_jobs`, `generation_jobs`, and `index_jobs`. | A separate message broker or object storage. |
| `queue` | A real broker, queue transport, or independently operated messaging resource. | Postgres rows that happen to be queued, leased, or polled. |
| `storage` | Durable blob/object storage buckets and paths, including the files or artifacts they physically own. | A transient value produced within one stage. |
| `function` | One deterministic architectural operation or stage. | A long-running execution boundary. |
| `worker` | A polling/background process or isolated runtime. | A one-shot deterministic transform. |
| `api` | An inbound API boundary. | A third-party dependency. |
| `external` | A dependency outside Manifest ownership. | A Manifest service exposed to callers. |

`client`, `service`, and `layer` retain their existing meanings. An `icon`
override may make a box more specific, but may never change its semantic type.

### Stored files and artifacts

Do not create a standalone artifact box. A connector always represents a real
interaction with its target, so a producer must write directly to the durable
storage component that owns the file. Name the file or artifact in the edge
label and declare it in that storage node's `contents` list; the shared card
renderer presents those contents inside the storage block.

For example, a build writes `semantic_index.json` to `Index Storage` rather
than sending a request to a `semantic_index.json` box:

```json
{
  "id": "index-storage",
  "label": "Index Storage",
  "type": "storage",
  "contents": ["semantic_index.json"]
}
```

Use a concise `internal` or `file` edge label for a truly transient value when
it needs to be visible between stages, but do not represent that value as a
node. Durable diagnostics that live in a Postgres row remain part of the
appropriate `database` node instead of being recast as object storage.

### Connection language

`A → B` means A initiates a call, sends a message, reads from, writes to, or
otherwise causes the labeled interaction with B. Labels must name the action or
payload, not a vague relationship: use `claim_next_edit_job`, `leased job`,
`validated planning request`, or `structured EditPlan`.

For every storage interaction, arrow direction follows the initiator—not the
direction that bytes happen to travel. A process that reads a file points to
the storage component (`Process → Storage`) with a `read …` label; storage
never points to a process merely because it returns the requested file. A
process that writes a file also points to storage (`Process → Storage`). This
keeps storage passive and makes every arrow consistently answer “who caused
this interaction?”

Choose `protocol` from the implementation mechanism:

- `http` for an API request;
- `database` for a table read/write or database RPC;
- `storage` for object storage read/write;
- `file` for file/artifact movement;
- `event` for asynchronous event delivery;
- `queue` only for an actual queue/broker;
- `internal` for an in-process or service-local stage handoff.

Use `synchronous` for immediate request/RPC/read/write results and
`asynchronous` for later execution, event delivery, polling, or durable
background work. A Postgres enqueue or claim RPC is a synchronous `database`
interaction even when it creates work that runs later; document that later work
with a separate edge.

### Request/response exchanges

Use one `direction: "two-way"` edge only for one cohesive request/response
exchange. Its source is the initiator and it must provide both message objects.
The canvas still renders exactly one arrow—from source to target—to represent
the initiating request. Never draw a reverse arrowhead or reverse motion for a
response: a database, storage resource, API, or external dependency does not
initiate a second request merely by returning data. The response is represented
in the shared connection inspector:

```json
{
  "label": "structured planning exchange",
  "protocol": "http",
  "direction": "two-way",
  "flow": "synchronous",
  "request": {
    "label": "bounded planning context",
    "type": "Responses API structured-output request",
    "exampleBody": "{\"allowed_targets\":[\"part-42\"]}"
  },
  "response": {
    "label": "validated EditPlan",
    "type": "Pydantic EditPlan",
    "exampleBody": "{\"operations\":[]}"
  }
}
```

The shared right-side inspector renders the request as `Source → Target` and
the response as `Target → Source`. One-way edges may include `request` details
for their outbound payload, but must never include `response` details.

Each node must provide:

- a unique `id`, `label`, and `description`;
- a semantic `type`;
- a unique desktop `position` used only as its initial canvas arrangement;
- a unique `mobileOrder` that explicitly authors the narrow-screen narrative;
- an optional `href` for native drill-down navigation;
- an optional shared `icon` override.

Use the semantic type for what a component *is*. For example, a Postgres-backed
job table is `database` even when its claim behavior acts like a queue. Use the
optional icon only to make the component more specific. Available shared icons
are `layers`, `terminal`, `api`, `process`, `service`, `worker`, `database`,
`queue`, `object-storage`, `file-json`, `source-code`, `ast`, `search`,
`editor`, and `external`.

### Service documentation structure

Service overview pages use the shared primitives in
`src/components/service-docs/` instead of recreating summary cards or tables:

- `ServiceAtGlance` for trigger, durable reads/state, produced result, code
  execution, and downstream consumer;
- `BoundaryMatrix` for owns, does not own, and depends on;
- `ModeMatrix` only when execution modes have materially different outcomes;
- `KeyGuarantees` for at most five implementation-backed promises;
- `FailureMatrix` for durable outcome, runtime execution, and downstream effect;
- `OperationsAndSource` for useful controls, observability, paths, and symbols.

Keep the main page concise. Put process mechanics in the node inspector,
boundary-crossing payloads in the connection inspector, and exact mechanics in
source code. Do not copy raw request bodies onto a service overview.

When documenting CAD Editor behavior, preserve these workflow distinctions:

- Name the control plane `NestJS CAD Agent` and the mutation boundary `Python
  CAD Tool Worker`. Nest owns HTTP/WebSocket submission, idempotency, OpenAI
  reasoning, durable orchestration, proof verification, commit, and progress
  replay. Python owns bounded context preparation and transactional
  AST/source-tool execution only. Never collapse them into one worker box.
- `edit_jobs` is the workflow source of truth, `edit_job_events` is the ordered
  public replay log, and `cad_tool_jobs` is the durable Nest-to-Python handoff.
  A WebSocket is only a delivery channel and must never be drawn or described
  as the work queue.
- OpenAI returns a strict versioned plan of registered tool operations. It does
  not write source, execute Python or CadQuery, create validation proof, commit
  canonical source, or complete the edit job.
- CAD `create_part` writes the exact runtime-only blank marker and attempts to
  queue `build_index`; an index-queue failure does not delete the new part.
- The Indexer excludes that exact blank marker, so a fresh index may validly
  contain no parts.
- A linked `requested_part_id` is authoritative and the editor cannot redirect
  the request to a better-ranked feature in another part. Only unlinked requests
  use project-wide semantic resolution.
- Initial design may replace the complete AI-owned body behind the system-owned
  runtime import. It uses the dedicated initialization prompt and exactly one
  `write_initial_model` operation on every generation or repair attempt.
  Established parts never receive the initialization prompt or whole-file
  replacement.
- Established-part editing is represented as a versioned tool plan. It may
  combine validated add, modify, and eligible delete operations for
  ModelParams fields, private helpers, server-decorated CAD features,
  `build_model`, and existing PART regions. Never describe this as unrestricted
  code writing.
- Deletion requires an existing semantic PART region or an explicit CAD-AGENT
  provenance marker. Required runtime imports, `ModelParams`, `build_model`,
  unrelated human-owned source, and out-of-scope parts remain protected.
- One tool plan is applied fully in memory and uploads one candidate only after
  every operation and the final source contract pass. Never depict an
  intermediate operation as a durable partial candidate.
- Live-progress labels describe public milestones such as planning, tool
  execution, validation, repair, commit, reindex, completion, and failure.
  Never expose prompts, source bodies, secrets, or private model reasoning.
- Validation repair uses the latest isolated candidate, its exact previous
  ToolPlan, and structured diagnostics. These temporary repair inputs never
  enter the accepted semantic index; only a validated committed model is
  reindexed.

The architecture diagram is the primary content on every `Service view` page.
`ArchitecturePage` renders it immediately below the service header and lets it
use the full documentation content width. The large, left-aligned page header
is the single visible service/diagram header: its one-sentence summary must
combine the service responsibility with the useful execution context from the
architecture document. Do not display the diagram's smaller internal header or
add a second “System design diagram” heading in service prose. When a node or connection inspector opens,
the shared workspace reserves the right-hand inspector column and gives the
remaining width to the diagram; when no inspector is open, the diagram reclaims
that entire column. Keep prose and service matrices in the narrower shared
content column below the diagram.

### Logical stages, variants, and boundaries

Logical workflow stages may use a `function` variant of `operation`,
`transform`, `gate`, `decision`, or `completion`. Long-running execution
boundaries may use a `worker` variant of `polling`, `sandbox`, or `scheduled`.
Variants refine visual and inspector emphasis; they never change what the
physical node type means. The strict schema rejects a variant on an
incompatible node type.

Use a document `boundaries` entry to show that several stages execute inside
one service, worker, runtime, trust, or transaction boundary. Boundaries are
visual ownership groups, not endpoints: edge `source` and `target` fields must
continue to reference the component that initiates or receives the real
interaction. Nested boundaries declare `parentId`, and every nested member must
also be a member of its parent.

Desktop boundary geometry is derived from current child-card positions, so it
follows dragged stages and is never written to browser-local layout storage.
Mobile renders boundaries as narrative section labels. Authors declare only
member node IDs; never author group coordinates or group-specific CSS.

### Inspector details and source evidence

Use optional node `details` to answer what happens inside a component. Prefer
the following structured fields when supported by the implementation:

- `responsibility`, `ownedBy`, and `runsIn`;
- `trigger` and `preconditions`;
- named `inputs` and `outputs`, plus `durableReads`, `durableWrites`, and
  `downstream`;
- ordered `steps`, `decisionPoints`, and `shortCircuit`;
- `successCondition` and structured `failureConditions`;
- evidence-backed `guarantees`;
- `operations` for timeout, concurrency, retry behavior, observability, and
  runtime controls.

Use edge `details` for the meaning of a boundary crossing: `summary`,
`preconditions`, `operation`, `payload`, `durability`, `failureBehavior`,
`trustBoundary`, and `evidence`. Do not duplicate the existing structured
request/response objects.

Evidence entries contain a repository-relative `path`, optional non-blank
`symbol`, and one kind: `source`, `migration`, `schema`, `test`, or
`configuration`. Absolute paths, URLs, traversal, invented links, and empty
placeholders are invalid. Important claims such as isolation, transaction
semantics, credential removal, hash binding, retry, or lack of retry must cite
the source, migration, schema, test, or configuration that proves them.

Every edge automatically uses the full connector as its shared details target.
Selecting a relationship opens the reusable right-side inspector; documentation
authors never place midpoint controls or popovers. Use `protocol`, `flow`, and
`direction` to select the reusable line and arrow conventions. Use an edge
`href` only when the inspector should link to a durable reference section.

Documentation authors declare components, initial positions, endpoints, and
relationship metadata only. Never author connector coordinates, SVG paths,
handle selections, or page-specific routing CSS. The shared desktop renderer
selects live cardinal handles, separates reverse/parallel relationships, and
reroutes every incident edge while a reader drags a card. Browser-local layout
positions are disposable reader preferences and never belong in architecture
JSON.

Desktop cards are draggable and use a separate native open-link control.
Clicking a card focuses its immediate relationship neighborhood. At widths up
to 52rem, the same document renders as the authored static vertical sequence;
do not create a second mobile diagram or shrink desktop coordinates.

The reusable primitives live in:

- `src/components/architecture/ArchitectureIcon.tsx`
- `src/components/architecture/DiagramBox.tsx`
- `src/components/architecture/ArchitectureDiagram.tsx`
- `src/components/architecture/ArchitectureBoundary.tsx`
- `src/components/architecture/NodeDetailsPanel.tsx`
- `src/components/architecture/ConnectionDetailsPanel.tsx`
- `src/components/architecture/diagram-schema.ts`
- `src/components/architecture/interactive-routing.ts`
- `src/components/architecture/layout-persistence.ts`
- `src/components/architecture/static-layout.ts`

Extend those shared primitives and the strict schema when a genuinely new
visual concept is needed. Keep light/dark, mobile, keyboard, and schema tests
working for every diagram rather than special-casing one documentation page.
