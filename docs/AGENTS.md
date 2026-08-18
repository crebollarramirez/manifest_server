# Documentation Agent

Maintain concise, accurate architecture documentation for Manifest.

Documentation should help a developer understand a service in **3–5 minutes**:

1. What does this service do?
2. What does it own?
3. What does it explicitly not own?
4. How does it interact with the rest of the system?
5. What goes in and what comes out?
6. Where does durable state live?
7. How can it be run, tested, and inspected?

Prefer diagrams, short explanations, structured matrices, and links to source over long prose.

**Document contracts and boundaries, not code.**

Do not reproduce implementation details that are already clear from source.

---

# Development

Start the Astro development server in background mode:

```bash
astro dev --background
```

Manage it with:

```bash
astro dev status
astro dev logs
astro dev stop
```

Astro documentation:

- Routing: https://docs.astro.build/en/guides/routing/
- Components: https://docs.astro.build/en/basics/astro-components/
- Framework components: https://docs.astro.build/en/guides/framework-components/
- Content collections: https://docs.astro.build/en/guides/content-collections/
- Styling: https://docs.astro.build/en/guides/styling/
- Internationalization: https://docs.astro.build/en/guides/internationalization/

---

# Documentation Principles

Documentation is a map of the system, not a replacement for the codebase.

Prefer documenting:

- service responsibility;
- ownership boundaries;
- inputs and outputs;
- durable state;
- major workflow stages;
- guarantees and failure behavior;
- important dependencies;
- operational entry points;
- authoritative source references.

Avoid documenting:

- every class or function;
- implementation details obvious from source;
- large copied schemas or request bodies;
- speculative behavior;
- duplicated explanations;
- temporary in-memory values as architecture;
- implementation history unless it is still required to understand the current system.

When exact mechanics matter, link to the authoritative source instead of reproducing it.

---

# Service Documentation Standard

Every service page follows the same structure.

## 1. Service Header

Provide:

- service name;
- one sentence describing its responsibility and useful execution context.

Keep the summary concrete.

Do not add redundant introductions or a second diagram heading.

---

## 2. Architecture Diagram

The architecture diagram is the primary content of every Service view.

Render it immediately below the service header at full documentation width.

The diagram should show only the important architectural relationships:

- entry points;
- major execution boundaries;
- durable state;
- external dependencies;
- important internal stages;
- outputs;
- downstream consumers.

Do not turn the overview diagram into a complete implementation map.

Detailed mechanics belong in node inspectors, connection inspectors, focused reference pages, or source code.

Architecture diagram authoring rules live in:

```text
ARCHITECTURE_AUTHORING.md
```

Consult that file before creating or modifying architecture diagram data or shared diagram behavior.

---

## 3. Responsibilities and Boundaries

Use `BoundaryMatrix`.

Every service page must make three things explicit.

### Owns

Responsibilities for which the service is authoritative.

### Does Not Own

Responsibilities that belong elsewhere.

This is required. Service documentation must communicate boundaries, not only capabilities.

### Depends On

Services, runtimes, databases, storage, external providers, or other system components required by the service.

---

## 4. How It Works

Explain the primary workflow as a short ordered sequence.

Prefer approximately **3–8 steps**.

Describe:

- major state transitions;
- meaningful execution stages;
- important boundary crossings;
- final outcomes.

Do not narrate individual function calls.

Detailed process mechanics belong in inspectors or source code.

---

## 5. Interface

Describe important service-level contracts.

Include only relevant:

- triggers;
- requests;
- responses;
- events;
- durable jobs;
- produced artifacts;
- downstream results.

Do not copy full request bodies or complete schemas onto service overview pages.

Link to the authoritative schema or source when exact structure matters.

---

## 6. Data

Clearly identify durable reads and writes.

Example:

```text
Reads
- candidate source
- semantic index
- edit job

Writes
- validation result
- committed source
- geometry snapshot
```

Do not represent transient in-memory values as durable state.

Do not invent a storage layer for data that is actually stored in a database, object storage, or another existing resource.

---

## 7. Guarantees and Failures

Use shared documentation primitives when the behavior is important enough to surface.

Use `KeyGuarantees` for at most **five** implementation-backed promises.

Use `FailureMatrix` when failures materially affect:

- durable state;
- runtime execution;
- retries;
- downstream behavior;
- user-visible outcomes.

Important guarantees must be supported by source evidence.

---

## 8. Operations and Testing

Use `OperationsAndSource`.

Include only useful operational information:

- how the service is started;
- relevant test commands;
- important observability;
- runtime controls;
- major source paths;
- important symbols.

Avoid function-by-function source inventories.

---

## 9. Related Services

Link only to directly related:

- services;
- architecture pages;
- focused references.

Do not create large generic link collections.

---

# Shared Service Documentation Components

Use the shared primitives in:

```text
src/components/service-docs/
```

Use:

