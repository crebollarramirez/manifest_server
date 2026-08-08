# Tool harness (`tool_harness.py`)

A CLI for exercising registered agent tools by hand, without a server and
without the OpenAI-backed reasoning runtime. It drives tools through the
exact same `ToolRegistry` / `AgentTool.run()` path production uses
(`edit_worker.py`), so what you see here is what the real system does. Run
`list` for the exact, current set (it's registered in one place,
`build_registry()`, so this doc doesn't try to keep its own copy of the list
in sync) -- as of this writing that's the two read-only index tools plus
full create/edit/delete coverage for CAD features, `ModelParams` parameters,
whole CAD parts, and `build_model`'s body.

## Why this lives in `tools/`

The harness only knows how to call things through the strict
`ToolRegistry` / `AgentTool.run()` contract -- that's `tools/`-owned business
logic. Legacy code (`applier.py`, `EditPlan`/`ToolPlan` operations, the
reasoner/orchestrator pipeline) does not go through that contract, so it
cannot be exercised through this harness. Only tools that have actually been
migrated into or built directly in `tools/` are testable this way -- as more
get migrated, they become testable here for free, with no harness changes.

## Testing logic: two layers, two jobs

**pytest is the source of truth.** `tests/test_cad_create_feature_unit.py`
and `tests/test_cad_create_feature_integration.py` are the authoritative,
exhaustive regression suite -- every validation rule, every generation
detail, every failure code is asserted there. Run those whenever you change
tool code; CI-equivalent correctness comes from pytest, not from this
harness.

**The harness is for everything pytest is inconvenient for**: poking at a
tool with your own input on the fly, seeing *exactly* what a call changed
(a real diff, not an assertion), and getting a fast, readable sanity check
you can run standalone without pulling up the test suite. It has three modes:

- **`run`** -- free-form, in-memory by default. You supply arbitrary JSON
  arguments on the command line; the harness shows you the context it used,
  the arguments it parsed, the tool's structured result (`ok` / output
  fields, or the failure `code` / `message` / `details`), and a unified diff
  of every file the call changed (plus an `ast.parse()` check that any
  changed `.py` file is still valid Python). This is exploratory -- there's
  no pass/fail verdict, just "here's what happened." `run --backend
  supabase` also works, for a one-off flag-based call against real storage.
- **`real`** -- the same free-form call, but arguments and identity
  (`tool_id`, `project_id`, `part_id`, `candidate_id`, `args`) come from a
  local config file instead of flags, and it always uses the real Supabase
  backend. See "Testing against real Supabase" below.
- **`scenario`** -- asserted, always in-memory. A small set of built-in,
  named scenarios, each bundling a starting fixture, a specific call, and
  explicit checks against the result (does it succeed/fail as expected, is
  the generated decorator correct, is `build_model` untouched, did a
  *rejected* write leave the candidate byte-for-byte unchanged). Each check
  prints `PASS`/`FAIL`; the process exits non-zero if anything failed, so
  it's usable in a script too. This is the "prove the write/edit/delete
  actually worked correctly" layer the harness adds beyond `run`.

## Why `scenario` explicitly checks unchanged state

The most important correctness property for a mutating tool isn't just "it
returned success and the diff looks plausible" -- it's that a **rejected**
call never mutates anything. `create_feature_duplicate_semantic_id` asserts
exactly that: the call is rejected with `TOOL_VALIDATION_FAILED`, and the
candidate source afterward is byte-for-byte identical to before. That's the
same guarantee `tests/test_cad_create_feature_integration.py::test_invalid_input_does_not_mutate_candidate_source`
checks in pytest -- the scenario is a smaller, narrated version of it.

## Backends

- `--backend memory` (default) -- an in-memory, dict-backed repository.
  Zero setup, safe to run repeatedly, seeded automatically with a small
  built-in fixture `model.py` (a `ModelParams` dataclass, two existing
  features -- one with `# PART-START`/`# PART-END` markers -- and
  `build_model`) unless you pass `--seed-file` or `--no-seed`.
