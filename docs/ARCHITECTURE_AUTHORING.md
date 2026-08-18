# Architecture Authoring Guide

This document defines how Manifest architecture diagrams are authored.

Use it when creating or modifying:

- architecture JSON in `src/data/architecture/`;
- architecture node or connection metadata;
- architecture boundaries;
- node and connection inspector content;
- shared architecture diagram primitives or schema behavior.

For the higher-level service documentation standard, follow `AGENT.md`.

The goal of an architecture diagram is to make service boundaries, execution flow, durable state, and important interactions understandable without reproducing the implementation.

**Represent the architecture that physically exists. Do not use convenient workflow metaphors when they misrepresent implementation resources.**

---

# 1. Authoring Model

Architecture diagrams are data-driven.

Add or update a strict JSON architecture document in:

```text
src/data/architecture/
```

Render architecture documents through `ArchitecturePage`.

Do not create page-specific:

- diagram markup;
- node components;
- icons;
- connector CSS;
- SVG paths;
- connector coordinates;
- handle-selection logic;
- positioning code;
- routing logic;
- mobile diagrams.

Shared architecture components own rendering and interaction.

Documentation authors declare:

- nodes;
- semantic node types;
- initial desktop positions;
- mobile narrative order;
- boundaries;
- connections;
- relationship metadata;
- inspector details;
- source evidence.

---

# 2. Diagram Scope

A service architecture diagram should communicate the smallest useful architectural story.

Include components when they are important to understanding:

- how work enters the service;
- where orchestration occurs;
- where code executes;
- where durable state lives;
- which external systems participate;
- how work crosses service boundaries;
- what important artifacts are produced;
- what consumes the result.

Do not add a node merely because a class, function, file, or temporary value exists.

Prefer a diagram that answers:

```text
Who initiates the work?
        ↓
Where is it coordinated?
        ↓
Where is it executed?
        ↓
What durable resources are read or written?
        ↓
What leaves the service?
```

Detailed mechanics belong in inspectors or source code.

---

# 3. Semantic Node Types

Every node represents a physical implementation resource or meaningful execution stage.

Use the semantic type for what the component **is**, not what it resembles during a workflow.

| Node type | Use it for | Do not use it for |
| --- | --- | --- |
| `database` | Durable relational tables, database-owned state, and RPC-backed job tables | A conceptual queue or object storage |
| `queue` | A real broker, queue transport, or independently operated messaging resource | Postgres rows that happen to be queued, leased, or polled |
| `storage` | Durable blob/object storage buckets and paths | Temporary values or database rows |
| `function` | One deterministic architectural operation or stage | A long-running execution boundary |
| `worker` | A polling/background process or isolated runtime | A one-shot deterministic transform |
| `api` | An inbound API boundary | A third-party dependency |
| `external` | A dependency outside Manifest ownership | A Manifest-owned service |
| `client` | A client application or caller | Internal service execution |
| `service` | A service-level execution boundary | A single operation |
| `layer` | A meaningful logical architectural layer | Arbitrary visual grouping |

An icon may make a node more specific but may never change its semantic meaning.

---

# 4. Node Identity

Every node must provide:

- a unique `id`;
- a concise `label`;
- a useful `description`;
- a semantic `type`;
- a unique desktop `position`;
- a unique `mobileOrder`.

Optional fields may include:

- `href`;
- compatible `variant`;
- shared `icon`;
- `contents`;
- structured `details`.

Use stable IDs.

Prefer:

```json
{
  "id": "cad-tool-worker",
  "label": "Python CAD Tool Worker"
}
```

Avoid IDs based on display order:

```json
{
  "id": "node-7"
}
```

Node identity should survive layout changes.

---

# 5. Labels and Descriptions

Node labels should name the actual architectural resource.

Prefer:

```text
NestJS CAD Agent
Python CAD Tool Worker
Edit Job Store
Index Storage
OpenAI
Validation Sandbox
```

Avoid vague labels:

```text
Processor
System
Logic
Data
Handler
Thing
```

Descriptions should answer what the component is responsible for in one concise statement.

Do not turn descriptions into implementation walkthroughs.

---

# 6. Logical Function Variants

A `function` node may use these variants:

- `operation`;
- `transform`;
- `gate`;
- `decision`;
- `completion`.