- `ServiceAtGlance` — trigger, durable reads/state, produced result, execution boundary, and downstream consumer;
- `BoundaryMatrix` — owns, does not own, and depends on;
- `ModeMatrix` — only when execution modes materially differ;
- `KeyGuarantees` — at most five implementation-backed guarantees;
- `FailureMatrix` — durable outcome, runtime execution, and downstream effect;
- `OperationsAndSource` — operations, observability, source paths, and symbols.

Do not recreate service summary cards or tables for individual pages.

Keep the main page concise.

---

# Source Evidence

Documentation must describe the implementation that exists, not the implementation the author expects to exist.

Important architectural claims should cite repository evidence.

This includes claims about:

- authoritative state;
- isolation;
- transaction semantics;
- durability;
- hash binding;
- credential handling;
- retry behavior;
- lack of retry;
- validation guarantees;
- commit behavior.

Evidence should use repository-relative references to:

- source;
- migration;
- schema;
- test;
- configuration.

Do not use:

- absolute paths;
- path traversal;
- invented source references;
- empty placeholders.

If the implementation does not support a claim, do not document the claim as fact.

---

# CAD Editor Architecture Invariants

When documenting the CAD Editor, preserve the following boundaries and workflow facts.

These are architectural constraints, not optional wording preferences.

## Control Plane and Mutation Boundary

`NestJS CAD Agent` is the control plane.

It owns:

- HTTP/WebSocket submission;
- idempotency;
- OpenAI reasoning;
- durable orchestration;
- proof verification;
- commit;
- progress replay.

`Python CAD Tool Worker` is the mutation boundary.

It owns:

- bounded context preparation;
- transactional AST/source-tool execution.

Never collapse these into one worker or one generic CAD service.

---

## Durable Workflow State

`edit_jobs` is the workflow source of truth.

`edit_job_events` is the ordered public replay log.

`cad_tool_jobs` is the durable Nest-to-Python handoff.

WebSocket is a delivery channel.

Never describe or diagram WebSocket as the work queue.

---

## OpenAI Boundary

OpenAI returns a strict, versioned plan of registered tool operations.

OpenAI does not:

- write source;
- execute Python;
- execute CadQuery;
- create validation proof;
- commit canonical source;
- complete the edit job.

Do not imply that the model directly mutates project source.

---

## Part Creation and Indexing

CAD `create_part`:

1. writes the exact runtime-only blank marker;
2. attempts to queue `build_index`.

An index-queue failure does not delete the newly created part.

The Indexer excludes the exact blank marker.

Therefore, a fresh valid index may contain no parts.

---

## Part Targeting

A linked `requested_part_id` is authoritative.

The editor cannot redirect a linked request to a better-ranked feature in another part.

Only unlinked requests use project-wide semantic resolution.

---

## Initial Design

Initial design may replace the complete AI-owned body behind the system-owned runtime import.

It uses:

- the dedicated initialization prompt;
- exactly one `write_initial_model` operation for each generation or repair attempt.

Established parts never receive the initialization prompt or whole-file replacement.

---

## Established-Part Editing

Established-part editing uses a versioned tool plan.

The plan may combine validated add, modify, and eligible delete operations for:

- `ModelParams` fields;
- private helpers;
- server-decorated CAD features;
- `build_model`;
- existing PART regions.

Never describe established-part editing as unrestricted code generation.

---

## Deletion Protection

Deletion requires:

- an existing semantic PART region; or
- an explicit CAD-AGENT provenance marker.

Protected content includes:

- required runtime imports;
- `ModelParams`;
- `build_model`;
- unrelated human-owned source;
- out-of-scope parts.

---

## Candidate Atomicity

One tool plan is applied completely in memory.

A durable candidate is uploaded only after:

1. every operation succeeds;
2. the final source contract passes.

Never depict intermediate tool operations as durable partial candidates.

---

## Progress Reporting

Public progress may expose milestones such as:

- planning;
- tool execution;
- validation;
- repair;
- commit;
- reindex;
- completion;
- failure.

Never expose:

- prompts;
- source bodies;
- secrets;
- private model reasoning.

---

## Validation Repair

Repair operates on:

- the latest isolated candidate;
- its exact previous ToolPlan;
- structured diagnostics.

Temporary repair inputs never enter the accepted semantic index.

Only validated, committed source is reindexed.

---

# Documentation Quality Rules

## Prefer

- diagrams;
- concise explanations;
- ownership boundaries;
- short workflow sequences;
- structured matrices;
- source-backed guarantees;
- direct links to related services;
- links to authoritative source.

## Avoid

- function-by-function documentation;
- repeating source code;
- copying large schemas;
- giant prose sections;
- implementation history;
- speculative architecture;
- duplicate explanations across pages;
- page-specific diagram implementations;
- documenting temporary values as architectural resources.

---

# Reading Budget

A normal service overview should be understandable in approximately **3–5 minutes**.

If substantially more explanation is required, move the detail into:

- node inspectors;
- connection inspectors;
- `ARCHITECTURE_AUTHORING.md`;
- dedicated architecture pages;
- focused reference documentation;
- source code;
- tests.

The service overview should remain a concise map of the service and its boundaries.
