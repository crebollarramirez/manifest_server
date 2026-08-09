"""Interactive CLI harness for exercising registered agent tools by hand.

Lives in ``tools/`` deliberately: it only knows how to drive tools through
the strict ``ToolRegistry`` / ``AgentTool.run()`` contract, which is
tools/-owned business logic. Legacy code (``applier.py``, ``EditPlan`` /
``ToolPlan`` operations, the reasoner/orchestrator pipeline) does not go
through that contract and cannot use this harness -- only tools that have
been migrated into ``tools/`` are callable here. As more tools move in
(per the ongoing incremental migration), they become testable this way for
free.

Three subcommands:

  list       -- show every registered tool's ID, version, and description.
  run        -- call one tool with arbitrary JSON arguments and see exactly
                what it did (structured result + a diff of any changed file).
  scenario   -- run a small set of built-in, asserted scenarios and get a
                clear PASS/FAIL verdict that writes/edits actually worked.

Examples:

    python3 workers/agent_3d/tools/tool_harness.py list

    python3 workers/agent_3d/tools/tool_harness.py scenario --all

    python3 workers/agent_3d/tools/tool_harness.py run create_feature --args '{
        "semantic_id": "corner_fillets",
        "function_name": "fillet_base_corners",
        "role": "aesthetic_features",
        "parameters": ["plate_width"],
        "dependencies": [{"semantic_id": "base_plate", "argument_name": "plate"}],
        "search_keys": ["corner fillets", "rounded corners"],
        "docstring": "Round the base plate corners.",
        "function_body": "radius = min(params.plate_width * 0.05, 3.0)\\nreturn plate.edges(\\"|Z\\").fillet(radius)"
    }'
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import difflib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

# This file lives at workers/agent_3d/tools/tool_harness.py.
# WORKERS_ROOT (workers/) must be on sys.path for `import agent_3d` to
# resolve when this script is run directly; WORKSPACE_ROOT (the repo root)
# is additionally needed for `workers.indexer` imports pulled in by
# agent_3d.repository (the --backend supabase path only).
AGENT_ROOT = str(Path(__file__).resolve().parents[1])
WORKERS_ROOT = str(Path(__file__).resolve().parents[2])
WORKSPACE_ROOT = str(Path(__file__).resolve().parents[3])
for _root in (WORKERS_ROOT, WORKSPACE_ROOT):
    if _root not in sys.path:
        sys.path.insert(0, _root)

from workers.agent_3d.failures import WorkflowFailure  # noqa: E402
from workers.agent_3d.tools import (  # noqa: E402
    CreateCadPartTool,
    CreateFeatureTool,
    CreateParameterTool,
    DeleteFeatureTool,
    DeleteParameterTool,
    EditCadBuildModelTool,
    EditFeatureTool,
    EditParameterTool,
    IndexGetFeatureTool,
    IndexSearchTool,
    ToolExecutionContext,
    ToolFailure,
    ToolRegistry,
    ToolServices,
    ToolSuccess,
    Toolbox,
)

DEFAULT_PROJECT_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_PART_ID = "00000000-0000-4000-8000-000000000002"
DEFAULT_CANDIDATE_ID = "harness-candidate"

DEFAULT_FIXTURE_SOURCE = '''"""Generated CAD model."""
from cadquery_runtime import cad_part, cq, dataclass


@dataclass(frozen=True)
class ModelParams:
    hole_diameter: float = 4.0
    plate_width: float = 20.0
    plate_height: float = 10.0


@cad_part(
    semantic_id='mount_holes',
    role='fastener_features',
    library="cadquery",
    parameters=('hole_diameter',),
    depends_on=(),
    search_keys=('mounting holes', 'screw holes'),
)
def cut_mounting_holes(params: ModelParams):
    return cq.Workplane("XY").circle(params.hole_diameter / 2).extrude(2)


# PART-START: base_plate
@cad_part(
    semantic_id='base_plate',
    role='structural_plate',
    library="cadquery",
    parameters=('plate_width', 'plate_height'),
    depends_on=(),
    search_keys=('base plate', 'structural plate'),
)
def build_base_plate(params: ModelParams):
    return cq.Workplane("XY").box(params.plate_width, params.plate_height, 2)
# PART-END: base_plate


def build_model(params: ModelParams):
    return cut_mounting_holes(params)
'''


def _candidate_path(project_id: str, part_id: str, candidate_id: str) -> str:
    return f"{project_id}/candidates/cad/{part_id}/{candidate_id}/model.py"


class InMemoryRepository:
    """Dict-backed ``ToolRepository`` -- zero setup, safe to run repeatedly."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})

    def read_text(self, path: str) -> str:
        try:
            return self.files[path]
        except KeyError as exc:
            raise WorkflowFailure("SOURCE_MISSING", f"{path} was not found.") from exc

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content

    def snapshot(self) -> dict[str, str]:
        return dict(self.files)