Examples:

```text
Apply Tool Plan       → operation
Build Semantic Index  → transform
Validate Candidate    → gate
Resolve Target        → decision
Commit Candidate      → completion
```

Variants refine visual and inspector emphasis.

They never change the physical semantic type.

Do not use a function variant on an incompatible node type.

---

# 7. Worker Variants

A `worker` may use:

- `polling`;
- `sandbox`;
- `scheduled`.

Examples:

```text
CAD Tool Worker       → polling
Validation Runtime    → sandbox
Cleanup Worker        → scheduled
```

Use the variant only when it communicates a meaningful execution characteristic.

---

# 8. Architecture Boundaries

Use document `boundaries` when several nodes execute inside one meaningful:

- service;
- worker;
- runtime;
- trust boundary;
- transaction boundary.

Boundaries visually communicate ownership.

They are not endpoints.

Connections must still reference the real node that initiates or receives the interaction.

Authors declare boundary membership only.

Do not author:

- boundary coordinates;
- boundary SVG;
- boundary-specific positioning CSS.

Desktop boundary geometry is derived from the current child-card positions.

On mobile, boundaries become narrative section labels.

---

# 9. Nested Boundaries

Nested boundaries declare `parentId`.

Every member of a nested boundary must also belong to its parent boundary.

Conceptually:

```text
NestJS CAD Agent
┌───────────────────────────────────┐
│ Planning Runtime                  │
│ ┌───────────────────────────────┐ │
│ │ Target Resolution             │ │
│ │ Plan Construction             │ │
│ └───────────────────────────────┘ │
│                                   │
│ Commit Pipeline                   │
└───────────────────────────────────┘
```

Do not create nesting solely for visual decoration.

Use it only when the nested ownership or runtime relationship is architecturally meaningful.

---

# 10. Durable Files and Artifacts

Do not create standalone artifact nodes.

A durable artifact belongs to the storage resource that physically owns it.

For example:

```json
{
  "id": "index-storage",
  "label": "Index Storage",
  "type": "storage",
  "contents": ["semantic_index.json"]
}
```

Represent:

```text
Indexer → Index Storage
          write semantic_index.json
```

Do not represent:

```text
Indexer → semantic_index.json → Index Storage
```

The artifact is content of the storage resource, not an independently executing architectural component.

---

# 11. Database-Owned State

Durable state stored in Postgres remains part of a `database` node.

This includes job tables even when their behavior resembles queueing.

For example:

```text
edit_jobs
edit_job_events
cad_tool_jobs
generation_jobs
index_jobs
```

If these are implemented as database tables or RPC-backed database state, represent them as `database`.

Do not use `queue` merely because rows are:

- queued;
- leased;
- claimed;
- polled;
- retried.

Use `queue` only for an actual queue or broker resource.

---

# 12. Transient Values

Do not create nodes for temporary values produced within one stage.

Examples:

```text
validated request
candidate source string
resolved semantic ID
ToolPlan
diagnostic payload
```

If a transient value is important to understanding a boundary crossing, put it in the edge label or edge details.

Example:

```text
Planner → Tool Executor
validated ToolPlan
```

A concise `internal` or `file` interaction may represent a transient handoff when necessary.

Do not imply durability unless the value is actually persisted.

---

# 13. Connection Semantics

Every edge answers:

> Who initiated this interaction, and what did they do?

`A → B` means A initiates a call, sends a message, reads from, writes to, or otherwise causes the labeled interaction with B.

This rule is consistent across:

- APIs;
- services;
- databases;
- storage;
- workers;
- external dependencies.

The direction represents the **initiator**, not the direction bytes happen to travel.

---

# 14. Connection Labels

Labels should name the action or payload.

Prefer:

```text
submit edit request
claim_next_edit_job
leased CAD tool job
validated planning request
structured EditPlan
read candidate source
write semantic_index.json
commit validated source
```

Avoid:

```text
uses
calls
data
request
response
connects to
interaction
```

A reader should understand the relationship from the label without opening the inspector.

---

# 15. Storage Interaction Direction

Storage is passive.

A process reading storage points **to the storage resource**:

```text
Process → Storage
read model.py
```

A process writing storage also points **to the storage resource**:

```text
Process → Storage
write candidate source
```

