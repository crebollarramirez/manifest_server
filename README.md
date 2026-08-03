# Manifest Server

Manifest Server contains the NestJS backend, Supabase-backed workers, CLI,
and the architecture documentation site.

## Start with the documentation

The Astro site is the source of truth for backend behavior, service boundaries,
API actions, job lifecycles, diagrams, failure handling, and the complete local
runbook:

- [Backend system design and local runbook](docs/src/pages/index.astro)
- [CAD Editor service](docs/src/pages/services/cad-editor.astro)
- [Indexer Worker service](docs/src/pages/services/indexer-worker.astro)
- [CAD Validator service](docs/src/pages/services/cad-validator.astro)
- [CAD Exporter service](docs/src/pages/services/cad-exporter.astro)
- [Diagram authoring standard](docs/AGENTS.md)

Worker-specific implementation notes remain next to their code:

- [CAD Editor README](workers/cad_editor/README.md)
- [Indexer Worker README](workers/indexer/README.md)

## Run the documentation site locally

From the repository root:

```bash
cd docs
npm install
npx astro dev --background
```

Open the local URL printed by Astro. Manage the background server with:

```bash
npx astro dev status
npx astro dev logs
npx astro dev stop
```

Build the static site and run its dependency-free architecture tests with:

```bash
npm run build
node --test --experimental-strip-types src/components/architecture/*.test.ts
```

## Backend setup

Use the [backend local runbook](docs/src/pages/index.astro#local-runbook) for
the commands and environment variables needed to start Supabase, run the
NestJS CAD Agent and worker containers, and use the CLI. It also documents what
each command does and how to check asynchronous job status.

The backend implementation is organized as:

- `services/cad_agent/` — NestJS action API, catalog and job operations, CAD
  edit submission, linked-mesh generation, OpenAI tool planning, durable
  orchestration, WebSocket progress replay, and guarded commit.
- `workers/cad_editor/` — bounded Python CAD context and transactional
  AST/source tool execution against isolated candidates.
- `workers/indexer/` — static CAD source indexing and Getter retrieval.
- `workers/cad_validator/` — candidate source and geometry validation.
- `workers/cad_exporter/` — hash-bound CAD and mesh artifact export.
- `cad_agent_cli.py` — terminal client for the NestJS action and progress APIs.

## Verification

Run the backend tests from the repository root:

```bash
python -m unittest discover -s tests -p 'test_*.py'
cd services/cad_agent && npm test && npm run build
```

For service behavior, diagrams, API examples, and operational details, use the
documentation site rather than duplicating those details here.