def build_registry() -> ToolRegistry:
    """Register the same tools edit_worker.py registers, without the OpenAI
    reasoning runtime build_runtime() also unconditionally requires."""

    registry = ToolRegistry()
    registry.register_many(
        [
            IndexSearchTool(),
            IndexGetFeatureTool(),
            CreateFeatureTool(),
            EditFeatureTool(),
            DeleteFeatureTool(),
            CreateCadPartTool(),
            CreateParameterTool(),
            EditParameterTool(),
            DeleteParameterTool(),
            EditCadBuildModelTool(),
        ]
    )
    return registry


def build_supabase_repository():
    import os

    # python-dotenv is a repo-root-only dependency (workers/agent_3d's own
    # container never needed it -- production edit_worker.py gets its env
    # vars straight from the container environment, not a .env file). When
    # running this harness on the host, use it to load SUPABASE_URL (repo-
    # root .env) and SUPABASE_SERVICE_ROLE_KEY (workers/agent_3d/.env; that
    # file also has SUPABASE_URL_DOCKER, a docker-only host alias not used
    # here). When running inside the cad-editor container, dotenv isn't
    # installed, but both vars are already set directly in the container
    # environment -- skip file loading and fall through to os.environ.
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        load_dotenv = None
    if load_dotenv is not None:
        load_dotenv(Path(WORKSPACE_ROOT) / ".env")
        load_dotenv(Path(AGENT_ROOT) / ".env")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit(
            "error: --backend supabase requires SUPABASE_URL (repo-root .env) "
            "and SUPABASE_SERVICE_ROLE_KEY (workers/agent_3d/.env)"
        )
    from supabase import create_client

    from workers.agent_3d.repository import SupabaseEditRepository

    return SupabaseEditRepository(create_client(url, key))


def _print_header(title: str) -> None:
    print(f"\n=== {title} ===")


def cmd_list(_args: argparse.Namespace) -> int:
    registry = build_registry()
    toolbox = Toolbox(registry)
    tool_ids = [
        "index_search",
        "index_get_feature",
        "create_feature",
        "edit_feature",
        "delete_feature",
        "create_cad_part",
        "create_parameter",
        "edit_parameter",
        "delete_parameter",
        "edit_cad_build_model",
    ]
    definitions = toolbox.get_tool_definitions(tool_ids)
    _print_header("Registered tools")
    for definition in definitions:
        print(f"- {definition['name']}")
        print(f"    {definition['description']}\n")
    return 0


def _load_args_json(args: argparse.Namespace) -> str:
    if args.args_file:
        return Path(args.args_file).read_text(encoding="utf-8")
    if args.args is not None:
        return args.args
    raise SystemExit("error: run requires --args '<json>' or --args-file <path>")


