from __future__ import annotations

import ast
import unittest

from pydantic import ValidationError

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.tools import (
    DeleteFeatureInput,
    DeleteFeatureTool,
    EditFeatureInput,
    EditFeatureTool,
    ToolExecutionContext,
    ToolFailure,
    ToolServices,
    ToolSuccess,
)


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PART_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
CANDIDATE_ID = "candidate-1"
CANDIDATE_PATH = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{CANDIDATE_ID}/model.py"

# cut_hole is agent-owned (PART markers) and unused anywhere.
# cut_base_plate is hand-authored (no markers) and used by build_model.
BASE_SOURCE = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    hole_diameter: float = 4.0
    plate_width: float = 20.0

# PART-START: cut_hole
@cad_part(
    semantic_id='cut_hole',
    role='fastener_features',
    library="cadquery",
    parameters=('hole_diameter',),
    depends_on=(),
    search_keys=('hole', 'mounting'),
)
def cut_hole(params: ModelParams):
    \"\"\"Cut a mounting hole.\"\"\"
    radius = params.hole_diameter / 2
    return cq.Workplane("XY").circle(radius).extrude(-2)
# PART-END: cut_hole

@cad_part(
    semantic_id='base_plate',
    role='structural_plate',
    library="cadquery",
    parameters=('plate_width',),
    depends_on=(),
    search_keys=('plate',),
)
def cut_base_plate(params: ModelParams):
    return cq.Workplane("XY").box(params.plate_width, params.plate_width, 2)

def build_model(params: ModelParams):
    return cut_base_plate(params)
"""

# cut_hole is owned and directly called by build_model.
SOURCE_CALLED_BY_BUILD_MODEL = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    hole_diameter: float = 4.0

# PART-START: cut_hole
@cad_part(
    semantic_id='cut_hole',
    role='fastener_features',
    library="cadquery",
    parameters=('hole_diameter',),
    depends_on=(),
    search_keys=('hole',),
)
def cut_hole(params: ModelParams):
    \"\"\"Cut a mounting hole.\"\"\"
    return cq.Workplane("XY").circle(params.hole_diameter / 2).extrude(-2)
# PART-END: cut_hole

def build_model(params: ModelParams):
    return cut_hole(params)
"""

# cut_hole is owned and referenced by base_plate's depends_on (not called in build_model).
SOURCE_DEPENDED_ON = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    hole_diameter: float = 4.0
    plate_width: float = 20.0

# PART-START: cut_hole
@cad_part(
    semantic_id='cut_hole',
    role='fastener_features',
    library="cadquery",
    parameters=('hole_diameter',),
    depends_on=(),
    search_keys=('hole',),
)
def cut_hole(params: ModelParams):
    \"\"\"Cut a mounting hole.\"\"\"
    return cq.Workplane("XY").circle(params.hole_diameter / 2).extrude(-2)
# PART-END: cut_hole

@cad_part(
    semantic_id='base_plate',
    role='structural_plate',
    library="cadquery",
    parameters=('plate_width',),
    depends_on=('cut_hole',),
    search_keys=('plate',),
)
def build_base_plate(
    params: ModelParams,
    hole,
):
    return cq.Workplane("XY").box(params.plate_width, params.plate_width, 2)

def build_model(params: ModelParams):
    return build_base_plate(params, None)