Do not reverse the edge because the requested bytes are returned to the caller.

The edge represents who initiated the operation.

---

# 16. Database Interaction Direction

The same initiator rule applies to databases.

For example:

```text
NestJS CAD Agent → Edit Job Store
create edit job
```

and:

```text
Python CAD Tool Worker → CAD Tool Job Store
claim_next_tool_job
```

Do not point the database toward a worker merely because a query returns a row.

---

# 17. Protocol

Choose `protocol` from the actual implementation mechanism.

Use:

- `http` — HTTP/API interaction;
- `database` — table read/write or database RPC;
- `storage` — object-storage read/write;
- `file` — explicit file/artifact movement;
- `event` — asynchronous event delivery;
- `queue` — actual queue/broker interaction;
- `internal` — in-process or service-local handoff.

Do not select a protocol based on conceptual behavior.

For example, a Postgres enqueue RPC is:

```text
protocol: database
```

not:

```text
protocol: queue
```

---

# 18. Flow

Use:

```text
synchronous
```

when the initiator receives the result as part of the immediate operation.

Examples:

- HTTP request/response;
- database RPC;
- database read/write;
- object-storage read/write;
- in-process function handoff.

Use:

```text
asynchronous
```

when execution or delivery happens later.

Examples:

- polling;
- background execution;
- event delivery;
- durable queued work;
- scheduled work.

A synchronous database write that creates future work remains a synchronous database interaction.

Represent the later background execution with another relationship.

---

# 19. Request/Response Exchanges

Use one `direction: "two-way"` edge for one cohesive request/response exchange.

The source remains the initiator.

Example:

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

The diagram renders one initiating arrow:

```text
Source → Target
```

The connection inspector explains:

```text
Request
Source → Target

Response
Target → Source
```

Do not draw a second reverse edge merely to represent the response.

---

# 20. One-Way Connections

A one-way edge may contain request/payload details.

It must not contain response details.

Use one-way relationships for interactions such as:

- event publication;
- durable write;
- enqueue operation without a cohesive response contract worth documenting;
- fire-and-forget notification;
- internal stage handoff.

---

# 21. Node Inspector Details

Use optional node `details` to explain what happens inside a component.

Prefer structured fields when supported by the implementation:

- `responsibility`;
- `ownedBy`;
- `runsIn`;
- `trigger`;
- `preconditions`;
- `inputs`;
- `outputs`;
- `durableReads`;
- `durableWrites`;
- `downstream`;
- `steps`;
- `decisionPoints`;
- `shortCircuit`;
- `successCondition`;
- `failureConditions`;
- `guarantees`;
- `operations`.

Do not fill every field merely because it exists.

Include details that materially improve understanding.

---

# 22. Node Responsibilities

`responsibility` should summarize what the component owns.

Example:

```text
Applies the validated CAD tool plan transactionally against the isolated candidate source.
```

Avoid:

```text
Handles CAD stuff.
```

Do not duplicate the entire service overview inside each node.

---

# 23. Preconditions and Triggers

Use `trigger` for what causes the component to run.

Use `preconditions` for conditions that must already be true.

Example:

```text
Trigger:
A leased cad_tool_jobs record is available.

Preconditions:
- job belongs to the current project and part
- bounded source context is available
- requested tool operations are registered
```

Do not turn preconditions into speculative validation rules.

They must reflect implementation behavior.

---

# 24. Inputs and Outputs

Use named inputs and outputs when they clarify the contract.

Prefer semantic names:

```text
Input:
Validated ToolPlan

Output:
Candidate source + structured execution result
```

Avoid dumping complete schemas into the inspector.

Use source evidence when exact schema definitions matter.

---

# 25. Durable Reads and Writes

Use `durableReads` and `durableWrites` only for persisted state.

Examples:

```text
durableReads:
- canonical model.py
- cad_tool_jobs row

durableWrites:
- candidate model.py
- tool execution result
```

Do not list temporary local variables or in-memory intermediate source as durable state.

---

# 26. Ordered Steps

Use `steps` for a concise sequence inside a component when that sequence is architecturally meaningful.

Prefer roughly 3–8 steps.

Example:

