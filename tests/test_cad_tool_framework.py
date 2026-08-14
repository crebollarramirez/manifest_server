from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from typing import Any

from pydantic import ConfigDict

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.planning.resolver import resolve_deterministic_edit_target
from workers.agent_3d.tools.base import (
    BATCH_GROUP_PARAMETER_CREATE,
    BATCH_GROUP_READ,
)
from workers.agent_3d.tools.tool_harness import build_registry
from workers.agent_3d.tools import (
    AgentTool,
    DuplicateToolError,
    IndexGetFeatureTool,
    IndexSearchTool,
    InvalidToolDefinitionError,
    StrictToolModel,
    ToolExecutionContext,
    ToolExecutor,
    ToolFailure,
    ToolInputRejected,
    ToolRegistry,
    ToolServices,
    ToolSuccess,
    Toolbox,
    UnknownToolError,
)


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PART_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
ROOT = Path(__file__).resolve().parents[1]


class Repository:
    def __init__(self, index: dict[str, Any] | None = None):
        self.index = index or {
            "schema_version": 1,
            "project_id": PROJECT_ID,
            "parts": [
                {
                    "part_id": PART_ID,
                    "part_name": "Holder",
                    "model_params": [
                        {"name": "hole_diameter_mm", "type_name": "float"}
                    ],
                    "cad_parts": [
                        {
                            "semantic_id": "holder_floor",
                            "role": "supporting floor",
                            "parameters": [],
                            "depends_on": [],
                            "search_keys": ["holder floor"],
                            "function_name": "build_holder_floor",
                        },
                        {
                            "semantic_id": "drainage_holes",
                            "role": "drainage openings",
                            "parameters": ["hole_diameter_mm"],
                            "depends_on": ["holder_floor"],
                            "search_keys": ["drainage holes"],
                            "function_name": "cut_drainage_holes",
                        },
                    ],
                }
            ],
        }

    def read_text(self, _path: str) -> str:
        return json.dumps(self.index)


def context(repository: Repository | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        part_id=PART_ID,
        candidate_id=None,
        services=ToolServices(repository=repository or Repository()),
    )


class ExampleInput(StrictToolModel):
    value: int


class ExampleOutput(StrictToolModel):
    doubled: int


class ExampleTool(AgentTool[ExampleInput, ExampleOutput]):
    tool_id = "example_double"
    version = 1
    description = "Double one validated integer without changing server state."
    input_model = ExampleInput
    output_model = ExampleOutput
    batch_group = BATCH_GROUP_READ

    def __init__(self):
        self.trace: list[str] = []
        self.received: ExampleInput | None = None

    async def validate_input(
        self,
        tool_input: ExampleInput,
        _context: ToolExecutionContext,
    ) -> ExampleInput:
        self.trace.append("validate")
        if tool_input.value < 0:
            raise ToolInputRejected("Value must be nonnegative.")
        return tool_input

    async def execute(
        self,
        tool_input: ExampleInput,
        _context: ToolExecutionContext,
    ) -> ExampleOutput:
        self.trace.append("execute")
        self.received = tool_input
        return ExampleOutput(doubled=tool_input.value * 2)


class InvalidOutputTool(ExampleTool):
    tool_id = "example_invalid_output"

    async def execute(  # type: ignore[override]
        self,
        _tool_input: ExampleInput,
        _context: ToolExecutionContext,
    ) -> Any:
        return {"wrong": 1}


class ExplodingTool(ExampleTool):
    tool_id = "example_explode"

    async def execute(
        self,
        _tool_input: ExampleInput,
        _context: ToolExecutionContext,
    ) -> ExampleOutput:
        raise RuntimeError("internal-secret-value")


class ToolFrameworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_pipeline_validates_before_typed_execution(self):
        tool = ExampleTool()

        result = await tool.run({"value": 4}, context())

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.doubled, 8)
        self.assertEqual(tool.trace, ["validate", "execute"])
        self.assertIsInstance(tool.received, ExampleInput)

    async def test_missing_extra_and_incorrect_types_are_rejected(self):
        tool = ExampleTool()
        for arguments in ({}, {"value": 2, "extra": True}, {"value": "2"}):
            with self.subTest(arguments=arguments):
                result = await tool.run(arguments, context())
                self.assertIsInstance(result, ToolFailure)
                self.assertEqual(result.error.code, "TOOL_INPUT_INVALID")
        self.assertEqual(tool.trace, [])

    async def test_domain_validation_prevents_execution(self):
        tool = ExampleTool()

        result = await tool.run({"value": -1}, context())

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertEqual(tool.trace, ["validate"])

    async def test_invalid_output_is_rejected(self):
        result = await InvalidOutputTool().run({"value": 1}, context())

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_OUTPUT_INVALID")

    async def test_unexpected_error_is_logged_and_sanitized(self):
        with self.assertLogs(
            "workers.agent_3d.tools.base", level="ERROR"
        ) as captured:
            result = await ExplodingTool().run({"value": 1}, context())

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_EXECUTION_FAILED")
        self.assertNotIn("internal-secret-value", result.error.message)
        self.assertIn("tool_id=example_explode", "\n".join(captured.output))

    def test_registry_accepts_valid_and_rejects_duplicate_or_unknown_ids(self):
        registry = ToolRegistry()
        registry.register(ExampleTool())
        self.assertIsInstance(registry.get("example_double"), ExampleTool)
        with self.assertRaises(DuplicateToolError):
            registry.register(ExampleTool())
        with self.assertRaises(UnknownToolError):
            registry.get("example_missing")

    def test_registry_rejects_malformed_definitions(self):
        class VagueTool(ExampleTool):
            tool_id = "search"

        class BadVersionTool(ExampleTool):
            tool_id = "example_bad_version"
            version = 0

        class BadDescriptionTool(ExampleTool):
            tool_id = "example_bad_description"
            description = ""

        class BadModelTool(ExampleTool):
            tool_id = "example_bad_model"
            input_model = dict  # type: ignore[assignment]

        class LooseInput(StrictToolModel):
            model_config = ConfigDict(extra="allow", strict=True, frozen=True)
            value: int

        class BadConfigTool(ExampleTool):
            tool_id = "example_bad_config"
            input_model = LooseInput  # type: ignore[assignment]

        class MissingExecuteTool(ExampleTool):
            tool_id = "example_missing_execute"
            execute = None  # type: ignore[assignment]

        class OverrideRunTool(ExampleTool):
            tool_id = "example_override_run"

            async def run(self, raw_arguments, tool_context):  # type: ignore[override]
                return await super().run(raw_arguments, tool_context)

        class BadBatchGroupTool(ExampleTool):
            tool_id = "example_bad_batch_group"
            batch_group = 3  # type: ignore[assignment]

        class UnknownBatchGroupTool(ExampleTool):
            # A plausible typo. Without the closed BATCH_GROUPS set this would
            # register happily and form a silent singleton group that never
            # batches with anything -- the exact failure the set exists to
            # turn into a registration error.
            tool_id = "example_unknown_batch_group"
            batch_group = "parameter_creation"  # type: ignore[assignment]

        class MissingBatchMetadataTool(AgentTool[ExampleInput, ExampleOutput]):
            tool_id = "example_missing_batch_metadata"
            version = 1
            description = "Declares no batch_group at all."
            input_model = ExampleInput
            output_model = ExampleOutput

            async def execute(
                self,
                tool_input: ExampleInput,
                _context: ToolExecutionContext,
            ) -> ExampleOutput:
                return ExampleOutput(doubled=tool_input.value * 2)

        for malformed in (
            VagueTool(),
            BadVersionTool(),
            BadDescriptionTool(),
            BadModelTool(),
            BadConfigTool(),
            MissingExecuteTool(),
            OverrideRunTool(),
            BadBatchGroupTool(),
            UnknownBatchGroupTool(),
            MissingBatchMetadataTool(),
        ):
            with self.subTest(tool=type(malformed).__name__):
                with self.assertRaises(InvalidToolDefinitionError):
                    ToolRegistry().register(malformed)

    async def test_executor_resolves_only_through_exact_registry_id(self):
        registry = ToolRegistry()
        registry.register(ExampleTool())
        executor = ToolExecutor(registry)

        success = await executor.execute(
            tool_id="example_double",
            raw_arguments={"value": 3},
            context=context(),
        )
        missing = await executor.execute(
            tool_id="example.double",
            raw_arguments={"value": 3},
            context=context(),
        )

        self.assertIsInstance(success, ToolSuccess)
        self.assertIsInstance(missing, ToolFailure)
        self.assertEqual(missing.error.code, "TOOL_NOT_FOUND")

    def test_toolbox_uses_canonical_ids_and_excludes_server_context(self):
        registry = ToolRegistry()
        registry.register_many([IndexSearchTool(), IndexGetFeatureTool()])

        definitions = Toolbox(registry).get_tool_definitions(
            ["index_search", "index_get_feature"]
        )

        self.assertEqual(
            [definition["name"] for definition in definitions],
            ["index_search", "index_get_feature"],
        )
        self.assertEqual(
            set(definitions[1]["parameters"]["properties"]),
            {"semantic_id"},
        )
        serialized = json.dumps(definitions)
        for server_field in (
            "run_id",
            "project_id",
            "part_id",
            "candidate_id",
            "services",
        ):
            self.assertNotIn(server_field, serialized)

    def test_get_definitions_returns_only_the_allowlisted_tools_in_order(self):
        registry = ToolRegistry()
        registry.register_many(
            [
                IndexSearchTool(),
                IndexGetFeatureTool(),
                ExampleTool(),
            ]
        )

        definitions = registry.get_definitions(
            allowed_tools=["example_double", "index_search"],
        )

        self.assertEqual(
            [definition["name"] for definition in definitions],
            ["example_double", "index_search"],
        )
        self.assertEqual(
            definitions[0]["parameters"],
            ExampleInput.model_json_schema(),
        )
        self.assertEqual(definitions[0]["description"], ExampleTool.description)

    def test_get_definitions_excludes_registered_but_unallowed_tools(self):
        registry = ToolRegistry()
        registry.register_many([IndexSearchTool(), IndexGetFeatureTool()])

        definitions = registry.get_definitions(allowed_tools=["index_search"])

        self.assertEqual(
            [definition["name"] for definition in definitions],
            ["index_search"],
        )

    def test_get_definitions_empty_allowlist_returns_empty_catalog(self):
        registry = ToolRegistry()
        registry.register(ExampleTool())

        self.assertEqual(registry.get_definitions(allowed_tools=[]), [])

    def test_get_definitions_rejects_unknown_allowed_tool_id(self):
        registry = ToolRegistry()
        registry.register(ExampleTool())

        with self.assertRaises(UnknownToolError):
            registry.get_definitions(allowed_tools=["example_double", "no_such_tool"])

    def test_get_definitions_rejects_malformed_or_duplicate_allowed_ids(self):
        registry = ToolRegistry()
        registry.register(ExampleTool())

        with self.assertRaises(InvalidToolDefinitionError):
            registry.get_definitions(allowed_tools=["example_double", ""])
        with self.assertRaises(DuplicateToolError):
            registry.get_definitions(allowed_tools=["example_double", "example_double"])

    async def test_migrated_index_tools_preserve_search_and_context_behavior(self):
        registry = ToolRegistry()
        registry.register_many([IndexSearchTool(), IndexGetFeatureTool()])
        executor = ToolExecutor(registry)

        with self.assertLogs(
            "workers.agent_3d.tools.registry", level="INFO"
        ) as captured:
            search = await executor.execute(
                tool_id="index_search",
                raw_arguments={"query": "drainage holes", "limit": 5},
                context=context(),
            )
        feature = await executor.execute(
            tool_id="index_get_feature",
            raw_arguments={"semantic_id": "drainage_holes"},
            context=context(),
        )

        self.assertIsInstance(search, ToolSuccess)
        self.assertEqual(search.data.status, "ok")
        self.assertEqual(search.data.matches[0].semantic_id, "drainage_holes")
        self.assertIsInstance(feature, ToolSuccess)
        self.assertEqual(feature.data.status, "ok")
        self.assertEqual(feature.data.target.semantic_id, "drainage_holes")
        self.assertEqual(
            feature.data.dependencies[0].semantic_id,
            "holder_floor",
        )
        self.assertIn("tool_id=index_search", "\n".join(captured.output))

    def test_legacy_planning_tool_path_is_removed(self):
        self.assertFalse(
            (
                ROOT
                / "workers"
                / "agent_3d"
                / "planning_read_tools.py"
            ).exists()
        )
        planning_source = (
            ROOT
            / "workers"
            / "agent_3d"
            / "planning"
            / "planning_agent.py"
        ).read_text(encoding="utf-8")
        registry_source = (
            ROOT
            / "workers"
            / "agent_3d"
            / "tools"
            / "registry.py"
        ).read_text(encoding="utf-8")
        for obsolete in (
            "def _tool_definition(",
            "search_semantic_index",
            "get_semantic_context",
            "index.search",
            "index__search",
            "provider_name",
            "tool_alias",
        ):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete, planning_source)
                self.assertNotIn(obsolete, registry_source)
        self.assertNotIn(".execute(", planning_source)