- `--backend supabase` -- the real `SupabaseEditRepository`
  (`cad_editor/repository.py`), for testing against a local (`supabase
  start`) or dev project. Reads `SUPABASE_URL` from the repo-root `.env` and
  `SUPABASE_SERVICE_ROLE_KEY` from `workers/agent_3d/.env` (that's where
  each already lives for this worker; `workers/agent_3d/.env` also has
  `SUPABASE_URL_DOCKER`, a docker-only host alias not used here since the
  harness runs directly on the host). Exits with a clear message (not a
  traceback) if either is missing. **Always points at real, already-existing
  data** -- you must supply `--project-id` / `--part-id` / `--candidate-id`
  for a project/part/candidate that already has source in storage. The
  harness never creates, seeds, or fabricates anything in real storage; if
  the candidate doesn't exist yet, the tool call fails with a clear
  `SOURCE_MISSING`-style error instead of silently creating one.

`scenario` always uses the in-memory backend; it's meant to be fast and
side-effect-free.

## Testing against real Supabase

```
cp workers/agent_3d/tools/real_supabase_test.json.example \
   workers/agent_3d/tools/real_supabase_test.json
```

Edit `real_supabase_test.json` (gitignored -- it holds real, environment-
specific IDs, same as `.env`): set `project_id` (and, for a part-scoped tool
like `create_feature`, `part_id` and `candidate_id`) to a project/part/
candidate that **already exists** in your local Supabase storage, and `args`
to whatever you want to call `tool_id` with. Then just run:

```
python3 workers/agent_3d/tools/tool_harness.py real
```

`part_id`/`candidate_id` are optional in the config -- omit both for a
project-scoped tool that doesn't need an existing part, such as
`create_cad_part`:

```json
{
  "tool_id": "create_cad_part",
  "project_id": "REPLACE_WITH_REAL_PROJECT_ID",
  "args": { "part_name": "Example Bracket" }
}
```

This is deliberately config-file-driven rather than flag-driven: the real
IDs don't need retyping on every run, and the command itself stays a single
short line. It prints the same context/arguments/result/diff output as
`run`, reading the relevant file before and after the call so you see
exactly what changed (or that nothing changed, if the call was rejected) --
for a tool whose write path isn't knowable until after it runs (like
`create_cad_part`, which writes to a path containing the `part_id` it just
created), the diff is read from the tool's own reported `source_path`
instead.

**The harness itself never seeds, creates, or fabricates data to make a call
succeed** -- e.g. `create_feature` fails with a clear error instead of
silently creating a candidate if one doesn't already exist at
`{project_id}/candidates/cad/{part_id}/{candidate_id}/model.py`. That's
different from calling a tool whose entire *job* is to create something:
running `create_cad_part` through `real` **does** insert a real row into the
`parts` table and write a real `model.py`, exactly as asked -- this is a
harder-to-reverse action than `create_feature`'s candidate-only writes
(candidates are disposable staging; parts are real project structure).

`--config <path>` overrides the default config file location if you want to
keep multiple named configs around (e.g. one per part you're testing
against).

## A gotcha this harness works around

Tool input models (`CreateFeatureInput`, etc.) declare fields like
`parameters: tuple[str, ...]` under a strict, non-coercing Pydantic config.
If you build a Python `dict` from `json.loads()` and hand it straight to
`model_validate()`, a JSON array is rejected for those fields -- strict mode
does not accept a `list` where a `tuple` is declared, even though a JSON
array is the only way to write a tuple in JSON. `run` avoids this by parsing
`--args` with `model_validate_json()` instead (which validates in JSON mode,
where arrays are accepted for tuple fields) and then dumping back to a
Python dict with `model_dump(mode="python")`, which preserves the tuple
type. Net effect: you can paste ordinary JSON with plain arrays and it just
works.

## Running without installing dependencies locally (Docker)

If your local `python3` doesn't have `pydantic`/`supabase` installed (e.g.
you get `ModuleNotFoundError: No module named 'pydantic'`), run the harness
inside the already-running `cad-editor` container instead of installing
anything on the host. `workers/agent_3d/docker-compose.yml` bind-mounts the
whole repo into the container at `/app`, so it sees every harness edit live
with no rebuild, and the image already has every dependency the harness
needs. Its environment also already has `SUPABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY` set (from `docker-compose.yml`, pointed at
`host.docker.internal` so it can reach your local Supabase), so
`run --backend supabase` and `real` work with no `.env` setup at all.

Just prefix any command below with `docker exec manifest-agent-3d-agent-3d-1`:

```
docker exec manifest-agent-3d-agent-3d-1 python3 workers/agent_3d/tools/tool_harness.py list
docker exec manifest-agent-3d-agent-3d-1 python3 workers/agent_3d/tools/tool_harness.py scenario --all
docker exec manifest-agent-3d-agent-3d-1 python3 workers/agent_3d/tools/tool_harness.py real
```

**Run each of these as a single line.** If a command wraps across two lines
in your terminal and you paste it back with a line break in the middle (with
or without a trailing `\`), your shell can split it into two separate
commands instead of one -- e.g. `docker exec ... python3` runs alone (starts
a bare interpreter, does nothing useful) and the rest
(`workers/agent_3d/.../tool_harness.py real`) then runs directly on your
*host* shell, where it isn't even a program you're allowed to execute
(`permission denied`). If you hit either of those errors, retype/paste the
command as one unbroken line rather than debugging it further.

Run from the repository root (`cd` there first) -- `docker exec` needs a
path relative to the container's `/app`, not your host working directory.

If you'd rather install dependencies locally instead of using Docker:
`pip install -r workers/agent_3d/requirements.txt` (ideally in a venv),
then drop the `docker exec ...` prefix from every command in this doc.

## Commands

```
# What can I call?
python3 workers/agent_3d/tools/tool_harness.py list

# Fast built-in correctness check (no setup required)
python3 workers/agent_3d/tools/tool_harness.py scenario --all
python3 workers/agent_3d/tools/tool_harness.py scenario create_feature_basic

# Try your own input against the default in-memory fixture
python3 workers/agent_3d/tools/tool_harness.py run create_feature --args '{
  "semantic_id": "corner_fillets",
  "function_name": "fillet_base_corners",
  "role": "aesthetic_features",
  "parameters": ["plate_width"],
  "dependencies": [{"semantic_id": "base_plate", "argument_name": "plate"}],
  "search_keys": ["corner fillets", "rounded corners"],
  "docstring": "Round the base plate corners.",
  "function_body": "radius = min(params.plate_width * 0.05, 3.0)\nreturn plate.edges(\"|Z\").fillet(radius)"
}'

# Same, but reading args from a file instead of an inline string
python3 workers/agent_3d/tools/tool_harness.py run create_feature --args-file my_args.json

# Against a candidate you seed yourself
python3 workers/agent_3d/tools/tool_harness.py run create_feature \
  --args-file my_args.json --seed-file my_model.py

# Against real Supabase-backed storage, one-off with flags. Requires
# SUPABASE_URL (repo-root .env) + SUPABASE_SERVICE_ROLE_KEY
# (workers/agent_3d/.env), and a real project/part/candidate that already
# has source in storage -- nothing is seeded or created for you here.
python3 workers/agent_3d/tools/tool_harness.py run create_feature \
  --backend supabase \
  --project-id <uuid> --part-id <uuid> --candidate-id <edit-job-id> \
  --args-file my_args.json

# Against real Supabase-backed storage, config-file-driven (see "Testing
# against real Supabase" above) -- same requirements, shorter command:
python3 workers/agent_3d/tools/tool_harness.py real
```

`run` and `real` exit `0` on `ToolSuccess`, `1` on `ToolFailure` (or invalid
arguments JSON, or, for `real`, a missing/incomplete config file). `scenario`
exits `0` only if every requested scenario's checks all passed.

## Running the pytest suite

```
pytest tests/test_cad_create_feature_unit.py
pytest tests/test_cad_create_feature_integration.py
pytest tests/test_cad_create_feature_unit.py tests/test_cad_create_feature_integration.py
```

See `cad_editor/tools/CREATE_FEATURE_TOOL.md` for what each test file covers
in detail, and the wider regression check (applier/tool-framework suites
`create_feature` shares generation and registration logic with):

```
pytest tests/test_cad_editor_core.py tests/test_cad_tool_framework.py tests/test_cad_editor_agent.py
```

## Adding a scenario for a future tool

As more tools land in `tools/` (per the ongoing legacy-logic migration --
see `cad_editor/tools/CREATE_FEATURE_TOOL.md`), give each mutating tool at
least one scenario: a success case that checks the specific effect the tool
is supposed to have, and a rejection case that checks state is unchanged
after a rejected call. Add the tool to `build_registry()`'s
`register_many([...])` call and to the `tool_ids` list in `cmd_list`, write
an `async def _scenario_<name>()` following the existing three as a
template, and register it in the `SCENARIOS` dict.