```text
1. Claim the durable tool job.
2. Load bounded candidate context.
3. Validate the registered operations.
4. Apply the complete plan in memory.
5. Run the final source contract.
6. Upload one candidate.
7. Return structured execution evidence.
```

Do not reproduce individual source-level function calls.

---

# 27. Decision Points

Use `decisionPoints` when branching behavior materially changes the architecture.

Examples:

- linked vs unlinked part targeting;
- initial design vs established-part edit;
- validation success vs repair;
- retryable vs terminal failure.

Do not create decision points for ordinary implementation conditionals.

---

# 28. Short Circuits

Use `shortCircuit` when a component can intentionally stop the normal workflow.

Examples:

- invalid candidate prevents commit;
- authoritative requested part is missing;
- final source contract fails;
- no repair attempts remain.

Document the resulting architectural effect, not every exception type.

---

# 29. Success Conditions

Use `successCondition` to define the meaningful completion state of the component.

Prefer:

```text
The complete tool plan has been applied, the final source contract passes, and one isolated candidate has been uploaded.
```

Avoid:

```text
Function returns successfully.
```

---

# 30. Failure Conditions

Use structured `failureConditions` for failures that matter to system behavior.

Useful failures include:

- no durable write occurs;
- job becomes terminal;
- candidate is discarded;
- retry is scheduled;
- canonical source remains unchanged;
- downstream indexing is skipped.

Do not catalog every internal exception.

---

# 31. Guarantees

Use `guarantees` only for important implementation-backed promises.

Examples:

- canonical source is unchanged until validation succeeds;
- a tool plan produces at most one durable candidate;
- linked part targeting cannot redirect across parts;
- only committed source enters the accepted semantic index.

Important guarantees require source evidence.

Do not infer guarantees from naming or intended architecture.

---

# 32. Operations

Use `operations` for runtime facts such as:

- timeout behavior;
- concurrency;
- retry policy;
- polling;
- scheduling;
- observability;
- runtime controls.

Keep operational details concise.

If the exact value is configuration-driven, cite the configuration rather than copying a value that may drift.

---

# 33. Connection Inspector Details

Use edge `details` to explain a boundary crossing.

Prefer:

- `summary`;
- `preconditions`;
- `operation`;
- `payload`;
- `durability`;
- `failureBehavior`;
- `trustBoundary`;
- `evidence`.

Connection details should answer:

```text
Why does this interaction exist?
What crosses the boundary?
Is it durable?
What happens if it fails?
What proves this behavior?
```

---

# 34. Do Not Duplicate Request/Response Metadata

When a two-way edge already has structured:

- `request`;
- `response`;

do not repeat those bodies inside `details`.

Use `details` for architectural meaning.

Use `request` and `response` for the concrete exchange.

---

# 35. Source Evidence

Important architectural claims must cite repository evidence.

Evidence entries contain:

- repository-relative `path`;
- optional non-blank `symbol`;
- one `kind`.

Allowed evidence kinds:

```text
source
migration
schema
test
configuration
```

Use evidence for claims such as:

- isolation;
- transaction semantics;
- credential removal;
- hash binding;
- retry behavior;
- lack of retry;
- authoritative state;
- commit behavior;
- durability guarantees.

Do not use:

- absolute paths;
- URLs;
- `../` traversal;
- invented files;
- invented symbols;
- empty placeholders.

If source evidence cannot be found, weaken or remove the claim.

---

# 36. Architecture Must Match Current Implementation

Do not document intended future architecture as if it already exists.

Before adding a meaningful node, edge, guarantee, failure behavior, or ownership claim:

1. inspect the relevant implementation;
2. identify the real execution/storage boundary;
3. identify authoritative state;
4. identify the actual interaction mechanism;
5. attach evidence where the claim is important.

Documentation should follow the codebase.

Do not change production architecture merely to make a diagram easier to explain.

---

# 37. Service Page Layout

On every Service view:

1. render the large service header;
2. render the architecture diagram immediately below it;
3. render concise service documentation below the diagram.

The service header is the single visible diagram/service heading.

Its one-sentence summary should combine:

- service responsibility;
- useful execution context.

Do not display:

- the diagram's smaller internal heading;
- a second `System design diagram` heading;
- redundant introductory prose.

---

# 38. Diagram Width and Inspector Behavior

The architecture diagram uses the full documentation content width.