async def _run_tool(
    tool_id: str,
    args_json: str,
    *,
    context: ToolExecutionContext,
    registry: ToolRegistry,
):
    tool = registry.get(tool_id)
    try:
        # StrictToolModel is strict=True: a plain dict built via json.loads()
        # rejects list values for tuple[...] fields (e.g. CreateFeatureInput's
        # `parameters`), even though a JSON array is the natural encoding of a
        # tuple. model_validate_json parses JSON-mode, which does accept it,
        # and model_dump(mode="python") preserves the resulting tuple type --
        # so ordinary JSON args "just work" instead of failing
        # TOOL_INPUT_INVALID on every list-valued field.
        parsed_input = tool.input_model.model_validate_json(args_json)
    except Exception as exc:  # pydantic.ValidationError or json errors
        print(f"error: invalid --args JSON for {tool_id}: {exc}", file=sys.stderr)
        return None
    raw_arguments = parsed_input.model_dump(mode="python")
    return await tool.run(raw_arguments, context)


def _print_result(result) -> None:
    _print_header("Result")
    if result is None:
        print("(no result -- argument parsing failed, see error above)")
        return
    if isinstance(result, ToolSuccess):
        print("ok: true")
        print(json.dumps(result.data.model_dump(mode="json"), indent=2))
    elif isinstance(result, ToolFailure):
        print("ok: false")
        print(f"code: {result.error.code}")
        print(f"message: {result.error.message}")
        print(f"retryable: {result.error.retryable}")
        if result.error.details:
            print("details:")
            print(json.dumps(result.error.details, indent=2))
    else:
        print(repr(result))


def _print_diff(before: dict[str, str], after: dict[str, str]) -> None:
    _print_header("Changed files")
    changed_paths = sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )
    if not changed_paths:
        print("(none)")
        return
    for path in changed_paths:
        before_lines = before.get(path, "").splitlines(keepends=True)
        after_lines = after.get(path, "").splitlines(keepends=True)
        diff = difflib.unified_diff(
            before_lines, after_lines, fromfile=f"a/{path}", tofile=f"b/{path}"
        )
        print("".join(diff) or f"(no textual diff for {path})")
        if path.endswith(".py") and path in after:
            try:
                ast.parse(after[path])
                print(f"[{path}] parses as valid Python: yes")
            except SyntaxError as exc:
                print(f"[{path}] parses as valid Python: NO -- {exc}")


def cmd_run(args: argparse.Namespace) -> int:
    args_json = _load_args_json(args)
    registry = build_registry()

    if args.backend == "supabase":
        repository = build_supabase_repository()
        before_snapshot: dict[str, str] = {}
    else:
        repository = InMemoryRepository()
        seed_path = _candidate_path(args.project_id, args.part_id, args.candidate_id)
        if args.seed_file:
            repository.files[seed_path] = Path(args.seed_file).read_text(encoding="utf-8")
        elif not args.no_seed:
            repository.files[seed_path] = DEFAULT_FIXTURE_SOURCE
        before_snapshot = repository.snapshot()

    context = ToolExecutionContext(
        run_id=args.run_id or str(uuid.uuid4()),
        project_id=args.project_id,
        part_id=args.part_id,
        candidate_id=args.candidate_id,
        services=ToolServices(repository=repository),
    )

    _print_header("Context")
    print(f"tool_id:     {args.tool_id}")
    print(f"backend:     {args.backend}")
    print(f"project_id:  {context.project_id}")
    print(f"part_id:     {context.part_id}")
    print(f"candidate_id:{context.candidate_id}")

    _print_header("Arguments")
    print(args_json)

    result = asyncio.run(
        _run_tool(args.tool_id, args_json, context=context, registry=registry)
    )
    _print_result(result)

    if args.backend == "memory":
        after_snapshot = repository.snapshot()
        _print_diff(before_snapshot, after_snapshot)

    if result is None or isinstance(result, ToolFailure):
        return 1
    return 0


def _read_optional(repository, path: str) -> str | None:
    try:
        return repository.read_text(path)
    except WorkflowFailure:
        return None


