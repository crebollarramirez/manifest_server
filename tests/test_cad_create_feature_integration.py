from __future__ import annotations

import ast
import unittest

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.tools import (
    CreateFeatureTool,
    ToolExecutionContext,
    ToolFailure,
    ToolRegistry,
    ToolServices,
    ToolSuccess,
    Toolbox,
)


PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PART_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
RUN_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
CANDIDATE_ID = "edit-job-42"

# Representative accepted-source fixture: a frozen ModelParams dataclass, two
# existing semantic features (one carrying system-owned PART markers, as the
# server writes for previously-added features), and build_model.
ACCEPTED_SOURCE = '''"""Generated CAD model."""
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

ACCEPTED_PATH = f"{PROJECT_ID}/parts/{PART_ID}/model.py"
CANDIDATE_PATH = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{CANDIDATE_ID}/model.py"


class FakeRepository:
    """In-memory ``ToolRepository`` standing in for Supabase-backed storage."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})

    def read_text(self, path: str) -> str:
        try:
            return self.files[path]
        except KeyError as exc:
            raise WorkflowFailure("SOURCE_MISSING", f"{path} was not found.") from exc

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content


def make_repository() -> FakeRepository:
    return FakeRepository(
        {
            ACCEPTED_PATH: ACCEPTED_SOURCE,
            CANDIDATE_PATH: ACCEPTED_SOURCE,
        }
    )


def make_context(repository: FakeRepository) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        part_id=PART_ID,
        candidate_id=CANDIDATE_ID,
        services=ToolServices(repository=repository),
    )


NEW_FEATURE_ARGUMENTS = {
    "semantic_id": "corner_fillets",
    "function_name": "fillet_base_corners",
    "role": "aesthetic_features",
    "parameters": ("plate_width",),
    "dependencies": ({"semantic_id": "base_plate", "argument_name": "plate"},),
    "search_keys": ("corner fillets", "rounded corners"),
    "docstring": "Round the base plate's vertical corners.",
    "function_body": (
        "radius = min(params.plate_width * 0.05, 3.0)\n"
        "return plate.edges(\"|Z\").fillet(radius)"
    ),
}


class CreateFeatureIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_feature_in_edit_scoped_candidate(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(repository.files[ACCEPTED_PATH], ACCEPTED_SOURCE)
        self.assertNotEqual(repository.files[CANDIDATE_PATH], ACCEPTED_SOURCE)

    async def test_resulting_source_parses_with_ast(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        ast.parse(repository.files[CANDIDATE_PATH])  # raises on invalid syntax

    async def test_generated_cad_part_decorator_is_correct(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        self.assertIn(
            "@cad_part(\n"
            "    semantic_id='corner_fillets',\n"
            "    role='aesthetic_features',\n"
            '    library="cadquery",\n'
            "    parameters=('plate_width',),\n"
            "    depends_on=('base_plate',),\n"
            "    search_keys=('corner fillets', 'rounded corners'),\n"
            ")\n",
            content,
        )

    async def test_feature_is_inserted_before_build_model(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        self.assertLess(
            content.index("def fillet_base_corners("),
            content.index("def build_model("),
        )

    async def test_dependencies_reflected_in_metadata_and_arguments(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.dependency_semantic_ids, ("base_plate",))
        content = repository.files[CANDIDATE_PATH]
        self.assertIn(
            "def fillet_base_corners(\n    params: ModelParams,\n    plate,\n):",
            content,
        )

    async def test_parameters_synchronized_with_params_usage(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.parameters, ("plate_width",))

    async def test_unrelated_source_is_preserved_exactly(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        self.assertIn('"""Generated CAD model."""', content)
        self.assertIn(
            "class ModelParams:\n"
            "    hole_diameter: float = 4.0\n"
            "    plate_width: float = 20.0\n"
            "    plate_height: float = 10.0",
            content,
        )
        self.assertIn(
            "def cut_mounting_holes(params: ModelParams):\n"
            '    return cq.Workplane("XY").circle(params.hole_diameter / 2).extrude(2)',
            content,
        )

    async def test_build_model_is_unchanged(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertIn(
            "def build_model(params: ModelParams):\n"
            "    return cut_mounting_holes(params)\n",
            repository.files[CANDIDATE_PATH],
        )

    async def test_accepted_source_is_unchanged(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(repository.files[ACCEPTED_PATH], ACCEPTED_SOURCE)

    async def test_provenance_markers_are_unchanged(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        self.assertIn("# PART-START: base_plate", content)
        self.assertIn("# PART-END: base_plate", content)
        self.assertIn(
            "# PART-START: base_plate\n@cad_part(\n"
            "    semantic_id='base_plate',",
            content,
        )

    async def test_output_identifies_assembly_wiring_required(self):
        repository = make_repository()
        tool = CreateFeatureTool()

        result = await tool.run(dict(NEW_FEATURE_ARGUMENTS), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertTrue(result.data.requires_assembly_wiring)
        self.assertNotIn("build_model", result.data.summary.split(".")[0])

    async def test_invalid_input_does_not_mutate_candidate_source(self):
        repository = make_repository()
        tool = CreateFeatureTool()
        bad_arguments = dict(NEW_FEATURE_ARGUMENTS)
        bad_arguments["semantic_id"] = "base_plate"  # already exists

        result = await tool.run(bad_arguments, make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(repository.files[CANDIDATE_PATH], ACCEPTED_SOURCE)

    async def test_registered_tool_is_discoverable_through_toolbox(self):
        registry = ToolRegistry()
        registry.register(CreateFeatureTool())
        toolbox = Toolbox(registry)

        definitions = toolbox.get_tool_definitions(["create_feature"])

        self.assertEqual(len(definitions), 1)
        self.assertEqual(definitions[0]["name"], "create_feature")
        self.assertIn("edit-scoped candidate", definitions[0]["description"])


if __name__ == "__main__":
    unittest.main()
