# Manifest Server

Manifest Server contains the backend workers, Supabase Edge Functions, CLI,
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
the commands and environment variables needed to start Supabase, serve
`cad-agent`, run the worker containers, and use the CLI. It also documents what
each command does and how to check asynchronous job status.

The backend implementation is organized as:

- `supabase/functions/cad-agent/` — request validation, catalog operations,
  durable job creation, status reads, and linked mesh generation.
- `workers/cad_editor/` — linked CAD initial design and structured source
  editing orchestration.
- `workers/indexer/` — static CAD source indexing and Getter retrieval.
- `workers/cad_validator/` — candidate source and geometry validation.
- `workers/cad_exporter/` — hash-bound CAD and mesh artifact export.
- `cad_agent_cli.py` — terminal client for the Edge Function actions.

## Verification

Run the backend tests from the repository root:

```bash
python -m unittest discover -s tests -p 'test_*.py'
deno check --config supabase/functions/cad-agent/deno.json supabase/functions/cad-agent/index.ts
```

For service behavior, diagrams, API examples, and operational details, use the
documentation site rather than duplicating those details here.