When no inspector is open:

```text
┌─────────────────────────────────────────────────────────┐
│                    Architecture Diagram                 │
└─────────────────────────────────────────────────────────┘
```

When a node or connection inspector opens:

```text
┌────────────────────────────────────┬────────────────────┐
│                                    │                    │
│          Diagram Workspace         │     Inspector      │
│                                    │                    │
└────────────────────────────────────┴────────────────────┘
```

The shared workspace reserves the inspector column.

The remaining width belongs to the diagram.

When the inspector closes, the diagram reclaims the full width.

Do not implement this behavior per page.

---

# 39. Desktop Interaction

Desktop cards are draggable.

Dragging changes reader-local layout only.

Browser-local positions are disposable preferences.

They must never be written back into architecture JSON.

The authored desktop `position` is only the initial arrangement.

Desktop cards use a separate native open-link control when `href` is present.

Clicking a card focuses its immediate relationship neighborhood.

---

# 40. Connector Routing

Documentation authors do not define connector geometry.

Never author:

- SVG path coordinates;
- connector bend points;
- source handles;
- target handles;
- reverse-arrow geometry;
- parallel-edge offsets.

The shared renderer:

- selects live cardinal handles;
- separates reverse and parallel relationships;
- reroutes incident edges when cards move.

Authors define only:

- source;
- target;
- protocol;
- flow;
- direction;
- labels;
- structured metadata.

---

# 41. Relationship Selection

Every connector is its own shared details target.

Selecting a relationship opens the reusable right-side connection inspector.

Do not create:

- midpoint buttons;
- relationship popovers;
- page-specific connection dialogs.

Use an edge `href` only when the inspector should link to a durable reference page.

---

# 42. Mobile Architecture

At widths up to `52rem`, the same architecture document renders as an authored static vertical narrative.

Do not:

- create a second mobile diagram;
- shrink desktop coordinates into a tiny canvas;
- maintain separate mobile architecture data.

Use `mobileOrder` to explicitly author the narrow-screen sequence.

Every node must have a unique `mobileOrder`.

The order should tell the architectural story naturally from entry point to outcome.

---

# 43. Mobile Boundaries

On mobile, architecture boundaries render as narrative section labels.

Order nodes so the boundary structure remains understandable without relying on desktop geometry.

Do not use desktop positioning as a substitute for mobile narrative order.

---

# 44. Shared Icons

Available shared icons include:

```text
layers
terminal
api
process
service
worker
database
queue
object-storage
file-json
source-code
ast
search
editor
external
```

Use icons to clarify a semantic type.

Do not use an icon to disguise an incorrect type.

Example:

A Postgres job store remains:

```text
type: database
```

even if a queue-like icon would visually resemble its workflow behavior.

---

# 45. Shared Architecture Components

Reusable architecture implementation lives in:

```text
src/components/architecture/ArchitectureIcon.tsx
src/components/architecture/DiagramBox.tsx
src/components/architecture/ArchitectureDiagram.tsx
src/components/architecture/ArchitectureBoundary.tsx
src/components/architecture/NodeDetailsPanel.tsx
src/components/architecture/ConnectionDetailsPanel.tsx
src/components/architecture/diagram-schema.ts
src/components/architecture/interactive-routing.ts
src/components/architecture/layout-persistence.ts
src/components/architecture/static-layout.ts
```

Extend these shared primitives when a genuinely reusable visual or semantic concept is required.

Do not special-case one documentation page.

---

# 46. Extending the Schema

Before adding a new schema field, node type, variant, protocol, inspector section, or visual concept, ask:

1. Does the existing schema already represent the architecture accurately?
2. Is this concept reusable across multiple diagrams?
3. Is this architectural information rather than presentation-only metadata?
4. Can it be represented through existing node or edge details?
5. Will the addition remain meaningful on desktop and mobile?

Extend the schema only when the answer supports a genuinely new reusable concept.

Do not expand the schema to make one diagram easier to author.

---

# 47. Shared Behavior Requirements

Changes to architecture primitives must preserve:

- light mode;
- dark mode;
- desktop interaction;
- mobile rendering;
- keyboard behavior;
- schema validation;
- draggable layout behavior;
- inspector behavior;
- connector routing;
- existing architecture documents.

