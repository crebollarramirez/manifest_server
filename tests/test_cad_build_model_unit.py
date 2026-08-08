from __future__ import annotations

import ast
import unittest

from pydantic import ValidationError

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.tools import (
    EditCadBuildModelInput,
    EditCadBuildModelTool,
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

BASE_SOURCE = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    hole_diameter: float = 4.0

@cad_part(
    semantic_id='cut_hole',
    role='fastener_features',
    library="cadquery",
    parameters=('hole_diameter',),
    depends_on=(),
    search_keys=('hole',),
)
def cut_hole(params: ModelParams):
    return cq.Workplane("XY").circle(params.hole_diameter / 2).extrude(-2)

def build_model(params: ModelParams):
    return cq.Workplane("XY")
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


class EditCadBuildModelUnitTests(unittest.IsolatedAsyncioTestCase):
    def _repository(self) -> FakeRepository:
        return FakeRepository({CANDIDATE_PATH: BASE_SOURCE})

    def test_valid_input_parses(self):
        parsed = EditCadBuildModelInput.model_validate({"function_body": "return cut_hole(params)"})
        self.assertEqual(parsed.function_body, "return cut_hole(params)")

    def test_rejects_unknown_field(self):
        with self.assertRaises(ValidationError):
            EditCadBuildModelInput.model_validate({"function_body": "return 1", "extra": True})

    async def test_replaces_body(self):
        repository = self._repository()
        tool = EditCadBuildModelTool()

        result = await tool.run({"function_body": "return cut_hole(params)"}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn(
            "def build_model(params: ModelParams):\n    return cut_hole(params)\n", content
        )
        self.assertIn("def cut_hole(params: ModelParams):", content)  # unrelated source untouched

    async def test_normalizes_indentation(self):
        repository = self._repository()
        tool = EditCadBuildModelTool()

        result = await tool.run(
            {"function_body": "  \n  return cut_hole(params)\n  "}, make_context(repository)
        )

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        self.assertIn("    return cut_hole(params)\n", content)

    async def test_rejects_missing_return(self):
        tool = EditCadBuildModelTool()
        result = await tool.run({"function_body": "x = cut_hole(params)"}, make_context(self._repository()))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertEqual(result.error.details["reason"], "missing_return")

    async def test_rejects_forbidden_import(self):
        tool = EditCadBuildModelTool()
        result = await tool.run(
            {"function_body": "import os\nreturn cut_hole(params)"}, make_context(self._repository())
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    async def test_rejects_eval_and_exec(self):
        tool = EditCadBuildModelTool()
        for body in ("eval('1')\nreturn cut_hole(params)", "exec('x=1')\nreturn cut_hole(params)"):
            with self.subTest(body=body):
                result = await tool.run({"function_body": body}, make_context(self._repository()))
                self.assertIsInstance(result, ToolFailure)
                self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    async def test_rejects_provenance_markers(self):
        tool = EditCadBuildModelTool()
        result = await tool.run(
            {"function_body": "# PART-START: x\nreturn cut_hole(params)"},
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    async def test_rejects_malformed_body(self):
        tool = EditCadBuildModelTool()
        result = await tool.run({"function_body": "return (("}, make_context(self._repository()))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    async def test_missing_candidate_rejected(self):
        tool = EditCadBuildModelTool()
        result = await tool.run(
            {"function_body": "return cut_hole(params)"},
            make_context(self._repository(), candidate_id=None),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.details["reason"], "missing_candidate")

    async def test_unexpected_execution_failure_is_sanitized(self):
        tool = EditCadBuildModelTool()
        repository = ExplodingWriteRepository({CANDIDATE_PATH: BASE_SOURCE})

        result = await tool.run({"function_body": "return cut_hole(params)"}, make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_EXECUTION_FAILED")
        self.assertNotIn("internal-storage-secret", result.error.message)

    async def test_rejects_wiring_an_undefined_function(self):
        tool = EditCadBuildModelTool()
        result = await tool.run(
            {"function_body": "body = build_soap_holder_body(params)\nreturn body"},
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertEqual(result.error.details["reason"], "undefined_function_call")
        self.assertEqual(result.error.details["missing"], ["build_soap_holder_body"])

    async def test_accepts_a_call_to_an_already_defined_function(self):
        tool = EditCadBuildModelTool()

        result = await tool.run({"function_body": "return cut_hole(params)"}, make_context(self._repository()))

        self.assertIsInstance(result, ToolSuccess)

    async def test_accepts_attribute_chains_without_requiring_them_defined(self):
        tool = EditCadBuildModelTool()

        result = await tool.run(
            {"function_body": 'return cq.Workplane("XY").box(1, 1, 1)'},
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolSuccess)


class DefinedFunctionNamesTests(unittest.TestCase):
    def test_finds_top_level_functions(self):
        from workers.agent_3d.tools.build_model.build_model_tools import (
            _defined_function_names,
        )

        self.assertEqual(
            _defined_function_names(BASE_SOURCE), {"cut_hole", "build_model"}
        )

    def test_empty_skeleton_has_only_build_model(self):
        from workers.agent_3d.tools.build_model.build_model_tools import (
            _defined_function_names,
        )

        empty_skeleton = (
            "from cadquery_runtime import cad_part, cq, dataclass\n\n"
            "@dataclass(frozen=True)\n"
            "class ModelParams:\n"
            "    pass\n\n\n"
            "def build_model(params: ModelParams):\n"
            '    return cq.Workplane("XY")\n'
        )

        self.assertEqual(_defined_function_names(empty_skeleton), {"build_model"})


if __name__ == "__main__":
    unittest.main()