DEFAULT_REAL_CONFIG_PATH = Path(__file__).resolve().parent / "real_supabase_test.json"


def cmd_real(args: argparse.Namespace) -> int:
    """Run one tool against real, already-existing Supabase-backed storage.

    Arguments live in a local, gitignored JSON file (see
    real_supabase_test.json.example) instead of on the command line, so the
    command itself stays short and the real project_id/part_id/candidate_id
    aren't retyped every run. This never seeds or creates anything -- it only
    reads/writes the exact candidate the config file names.
    """

    config_path = Path(args.config) if args.config else DEFAULT_REAL_CONFIG_PATH
    if not config_path.exists():
        print(
            f"error: {config_path} not found. Copy "
            f"{config_path.with_suffix('.json.example').name} to "
            f"{config_path.name} and fill in real IDs.",
            file=sys.stderr,
        )
        return 2
    config = json.loads(config_path.read_text(encoding="utf-8"))

    for field in ("tool_id", "project_id", "args"):
        if field not in config:
            print(f"error: {config_path} is missing required field '{field}'", file=sys.stderr)
            return 2

    # part_id/candidate_id are optional: a project-scoped tool (e.g.
    # create_cad_part, which produces a part_id rather than being given one)
    # doesn't need either in the config.
    part_id = config.get("part_id") or ""
    candidate_id = config.get("candidate_id")

    registry = build_registry()
    repository = build_supabase_repository()
    context = ToolExecutionContext(
        run_id=str(uuid.uuid4()),
        project_id=config["project_id"],
        part_id=part_id,
        candidate_id=candidate_id,
        services=ToolServices(repository=repository),
    )
    known_path = _candidate_path(config["project_id"], part_id, candidate_id) if part_id and candidate_id else None

    _print_header("Context (real Supabase)")
    print(f"config file: {config_path}")
    print(f"tool_id:     {config['tool_id']}")
    print(f"project_id:  {context.project_id}")
    print(f"part_id:     {context.part_id or '(none -- project-scoped tool)'}")
    print(f"candidate_id:{context.candidate_id or '(none)'}")
    if known_path:
        print(f"candidate path: {known_path}")

    args_json = json.dumps(config["args"])
    _print_header("Arguments")
    print(args_json)

    before_content = _read_optional(repository, known_path) if known_path else None

    result = asyncio.run(
        _run_tool(config["tool_id"], args_json, context=context, registry=registry)
    )
    _print_result(result)

    # Some tools (e.g. create_cad_part) write to a path that isn't knowable
    # until after they run (it contains a part_id the tool itself creates) --
    # pick that up from the output so the diff still shows what changed.
    output_path = getattr(result.data, "source_path", None) if isinstance(result, ToolSuccess) else None
    diff_paths = {path for path in (known_path, output_path) if path}

    before_snapshot: dict[str, str] = {}
    after_snapshot: dict[str, str] = {}
    for path in diff_paths:
        before = before_content if path == known_path else None
        after = _read_optional(repository, path)
        if before is not None:
            before_snapshot[path] = before
        if after is not None:
            after_snapshot[path] = after
    _print_diff(before_snapshot, after_snapshot)

    if result is None or isinstance(result, ToolFailure):
        return 1
    return 0


# --- scenarios ---------------------------------------------------------


def _check(label: str, condition: bool, failures: list[str]) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        failures.append(label)


