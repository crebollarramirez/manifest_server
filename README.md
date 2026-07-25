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

The CLI keeps track of a linked project and part. Once both are linked, plain text input is sent to the AI agent as chat.

Supported commands:

- `/create -project <name>`: Create a project and link the CLI to it.
- `/create -part -cad <name>`: Create a CAD part inside the linked project and link to it.
- `/create -part -mesh <name>`: Create a mesh part inside the linked project and link to it.
- `/link -project <name>`: Link the CLI to an existing project.
- `/link -part <name>`: Link the CLI to an existing part in the current project.
- `/list -projects`: List all projects.
- `/list -parts`: List parts in the current linked project.
- `/export <partId>`: Queue a manual export job for a part by ID.
- `/validate <partId>`: Queue a manual validation job for a part by ID.
- `/index <projectId>`: Queue an index build for every CAD part in a project.
- `/index -test <request>`: Test retrieval against the linked project's current index and print ranked matches plus focused context.
- `/delete -project <name>`: Delete a project after confirmation.
- `/delete -part <name>`: Delete a part in the current project after confirmation.
- `exit` or `quit`: Close the CLI.
- `<plain text>`: Send a normal chat message to the AI agent for the currently linked project and part.

The Getter test rejects missing or stale indexes. Run `/index <projectId>`
again after adding, deleting, renaming, or changing a CAD part.

### Optional: run validator tests without Docker

```bash
python -m unittest tests/test_cad_ast_validator.py
```
