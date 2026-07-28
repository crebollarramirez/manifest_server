# Manifest System Docs

This directory contains the Manifest architecture and service documentation
site. It is a custom Astro documentation application, not a Starlight content
site: pages are authored as Astro components and share a documentation shell,
sidebar, responsive layout, and reusable architecture-diagram renderer.

The site documents real system behavior. A service page should explain the
service boundary, operational behavior, durable state, failure behavior, and
source ownership, then use a data-driven diagram to show how those pieces
interact.

## Technology stack

| Area | Current implementation | Purpose |
| --- | --- | --- |
| Site framework | Astro 7 with `@astrojs/react` | Static documentation pages with React islands where interactivity is useful. |
| UI | Astro, React 19, and shared CSS | Custom documentation shell, light/dark support, responsive prose, sidebar navigation, cards, callouts, and tables. |
| Diagrams | `@xyflow/react` (React Flow) | Draggable desktop architecture cards, live orthogonal connectors, focus state, pan/zoom, fit, and browser-local layout reset. |
| Diagram validation | Zod | Strict JSON contract validation before a diagram renders. Invalid documents show an accessible error panel instead of a partial graph. |
| Layout support | Authored grid positions and React Flow live routing | JSON supplies initial desktop positions and explicit mobile narrative order; connector coordinates are never authored. ELK remains installed for future initial-layout work. |
| Build tooling | Astro CLI and Node's built-in test runner | Static build plus dependency-free schema, layout, routing, persistence, edge, and connection-detail tests. |

Run commands from this directory:

```sh
npm run build
node --test --experimental-strip-types src/components/architecture/*.test.ts
astro dev --background
```

Use `astro dev status`, `astro dev logs`, and `astro dev stop` to manage the
background development server.

## What the site provides

The current site includes a combined Backend System Design overview and focused
service pages for Indexer Worker, the NestJS CAD Agent, the separate Python CAD
Tool Worker, CAD Validator, and CAD Exporter. The CAD Agent page documents
idempotent submission, durable WebSocket progress replay, strict OpenAI tool
plans, proof-gated commit, reindex, and export. The CAD Tool Worker page
documents its independent bounded-context and transactional AST/source
execution boundary.

Each page can provide:

- a purpose-and-boundaries explanation before the diagram;
- a responsive architecture diagram with a compact legend;
- concise prose, callouts, tables, operational notes, source-location notes,
  and failure behavior after the diagram;
- direct drill-down links from cards when a deeper architecture page exists;
- accessible connection details when a reader clicks a connector;
- light and dark theme support without page-specific diagram styling.

On desktop, cards can be dragged to isolate relationships. Their local
positions are saved only in the reader's browser and can be reset; they never
change the architecture JSON. Clicking a card focuses its direct neighbors and
clicking a connector opens the connection inspector on the right. On narrow
screens, the renderer switches to the explicitly authored vertical narrative:
cards are not draggable and connectors reroute for the stack.

## Service-documentation model

A service page explains the implementation in this order:

1. **Purpose and boundaries** — what the service owns, what it deliberately
   does not own, and the upstream/downstream systems it depends on.
2. **System design diagram** — the physical resources and interactions that
   make the service work.
3. **Supported paths or behavior** — service-specific commands, job types,
   API operations, or execution paths.
4. **Data, source, and artifact controls** — canonical source ownership,
   durable files, hashes, validation, and publication rules.
5. **Failure behavior** — cancellation, retry/reclaim behavior where it truly
   exists, bounded diagnostics, and preservation of last-known-good output.
6. **Operations and source code** — monitoring concerns, runtime controls, and
   local repository paths. Do not invent repository URLs.

Use the current implementation as the source of truth. Do not document a
lease, broker, RPC field, retry policy, or service-to-service call unless code
or migrations prove it exists.

## Diagram source of truth

Architecture documents live in `src/data/architecture/*.json`. Pages pass one
of those documents to `ArchitecturePage`, which renders the shared
`ArchitectureDiagram` React island. Authors should never create page-specific
SVG paths, connection CSS, node markup, icon components, or manual midpoint
labels.

Every document declares:

- `id`, `title`, `summary`, and `scope` (`system`, `domain`, `service`, or
  `foundation`);
- semantic `nodes` with an ID, label, description, type, initial `position`,
  and unique `mobileOrder`;
- `edges` with source, target, action/payload label, protocol, timing, and
  request/response metadata where applicable.

The strict Zod schema rejects unknown keys, blank identifiers, duplicate node
or edge IDs, duplicate desktop cells or mobile order values, dangling edge
endpoints, invalid enums, invalid storage contents, and linked foundation
nodes.