class ToolExecutorSyncBridgeTests(unittest.TestCase):
    def test_sync_bridge_returns_the_normalized_result(self):
        registry = ToolRegistry()
        registry.register(ExampleTool())

        result = ToolExecutor(registry).execute_sync(
            tool_id="example_double",
            raw_arguments={"value": 5},
            context=context(),
        )

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.doubled, 10)

    def test_sync_bridge_rejects_an_active_event_loop(self):
        registry = ToolRegistry()
        registry.register(ExampleTool())
        executor = ToolExecutor(registry)

        async def invoke() -> None:
            with self.assertRaises(RuntimeError):
                executor.execute_sync(
                    tool_id="example_double",
                    raw_arguments={"value": 5},
                    context=context(),
                )

        asyncio.run(invoke())


class SoloExampleTool(ExampleTool):
    tool_id = "example_solo"
    batch_group = None


class ToolExecutorBatchMetadataTests(unittest.TestCase):
    def test_batch_group_reflects_a_batchable_tool(self):
        registry = ToolRegistry()
        registry.register(ExampleTool())
        executor = ToolExecutor(registry)

        self.assertEqual(executor.batch_group("example_double"), BATCH_GROUP_READ)

    def test_batch_group_is_none_for_a_solo_tool(self):
        registry = ToolRegistry()
        registry.register(SoloExampleTool())
        executor = ToolExecutor(registry)

        self.assertIsNone(executor.batch_group("example_solo"))

    def test_a_declared_none_batch_group_registers_successfully(self):
        # None is a legal declared value, distinct from never declaring one --
        # which is why registration uses a sentinel rather than a getattr
        # default.
        registry = ToolRegistry()

        registry.register(SoloExampleTool())

        self.assertIsNone(registry.get("example_solo").batch_group)

    def test_batch_group_fails_closed_for_an_unknown_tool(self):
        registry = ToolRegistry()
        executor = ToolExecutor(registry)

        self.assertIsNone(executor.batch_group("not_a_real_tool"))