Update relevant tests whenever shared behavior changes.

---

# 48. CAD Editor Diagram Invariants

The high-level architectural invariants are defined in `AGENT.md`.

When authoring CAD Editor diagrams, preserve them exactly.

In particular:

- `NestJS CAD Agent` is the control plane;
- `Python CAD Tool Worker` is the source mutation boundary;
- `edit_jobs` is the workflow source of truth;
- `edit_job_events` is the ordered public replay log;
- `cad_tool_jobs` is the durable Nest-to-Python handoff;
- WebSocket is a delivery channel, not a queue;
- OpenAI returns registered tool operations and does not mutate source;
- linked `requested_part_id` is authoritative;
- established-part editing is a versioned tool plan, not unrestricted code generation;
- one complete tool plan produces at most one durable candidate;
- only validated committed source enters the accepted semantic index.

Do not alter these boundaries to simplify a diagram.

---

# 49. Example: Correct Storage Modeling

Correct:

```text
┌──────────────┐      write semantic_index.json      ┌───────────────┐
│   Indexer    │ ──────────────────────────────────→ │ Index Storage │
└──────────────┘                                     │               │
                                                     │ contents:     │
                                                     │ semantic_     │
                                                     │ index.json    │
                                                     └───────────────┘
```

Incorrect:

```text
Indexer → semantic_index.json → Index Storage
```

The file is not an execution resource.

---

# 50. Example: Correct Database Job Modeling

If `cad_tool_jobs` is a Postgres-backed handoff:

Correct:

```text
NestJS CAD Agent → CAD Tool Job Store
                   enqueue tool job

Python CAD Tool Worker → CAD Tool Job Store
                         claim_next_tool_job
```

Node type:

```text
database
```

Incorrect:

```text
NestJS CAD Agent → CAD Tool Queue → Python CAD Tool Worker
```

unless a real independent queue/broker actually exists.

---

# 51. Example: Correct Request/Response Modeling

Correct:

```text
NestJS CAD Agent ─────────────→ OpenAI
                 planning request

direction: two-way

request:
  bounded planning context

response:
  validated EditPlan
```

Incorrect:

```text
NestJS CAD Agent → OpenAI
OpenAI → NestJS CAD Agent
```

when both arrows represent one synchronous request/response exchange.

---

# 52. Example: Correct Internal Stage Modeling

If validation is a meaningful deterministic stage inside a runtime:

```text
type: function
variant: gate
label: Validate Candidate
```

If validation occurs inside an isolated long-running runtime:

```text
type: worker
variant: sandbox
label: Validation Sandbox
```

If both concepts matter, represent the sandbox as the execution boundary and the validation stage inside its boundary.

Do not use one node type to represent two different physical concepts.

---

# 53. Example: Diagram Authoring Checklist

Before finishing an architecture document, verify:

- [ ] Every node represents a real resource or meaningful architectural stage.
- [ ] Every node has a stable unique ID.
- [ ] Every node uses the correct semantic type.
- [ ] Every node has a unique desktop position.
- [ ] Every node has a unique mobile order.
- [ ] Durable files belong to their real storage node.
- [ ] Database-backed job state is represented as a database.
- [ ] Temporary values are not represented as durable nodes.
- [ ] Every edge points from the interaction initiator to its target.
- [ ] Edge labels describe actions or payloads.
- [ ] Protocol matches the real implementation mechanism.
- [ ] Flow correctly distinguishes immediate and later execution.
- [ ] Cohesive request/response exchanges use one two-way edge.
- [ ] Boundaries express real ownership or runtime grouping.
- [ ] Important guarantees have repository evidence.
- [ ] The diagram reflects current implementation.
- [ ] The desktop layout is readable.
- [ ] The mobile order tells a coherent story.
- [ ] No page-specific connector or layout behavior was introduced.
- [ ] Shared architecture tests still pass.

---

# 54. Final Authoring Rule

When deciding whether something belongs in an architecture diagram, ask:

> Does this help a developer understand an ownership boundary, execution boundary, durable state boundary, or important system interaction?

If yes, represent it at the appropriate level.

If no, leave it to the inspector, focused reference documentation, tests, or source code.

**Keep the diagram architectural. Keep the overview concise. Keep the implementation authoritative.**
