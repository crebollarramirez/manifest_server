# CAD Agent editing

The CAD editing workflow has two runtime boundaries:

- `services/cad_agent/` is the NestJS control plane. It accepts HTTP and
  WebSocket submissions, generates strict OpenAI tool plans, owns orchestration
  and progress replay, verifies validation proof, commits accepted source, and
  queues reindex/export work.
- `workers/cad_editor/` is the Python CAD tool boundary. It prepares bounded
  source context and applies registered AST/source operations to isolated
  candidates. It has no OpenAI dependency and cannot commit canonical source.

The control plane and this Python microservice have separate Astro documentation
pages and diagrams:

- `docs/src/pages/services/cad-agent.astro` and
  `docs/src/data/architecture/cad-editor.json` describe the NestJS CAD Agent.
- `docs/src/pages/services/cad-editor.astro` and
  `docs/src/data/architecture/cad-tool-worker.json` describe this Python CAD
  Tool Worker.

## Runtime flow

1. A client submits a project, optional linked part, request text, and
   `client_request_id`.
2. `submit_cad_edit_job` creates one `edit_jobs` workflow or returns the
   matching existing job.
3. The Nest worker claims the workflow and ensures the project index is fresh.
4. Nest queues a `prepare_context` row in `cad_tool_jobs`.
5. Python resolves one part, verifies accepted source, and returns bounded
   source context, allowed targets, and fingerprints.
6. OpenAI returns a strict `tool_plan_v1`; it never receives storage or
   database credentials.
7. Nest queues `apply_plan`. Python validates the complete plan and applies all
   operations in memory. Only a fully successful plan uploads one candidate.
8. The independent CAD Validator proves the exact candidate path and SHA-256.
   Repairable failures may be replanned, with three validation attempts total.
9. Nest commits only when the proof matches and canonical source still has the
   accepted base hash, then requires reindexing and queues best-effort export.
10. Every major stage appends an ordered public `edit_job_events` row.

WebSocket disconnects do not affect this state machine. A client can reconnect
with its last acknowledged event sequence and replay everything it missed.

## Supported tool operations

The shared Nest Zod and Python Pydantic contracts permit:

- `write_initial_model` for an exact blank linked CAD part only;
- parameter field replacement, addition, and eligible deletion;
- `@cad_part` metadata updates;
- feature or `build_model` body replacement;
- private-helper addition and eligible deletion;
- CAD-feature addition and eligible deletion.

Initial design may generate the complete AI-owned model body, but the runtime
import remains system-owned. Established parts never allow unrestricted
whole-file replacement.

Newly reasoned plans use ToolPlan schema version 2. Each established-source
plan includes an `impact_review` covering the feature operations, consumers of
changed parameters, and every transitive dependent. The Python tool worker
derives that set independently and rejects missing, extra, or contradictory
review entries. Persisted schema-version-1 plans remain executable.

`depends_on` contains immediate geometry producers only. Context preparation
derives reverse dependents and readable transitive paths, while static
`params.<field>` analysis identifies shared-parameter consumers. Decorator
`parameters` must match every field that can influence the feature, including
fields read through private helpers.

Added parameters, private helpers, and CAD features receive explicit
`CAD-AGENT` provenance markers. Delete operations are limited to those markers
or existing semantic `PART` regions. Required runtime imports, `ModelParams`,
`build_model`, unrelated human-owned code, and out-of-scope parts cannot be
deleted.

## Prompt sources

NestJS is the only CAD AI reasoning boundary. It loads and caches five focused
Markdown prompt sources:

- `supabase/functions/cad-agent/CAD_SYSTEM_PROMPT.md` defines only the strict
  CadQuery source style and modeling contract.
- `services/cad_agent/prompts/tool-plan.md` defines the registered tool catalog,
  arguments, effects, and safety preconditions.
- `services/cad_agent/prompts/initialization.md` defines complete-model
  construction for an exact blank linked part.
- `services/cad_agent/prompts/edit-plan.md` defines how to form one minimal,
  schema-valid ToolPlan from the authoritative inventory.
- `services/cad_agent/prompts/repair.md` adds generalized repair behavior when
  validation diagnostics or tool-preflight feedback are present.

Initialization receives the system contract, tool catalog, and initialization
prompt; it never receives established edit-plan rules. Established editing
receives the system contract, tool catalog, and edit-plan prompt. A repair adds
the generalized repair prompt to the active workflow composition. Validation
repair also receives the exact previous ToolPlan, failed candidate source and
hash, and structured diagnostics. The Python CAD tool worker has no OpenAI
dependency and owns no prompts. There are no generated prompt modules or
synchronization commands. Restart Nest after changing a prompt so its in-memory
cache is reloaded.

`supabase/functions/cad-agent/MESH_SYSTEM_PROMPT.md` remains the separate Edge
mesh-generation prompt.

## Run locally

Apply the current Supabase migrations first, then create
`workers/cad_editor/.env`:

```dotenv
SUPABASE_SERVICE_ROLE_KEY=replace-with-local-service-role-key
OPENAI_API_KEY=replace-with-openai-key

# Optional
SUPABASE_URL_DOCKER=http://host.docker.internal:54321
OPENAI_MODEL=gpt-5.4-mini
CAD_AGENT_PORT=3010
CAD_AGENT_POLL_INTERVAL_MS=2000
CAD_AGENT_DEPENDENCY_POLL_INTERVAL_MS=500
CAD_AGENT_DEPENDENCY_TIMEOUT_SECONDS=300
CAD_AGENT_LEASE_SECONDS=300
CAD_TOOL_JOB_POLL_INTERVAL_SECONDS=2
CAD_TOOL_LEASE_SECONDS=300
```

Build and start the Nest API/orchestrator and Python tool worker:

```bash
docker compose \
  --env-file workers/cad_editor/.env \
  -f workers/cad_editor/docker-compose.yml \
  up --build
```

When running Nest directly from source, use `services/cad_agent/.env` instead
of `workers/cad_editor/.env`:

```bash
cd services/cad_agent
npm ci
npm run start:dev
```

The direct Nest environment must define `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, and `OPENAI_API_KEY`. The `start` and `start:dev`
scripts load that file automatically; a `.env` inside `services/cad_agent/src/`
is not loaded.

Nest listens on `http://127.0.0.1:3010` by default:

- `POST /v1/cad-edits`
- `GET /v1/cad-edits/:jobId?after_sequence=0`
- `ws://127.0.0.1:3010/v1/cad-edits/ws`

The existing Supabase `cad-agent` Edge Function remains a compatible action
API. CAD chat submissions use the same `submit_cad_edit_job` database contract,
so Edge and direct Nest submissions share idempotency and durable processing.

## Development checks

Run the Nest contract/submission/replay tests and build:

```bash
cd services/cad_agent
npm test
npm run build
```

Run the Python tool tests from the repository root:

```bash
python -m unittest tests.test_cad_tool_executor
python -m unittest discover -s tests -p 'test_*.py'
```

The principal implementation files are:

- `services/cad_agent/src/contracts.ts`
- `services/cad_agent/src/submission.service.ts`
- `services/cad_agent/src/cad-edits.gateway.ts`
- `services/cad_agent/src/reasoner.service.ts`
- `services/cad_agent/src/orchestrator.service.ts`
- `workers/cad_editor/tool_worker.py`
- `workers/cad_editor/cad_editor/tool_contracts.py`
- `workers/cad_editor/cad_editor/tool_executor.py`
- `supabase/migrations/20260727000000_add_cad_agent_progress_and_tools.sql`