async def _scenario_create_feature_basic() -> list[str]:
    print("scenario: create_feature_basic")
    repository = InMemoryRepository(
        {_candidate_path(DEFAULT_PROJECT_ID, DEFAULT_PART_ID, DEFAULT_CANDIDATE_ID): DEFAULT_FIXTURE_SOURCE}
    )
    context = ToolExecutionContext(
        run_id=str(uuid.uuid4()),
        project_id=DEFAULT_PROJECT_ID,
        part_id=DEFAULT_PART_ID,
        candidate_id=DEFAULT_CANDIDATE_ID,
        services=ToolServices(repository=repository),
    )
    tool = CreateFeatureTool()
    result = await tool.run(
        {
            "semantic_id": "corner_fillets",
            "function_name": "fillet_base_corners",
            "role": "aesthetic_features",
            "parameters": ("plate_width",),
            "dependencies": ({"semantic_id": "base_plate", "argument_name": "plate"},),
            "search_keys": ("corner fillets", "rounded corners"),
            "docstring": "Round the base plate's vertical corners.",
            "function_body": (
                "radius = min(params.plate_width * 0.05, 3.0)\n"
                'return plate.edges("|Z").fillet(radius)'
            ),
        },
        context,
    )
    failures: list[str] = []
    _check("tool call succeeded", isinstance(result, ToolSuccess), failures)
    if not isinstance(result, ToolSuccess):
        return failures
    content = repository.files[result.data.source_path]
    parses = True
    try:
        ast.parse(content)
    except SyntaxError:
        parses = False
    _check("resulting candidate parses with ast", parses, failures)
    _check(
        "decorator present with correct semantic_id",
        "@cad_part(\n    semantic_id='corner_fillets'," in content,
        failures,
    )
    _check(
        "build_model is unchanged",
        "def build_model(params: ModelParams):\n    return cut_mounting_holes(params)\n" in content,
        failures,
    )
    _check("requires_assembly_wiring is True", result.data.requires_assembly_wiring is True, failures)
    return failures


async def _scenario_create_feature_with_dependency() -> list[str]:
    print("scenario: create_feature_with_dependency")
    repository = InMemoryRepository(
        {_candidate_path(DEFAULT_PROJECT_ID, DEFAULT_PART_ID, DEFAULT_CANDIDATE_ID): DEFAULT_FIXTURE_SOURCE}
    )
    context = ToolExecutionContext(
        run_id=str(uuid.uuid4()),
        project_id=DEFAULT_PROJECT_ID,
        part_id=DEFAULT_PART_ID,
        candidate_id=DEFAULT_CANDIDATE_ID,
        services=ToolServices(repository=repository),
    )
    tool = CreateFeatureTool()
    result = await tool.run(
        {
            "semantic_id": "corner_fillets",
            "function_name": "fillet_base_corners",
            "role": "aesthetic_features",
            "parameters": (),
            "dependencies": ({"semantic_id": "base_plate", "argument_name": "plate"},),
            "search_keys": ("corner fillets",),
            "docstring": "Round the base plate's vertical corners.",
            "function_body": 'return plate.edges("|Z").fillet(1.0)',
        },
        context,
    )
    failures: list[str] = []
    _check("tool call succeeded", isinstance(result, ToolSuccess), failures)
    if not isinstance(result, ToolSuccess):
        return failures
    content = repository.files[result.data.source_path]
    _check(
        "dependency argument_name appears in generated signature",
        "def fillet_base_corners(\n    params: ModelParams,\n    plate,\n):" in content,
        failures,
    )
    _check(
        "depends_on in decorator matches declared dependency",
        result.data.dependency_semantic_ids == ("base_plate",),
        failures,
    )
    return failures


