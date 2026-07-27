# Manifest Server

## Quick Start

Run all commands from the repository root unless noted otherwise.

### Supabase local dev

Start the local Supabase stack:

```bash
supabase start
```

Reset the local database and re-run all migrations:

```bash
supabase db reset
```

Push local migrations to the linked remote project:

```bash
supabase db push
```

Serve the local Edge Function:

```bash
supabase functions serve cad-agent --env-file supabase/functions/.env
```

### CAD validation worker

The validator worker uses `workers/cad_validator/.env` and connects back to the local Supabase stack through Docker.

Start it:

```bash
docker compose --env-file workers/cad_validator/.env -f workers/cad_validator/docker-compose.yml up --build
```

Stop it:

```bash
docker compose -f workers/cad_validator/docker-compose.yml down
```

### CAD exporter worker

The exporter worker uses `workers/cad_exporter/.env` and also connects to local Supabase through Docker.

Start it:

```bash
docker compose --env-file workers/cad_exporter/.env -f workers/cad_exporter/docker-compose.yml up --build
```

Stop it:

```bash
docker compose -f workers/cad_exporter/docker-compose.yml down
```

### CAD indexer worker

The indexer uses `workers/indexer/.env`, connects to Supabase with the service
role, and runs one worker process. It indexes only CAD parts and stores the
project index at `<projectId>/index/semantic_index.json` in the `3dProjects`
bucket. See the [indexer technical overview](workers/indexer/README.md) for
the build, retrieval, and integration logic.

Start it:

```bash
docker compose --env-file workers/indexer/.env -f workers/indexer/docker-compose.yml up --build
```

Stop it:

```bash
docker compose -f workers/indexer/docker-compose.yml down
```

### CAD editor worker

The editor runs exactly one orchestration worker. For a linked blank CAD part,
it generates and validates the first complete model; for an established part,
it resolves project-scoped targets and plans constrained edits. It validates
isolated candidates, commits accepted source, reindexes the project, and
queues export. The indexer, validator, and exporter remain independent workers. See the
[CAD editor technical overview](workers/cad_editor/README.md).

Set `SUPABASE_SERVICE_ROLE_KEY` and `OPENAI_API_KEY` in
`workers/cad_editor/.env`, then start it:

```bash
docker compose --env-file workers/cad_editor/.env -f workers/cad_editor/docker-compose.yml up --build
```

Stop it:

```bash
docker compose -f workers/cad_editor/docker-compose.yml down
```

### CLI

The terminal client talks to the local `cad-agent` function and loads variables from a repo-root `.env` file. At minimum, set:

```bash
SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_ANON_KEY=...
```

Install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Start the CLI:

```bash
python cad_agent_cli.py
```

The CLI keeps track of a linked project and optional part. Plain-text CAD
requests require only a linked project and search all of its CAD parts. Mesh
requests continue to require a linked mesh part.

Supported commands:

- `/create -project <name>`: Create a project and link the CLI to it.
- `/create -part -cad <name>`: Create a blank CAD part containing only the system runtime import and link to it. The first chat request for that linked part starts initial design.
- `/create -part -mesh <name>`: Create a mesh part inside the linked project and link to it.
- `/link -project <name>`: Link the CLI to an existing project.
- `/link -part <name>`: Link the CLI to an existing part in the current project.
- `/list -projects`: List all projects.
- `/list -parts`: List parts in the current linked project.
- `/export <partId>`: Queue a manual export job for a part by ID.
- `/validate <partId>`: Queue a manual validation job for a part by ID.
- `/index <projectId>`: Queue an index build for every CAD part in a project.
- `/index -test <request>`: Test retrieval against the linked project's current index and print ranked matches plus focused context.
- `/edit-status <jobId>`: Print CAD edit state, attempts, targets, diagnostics, changed symbols, and child job IDs.
- `/delete -project <name>`: Delete a project after confirmation.
- `/delete -part <name>`: Delete a part in the current project after confirmation.
- `exit` or `quit`: Close the CLI.
- `<plain text>`: Queue a project-scoped CAD edit, or update the linked mesh part when one is selected.

The Getter test rejects missing or stale indexes. Run `/index <projectId>`
again after adding, deleting, renaming, or changing a CAD part.
CAD edit jobs automatically queue a full index build when needed and reindex
again after commit. `/index -test` remains read-only and never auto-rebuilds.

### Optional: run validator tests without Docker

```bash
python -m unittest tests/test_cad_ast_validator.py
```