"""


class FakeRepository:
    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})

    def read_text(self, path: str) -> str:
        try:
            return self.files[path]
        except KeyError as exc:
            raise WorkflowFailure("SOURCE_MISSING", f"{path} was not found.") from exc

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content


class ExplodingWriteRepository(FakeRepository):
    def write_text(self, path: str, content: str) -> None:
        raise RuntimeError("internal-storage-secret")


def make_context(
    repository: FakeRepository,
    *,
    candidate_id: str | None = CANDIDATE_ID,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        part_id=PART_ID,
        candidate_id=candidate_id,
        services=ToolServices(repository=repository),
    )


class EditFeatureUnitTests(unittest.IsolatedAsyncioTestCase):
    def _repository(self, source: str = BASE_SOURCE) -> FakeRepository:
        return FakeRepository({CANDIDATE_PATH: source})

    def test_valid_input_parses_with_all_fields_optional(self):
        parsed = EditFeatureInput.model_validate({"semantic_id": "cut_hole"})
        self.assertIsNone(parsed.role)
        self.assertIsNone(parsed.function_body)

    def test_rejects_unknown_field(self):
        with self.assertRaises(ValidationError):
            EditFeatureInput.model_validate({"semantic_id": "cut_hole", "made_up": 1})

    async def test_rejects_missing_identifier(self):
        tool = EditFeatureTool()
        result = await tool.run({"role": "new_role"}, make_context(self._repository()))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "missing_identifier")

    async def test_rejects_no_edit_fields(self):
        tool = EditFeatureTool()
        result = await tool.run({"semantic_id": "cut_hole"}, make_context(self._repository()))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "no_changes")

    async def test_rejects_not_found(self):
        tool = EditFeatureTool()
        result = await tool.run(
            {"semantic_id": "does_not_exist", "role": "x"}, make_context(self._repository())
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "not_found")

    async def test_rejects_conflicting_identifiers(self):
        tool = EditFeatureTool()
        result = await tool.run(
            {"semantic_id": "cut_hole", "function_name": "cut_base_plate", "role": "x"},
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "conflicting_identifiers")

    async def test_edits_role_only_leaves_body_untouched(self):
        repository = self._repository()
        tool = EditFeatureTool()

        result = await tool.run(
            {"semantic_id": "cut_hole", "role": "updated_role"}, make_context(repository)
        )

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.updated_fields, ("role",))
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn("role='updated_role'", content)
        self.assertIn('    """Cut a mounting hole."""', content)  # docstring untouched

    async def test_edits_hand_authored_feature(self):
        repository = self._repository()
        tool = EditFeatureTool()

        result = await tool.run(
            {"function_name": "cut_base_plate", "role": "new_structural_role"},
            make_context(repository),
        )

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn("role='new_structural_role'", content)

    async def test_edits_function_body_and_checks_declared_vs_referenced(self):
        repository = self._repository()
        tool = EditFeatureTool()

        result = await tool.run(
            {
                "semantic_id": "cut_hole",
                "function_body": 'return cq.Workplane("XY").circle(params.hole_diameter).extrude(-1)',
            },
            make_context(repository),
        )

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn("extrude(-1)", content)
        self.assertIn("parameters=('hole_diameter',)", content)  # decorator untouched

    async def test_rejects_function_body_referencing_undeclared_parameter(self):
        tool = EditFeatureTool()
        result = await tool.run(
            {
                "semantic_id": "cut_hole",
                "function_body": "return cq.Workplane(\"XY\").box(params.plate_width, 1, 1)",
            },
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertIn("plate_width", result.error.details["missing_from_parameters"])

    async def test_edits_dependencies_regenerates_signature(self):
        repository = self._repository(SOURCE_DEPENDED_ON)
        tool = EditFeatureTool()

        result = await tool.run(
            {"semantic_id": "base_plate", "dependencies": ()}, make_context(repository)
        )

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn("def build_base_plate(\n    params: ModelParams,\n):", content)
        self.assertIn("depends_on=()", content)

    async def test_rejects_forbidden_function_body(self):
        tool = EditFeatureTool()
        result = await tool.run(
            {"semantic_id": "cut_hole", "function_body": "import os\nreturn base"},
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    async def test_missing_candidate_rejected(self):
        tool = EditFeatureTool()
        result = await tool.run(
            {"semantic_id": "cut_hole", "role": "x"},
            make_context(self._repository(), candidate_id=None),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "missing_candidate")

    async def test_unexpected_execution_failure_is_sanitized(self):
        tool = EditFeatureTool()
        repository = ExplodingWriteRepository({CANDIDATE_PATH: BASE_SOURCE})

        result = await tool.run(
            {"semantic_id": "cut_hole", "role": "x"}, make_context(repository)
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_EXECUTION_FAILED")
        self.assertNotIn("internal-storage-secret", result.error.message)


class DeleteFeatureUnitTests(unittest.IsolatedAsyncioTestCase):
    def _repository(self, source: str = BASE_SOURCE) -> FakeRepository:
        return FakeRepository({CANDIDATE_PATH: source})

    def test_valid_input_parses(self):
        parsed = DeleteFeatureInput.model_validate({"function_name": "cut_hole"})
        self.assertEqual(parsed.function_name, "cut_hole")

    async def test_rejects_missing_identifier(self):
        tool = DeleteFeatureTool()
        result = await tool.run({}, make_context(self._repository()))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "missing_identifier")

    async def test_rejects_not_found(self):
        tool = DeleteFeatureTool()
        result = await tool.run(
            {"semantic_id": "does_not_exist"}, make_context(self._repository())
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "not_found")

    async def test_rejects_non_owned_feature(self):
        tool = DeleteFeatureTool()
        result = await tool.run(
            {"function_name": "cut_base_plate"}, make_context(self._repository())
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "not_owned")

    async def test_rejects_feature_called_by_build_model(self):
        repository = self._repository(SOURCE_CALLED_BY_BUILD_MODEL)
        tool = DeleteFeatureTool()

        result = await tool.run({"semantic_id": "cut_hole"}, make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "in_use")
        self.assertIn("build_model", result.error.details["referenced_by"])
        self.assertEqual(repository.files[CANDIDATE_PATH], SOURCE_CALLED_BY_BUILD_MODEL)

    async def test_rejects_feature_depended_on_by_another(self):
        repository = self._repository(SOURCE_DEPENDED_ON)
        tool = DeleteFeatureTool()

        result = await tool.run({"semantic_id": "cut_hole"}, make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "in_use")
        self.assertIn("build_base_plate", result.error.details["referenced_by"])

    async def test_deletes_owned_unused_feature(self):
        repository = self._repository()
        tool = DeleteFeatureTool()

        result = await tool.run({"semantic_id": "cut_hole"}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertNotIn("cut_hole", content)
        self.assertNotIn("PART-START", content)
        self.assertIn("cut_base_plate", content)  # unrelated feature untouched
        self.assertIn(
            "def build_model(params: ModelParams):\n    return cut_base_plate(params)\n",
            content,
        )

    async def test_missing_candidate_rejected(self):
        tool = DeleteFeatureTool()
        result = await tool.run(
            {"semantic_id": "cut_hole"},
            make_context(self._repository(), candidate_id=None),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "missing_candidate")

    async def test_unexpected_execution_failure_is_sanitized(self):
        tool = DeleteFeatureTool()
        repository = ExplodingWriteRepository({CANDIDATE_PATH: BASE_SOURCE})

        result = await tool.run({"semantic_id": "cut_hole"}, make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_EXECUTION_FAILED")
        self.assertNotIn("internal-storage-secret", result.error.message)


if __name__ == "__main__":
    unittest.main()