async def _scenario_create_feature_duplicate_semantic_id() -> list[str]:
    print("scenario: create_feature_duplicate_semantic_id")
    repository = InMemoryRepository(
        {_candidate_path(DEFAULT_PROJECT_ID, DEFAULT_PART_ID, DEFAULT_CANDIDATE_ID): DEFAULT_FIXTURE_SOURCE}
    )
    path = _candidate_path(DEFAULT_PROJECT_ID, DEFAULT_PART_ID, DEFAULT_CANDIDATE_ID)
    context = ToolExecutionContext(
        run_id=str(uuid.uuid4()),
        project_id=DEFAULT_PROJECT_ID,
        part_id=DEFAULT_PART_ID,
        candidate_id=DEFAULT_CANDIDATE_ID,
        services=ToolServices(repository=repository),
    )
    tool = CreateFeatureTool()
    result = await tool.run(
        {
            "semantic_id": "base_plate",  # already exists in the fixture
            "function_name": "build_second_base_plate",
            "role": "structural_plate",
            "parameters": (),
            "dependencies": (),
            "search_keys": ("duplicate",),
            "docstring": "Should be rejected.",
            "function_body": 'return cq.Workplane("XY")',
        },
        context,
    )
    failures: list[str] = []
    _check("tool call was rejected", isinstance(result, ToolFailure), failures)
    if isinstance(result, ToolFailure):
        _check(
            "rejected with TOOL_VALIDATION_FAILED",
            result.error.code == "TOOL_VALIDATION_FAILED",
            failures,
        )
    _check(
        "candidate source is unchanged after the rejected write",
        repository.files[path] == DEFAULT_FIXTURE_SOURCE,
        failures,
    )
    return failures


SCENARIOS: dict[str, Any] = {
    "create_feature_basic": _scenario_create_feature_basic,
    "create_feature_with_dependency": _scenario_create_feature_with_dependency,
    "create_feature_duplicate_semantic_id": _scenario_create_feature_duplicate_semantic_id,
}


def cmd_scenario(args: argparse.Namespace) -> int:
    if args.all:
        names = list(SCENARIOS)
    elif args.name:
        if args.name not in SCENARIOS:
            print(f"error: unknown scenario '{args.name}'. Known: {', '.join(SCENARIOS)}", file=sys.stderr)
            return 2
        names = [args.name]
    else:
        print(f"error: scenario requires a name or --all. Known: {', '.join(SCENARIOS)}", file=sys.stderr)
        return 2

    _print_header("Scenarios")
    overall_failures: dict[str, list[str]] = {}
    for name in names:
        failures = asyncio.run(SCENARIOS[name]())
        overall_failures[name] = failures
        verdict = "PASS" if not failures else "FAIL"
        print(f"-> {name}: {verdict}\n")

    _print_header("Summary")
    any_failed = False
    for name, failures in overall_failures.items():
        verdict = "PASS" if not failures else f"FAIL ({len(failures)} check(s))"
        print(f"{name}: {verdict}")
        any_failed = any_failed or bool(failures)
    return 1 if any_failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call registered CAD-agent tools by hand.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List registered tools and their descriptions.")

    run_parser = subparsers.add_parser("run", help="Call one tool with JSON arguments.")
    run_parser.add_argument("tool_id")
    args_group = run_parser.add_mutually_exclusive_group()
    args_group.add_argument("--args", help="Tool arguments as a raw JSON string.")
    args_group.add_argument("--args-file", help="Path to a JSON file of tool arguments.")
    run_parser.add_argument("--backend", choices=["memory", "supabase"], default="memory")
    run_parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    run_parser.add_argument("--part-id", default=DEFAULT_PART_ID)
    run_parser.add_argument("--candidate-id", default=DEFAULT_CANDIDATE_ID)
    run_parser.add_argument("--run-id")
    run_parser.add_argument("--seed-file", help="Seed the in-memory candidate from this model.py file.")
    run_parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Don't seed a default candidate fixture (memory backend only).",
    )

    scenario_parser = subparsers.add_parser(
        "scenario", help="Run built-in, asserted scenarios."
    )
    scenario_parser.add_argument("name", nargs="?", choices=list(SCENARIOS))
    scenario_parser.add_argument("--all", action="store_true", help="Run every scenario.")

    real_parser = subparsers.add_parser(
        "real",
        help="Run one tool against real Supabase storage, using arguments from a config file.",
    )
    real_parser.add_argument(
        "--config",
        help=f"Path to the config JSON (default: {DEFAULT_REAL_CONFIG_PATH}).",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "list":
        return cmd_list(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "scenario":
        return cmd_scenario(args)
    if args.command == "real":
        return cmd_real(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