## What each diagram component means

Boxes represent physical implementation resources, not a convenient workflow
metaphor.

| Node type | Represents | Typical information shown on the card |
| --- | --- | --- |
| `client` | A human-facing or automation caller. | Client name and a drill-down link when one exists. |
| `api` | An inbound Manifest API boundary. | API name and interface role. |
| `function` | One deterministic architectural stage. | Stage name, such as source staging, validation, or completion. |
| `service` | A reusable service boundary. | Service name and responsibility. |
| `worker` | A polling, background, or isolated execution boundary. | Worker name and a purple visual accent. |
| `database` | Durable relational state or a Postgres-backed job table. | Table/store name and lifecycle ownership. |
| `queue` | A real message broker or independently operated queue. | Queue name; never use this for queued Postgres rows. |
| `storage` | Durable object/blob storage bucket or path. | Storage owner plus file rows at the bottom of the card. |
| `external` | A dependency outside Manifest ownership. | Dependency name and boundary treatment. |
| `layer` | A named architecture boundary. | Layer label only when a grouping boundary is useful. |

Icons can make a card more specific, but never change its semantic type. The
shared renderer applies the conventional visual language: APIs are entry
accented, workers are purple, databases and storage are state-colored, and
external dependencies use a boundary treatment.

### Stored files and artifacts

There are no standalone artifact boxes. An artifact is shown as a file-icon
row inside the durable storage box that owns it—for example `model.py`,
`params.json`, `model.step`, `model.stl`, `model.glb`, or
`semantic_index.json`.

Use a storage node's `contents` array to author those rows. A producer points
directly to the storage owner when it writes a durable file. A transient value
can be named in an internal or file edge label, but is not represented as a
separate node.

## Connector and request/response rules

An arrow always answers one question: **who initiated this interaction?**

`A → B` means A calls, reads from, writes to, enqueues work through, or
otherwise causes the labeled interaction with B. Labels describe the action or
payload, such as `read model.py and params.json`, `claim next supported export
job`, or `write terminal job outcome`.

For storage, both reads and writes point from the process to storage:

```text
Source Staging → Part Source Storage   read model.py and params.json
CAD Export → Export Storage            upsert STEP and STL
```

Storage never appears to request a process merely because it returns bytes.
The same initiator rule applies to databases, APIs, and external dependencies.

Use these protocol values:

| Protocol | Use for |
| --- | --- |
| `http` | API request/response interactions. |
| `database` | Postgres reads, writes, and RPCs. |
| `storage` | Object-storage reads and writes. |
| `file` | Transient file movement between internal stages. |
| `event` | Independent asynchronous event delivery. |
| `queue` | A real broker/queue transport only. |
| `internal` | An in-process or service-local handoff. |

`synchronous` means the initiating action receives an immediate result.
`asynchronous` means the work or delivery happens later. A Postgres enqueue or
claim RPC remains a synchronous `database` interaction even when a worker
performs the resulting job later.

For one cohesive request/response exchange, use one
`direction: "two-way"` edge and provide both `request` and `response` objects.
Each object includes a human label, concrete operation/type, and representative
copyable JSON body. The diagram still renders only **one arrow**, from the
initiator to the dependency. The response is not a second request; it is shown
in the connection inspector as `Target → Source`.

## Connection inspector

Clicking or keyboard-activating a connector opens the shared right-side
inspector. It shows:

- the connection label;
- protocol, synchronous/asynchronous flow, and contract direction;
- the request direction, label, type, and example body;
- for request/response exchanges, the response direction, label, type, and
  example body;
- an optional durable reference link when the edge declares `href`.

This keeps detailed payload examples accessible without placing permanent
labels, popovers, or manual controls in the diagram itself.

## Authoring and verification checklist

Before adding or changing a page:

1. Inspect the worker, API, migrations, and storage code. Use the physical
   implementation—not desired future behavior—as the documentation source.
2. Add or update the JSON document, then write concise page prose around it.
3. Reuse shared node types, icons, routing, and inspector metadata. Do not
   create a per-page visual exception.
4. Use `database` for Postgres job tables even when rows are claimed or polled;
   use `queue` only for a genuine broker.
5. Mark a node `foundation: true` only when it is terminal at that documentation
   depth. Foundation nodes cannot link to a deeper page.
6. Run the architecture tests and `npm run build`. Confirm linked cards,
   connector inspector controls, mobile ordering, and light/dark rendering
   remain usable.

The complete, enforceable diagram standard is maintained in `AGENTS.md`.