class ProductionBatchGroupTests(unittest.TestCase):
    """The exact batch group of every tool the worker registers.

    A table rather than per-tool assertions: this is the one test that
    catches BATCH_GROUP_PARAMETER_CREATE pasted onto delete_parameter, or a
    tool quietly gaining the ability to share a round with another.
    """

    def test_only_reads_and_parameter_creates_may_share_a_round(self):
        registry = build_registry()
        executor = ToolExecutor(registry)
        expected = {
            "index_search": BATCH_GROUP_READ,
            "index_get_feature": BATCH_GROUP_READ,
            "create_parameter": BATCH_GROUP_PARAMETER_CREATE,
            "edit_parameter": None,
            "delete_parameter": None,
            "create_feature": None,
            "edit_feature": None,
            "delete_feature": None,
            "create_cad_part": None,
            "edit_cad_build_model": None,
        }

        actual = {
            tool_id: executor.batch_group(tool_id) for tool_id in expected
        }

        self.assertEqual(actual, expected)


class PlanningTargetResolutionTests(unittest.TestCase):
    class Getter:
        def __init__(self, matches: list[dict[str, Any]]):
            self.matches = matches
            self.parts = {
                PART_ID: {
                    "part_name": "Holder",
                    "cad_parts": [
                        {"semantic_id": "holder_floor"},
                        {"semantic_id": "drainage_holes"},
                        {"semantic_id": "unrelated_feature"},
                    ],
                }
            }

        def search_parts(self, _request: str, _limit: int):
            return list(self.matches)

        def dependency_graph(self, _part_id: str):
            return {
                "nodes": [
                    {
                        "semantic_id": "drainage_holes",
                        "transitive_dependencies": ["holder_floor"],
                        "transitive_dependents": [],
                    }
                ]
            }

    def test_linked_part_is_authoritative(self):
        target = resolve_deterministic_edit_target(
            self.Getter([]),
            "add drainage holes",
            requested_part_id=PART_ID,
        )

        self.assertEqual(target.part_id, PART_ID)
        self.assertEqual(
            target.semantic_ids,
            ["holder_floor", "drainage_holes", "unrelated_feature"],
        )
        self.assertEqual(target.confidence, 1)

    def test_unlinked_part_uses_existing_confidence_and_dependency_expansion(self):
        target = resolve_deterministic_edit_target(
            self.Getter(
                [
                    {
                        "part_id": PART_ID,
                        "part_name": "Holder",
                        "semantic_id": "drainage_holes",
                        "score": 0.95,
                    },
                    {
                        "part_id": "other-part",
                        "part_name": "Other",
                        "semantic_id": "other_feature",
                        "score": 0.40,
                    },
                ]
            ),
            "add drainage holes",
        )

        self.assertEqual(target.part_id, PART_ID)
        self.assertEqual(target.semantic_ids, ["holder_floor", "drainage_holes"])

    def test_unlinked_ambiguous_or_missing_target_fails_clearly(self):
        with self.assertRaises(WorkflowFailure) as ambiguous:
            resolve_deterministic_edit_target(
                self.Getter(
                    [
                        {
                            "part_id": PART_ID,
                            "part_name": "Holder",
                            "semantic_id": "drainage_holes",
                            "score": 0.86,
                        },
                        {
                            "part_id": "other-part",
                            "part_name": "Other",
                            "semantic_id": "other_feature",
                            "score": 0.80,
                        },
                    ]
                ),
                "add holes",
            )
        self.assertEqual(ambiguous.exception.code, "AMBIGUOUS_TARGET")

        with self.assertRaises(WorkflowFailure) as missing:
            resolve_deterministic_edit_target(self.Getter([]), "add holes")
        self.assertEqual(missing.exception.code, "NO_INDEXED_TARGET")


if __name__ == "__main__":
    unittest.main()
