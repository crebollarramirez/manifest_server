from __future__ import annotations

import unittest
from typing import Any

from pydantic import ValidationError

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.tools import (
    CreateFeatureInput,
    CreateFeatureTool,
    ToolExecutionContext,
    ToolFailure,
    ToolServices,
    ToolSuccess,
)


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PART_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
CANDIDATE_ID = "candidate-1"

MODEL_SOURCE = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    hole_diameter: float = 4.0
    plate_width: float = 20.0

@cad_part(
    semantic_id='mount_holes',
    role='fastener_features',
    library='cadquery',
    parameters=('hole_diameter',),
    depends_on=(),
    search_keys=('mounting holes', 'screw holes'),
)
def cut_mounting_holes(params: ModelParams):
    return cq.Workplane("XY").circle(params.hole_diameter / 2).extrude(2)

def build_model(params: ModelParams):
    return cut_mounting_holes(params)
"""

CANDIDATE_PATH = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{CANDIDATE_ID}/model.py"


class FakeRepository:
    """Minimal in-memory ``ToolRepository`` used to exercise the tool lifecycle."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.writes: list[tuple[str, str]] = []

    def read_text(self, path: str) -> str:
        try:
            return self.files[path]
        except KeyError as exc:
            raise WorkflowFailure("SOURCE_MISSING", f"{path} was not found.") from exc

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content
        self.writes.append((path, content))


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


def valid_arguments(**overrides: Any) -> dict[str, Any]:
    """Baseline strict tool arguments.

    Container fields use actual tuples: ``StrictToolModel`` disables coercion,
    so a JSON-style list is rejected for a ``tuple[...]`` field even though it
    is semantically equivalent -- exercised explicitly in
    ``test_rejects_invalid_types``.
    """

    base: dict[str, Any] = {
        "semantic_id": "drainage_slots",
        "function_name": "cut_drainage_slots",
        "role": "drainage_features",
        "parameters": ("hole_diameter",),
        "dependencies": ({"semantic_id": "mount_holes", "argument_name": "base"},),
        "search_keys": ("drainage slots", "soap drainage"),
        "docstring": "Cut drainage slots relative to the mounting holes.",
        "function_body": (
            "radius = params.hole_diameter / 2\n"
            "return base.faces('>Z').workplane().rect(radius, radius).cutThruAll()"
        ),
    }
    base.update(overrides)
    return base


class CreateFeatureUnitTests(unittest.IsolatedAsyncioTestCase):
    def _repository(self) -> FakeRepository:
        return FakeRepository({CANDIDATE_PATH: MODEL_SOURCE})

    # 1. valid strict input parsing
    def test_valid_input_parses(self):
        parsed = CreateFeatureInput.model_validate(valid_arguments())
        self.assertEqual(parsed.semantic_id, "drainage_slots")
        self.assertEqual(parsed.dependencies[0].argument_name, "base")

    # 2. rejection of unknown fields
    def test_rejects_unknown_field(self):
        with self.assertRaises(ValidationError):
            CreateFeatureInput.model_validate(valid_arguments(library="cadquery"))

    # 3. rejection of invalid or coerced input types
    def test_rejects_invalid_types(self):
        for arguments in (
            valid_arguments(semantic_id=1),
            valid_arguments(parameters=["hole_diameter"]),  # list, not tuple
            valid_arguments(dependencies=({"semantic_id": "mount_holes"},)),  # missing field
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValidationError):
                    CreateFeatureInput.model_validate(arguments)

    # 4. normalization behavior
    async def test_normalization_trims_and_dedupes_search_keys(self):
        tool = CreateFeatureTool()
        arguments = valid_arguments(
            search_keys=(" drainage slots ", "drainage slots", "soap drainage"),
            function_body="  \n  radius = params.hole_diameter / 2\n  return base\n  ",
        )
        result = await tool.run(arguments, make_context(self._repository()))

        self.assertIsInstance(result, ToolSuccess)

    # 5 & 6. decorator generation + exact field order
    async def test_decorator_generation_and_field_order(self):
        repository = self._repository()
        tool = CreateFeatureTool()

        result = await tool.run(valid_arguments(), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        decorator_start = content.index("@cad_part(\n    semantic_id='drainage_slots'")
        decorator_block = content[decorator_start : decorator_start + 400]
        ordered_fields = ["semantic_id=", "role=", "library=", "parameters=", "depends_on=", "search_keys="]
        positions = [decorator_block.index(field) for field in ordered_fields]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('library="cadquery"', decorator_block)

    # 7. empty tuple generation
    async def test_empty_tuple_generation_for_no_dependencies(self):
        repository = self._repository()
        tool = CreateFeatureTool()
        arguments = valid_arguments(
            dependencies=(),
            function_body="radius = params.hole_diameter / 2\nreturn radius",
        )

        result = await tool.run(arguments, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertIn("depends_on=()", repository.files[CANDIDATE_PATH])

    # 8. one-item tuple comma generation
    async def test_one_item_tuple_comma(self):
        repository = self._repository()
        tool = CreateFeatureTool()

        result = await tool.run(valid_arguments(), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertIn("parameters=('hole_diameter',)", repository.files[CANDIDATE_PATH])
        self.assertIn("depends_on=('mount_holes',)", repository.files[CANDIDATE_PATH])

    # 9. multi-item tuple generation
    async def test_multi_item_tuple_generation(self):
        repository = self._repository()
        tool = CreateFeatureTool()
        arguments = valid_arguments(search_keys=("drainage slots", "soap drainage", "runoff"))

        result = await tool.run(arguments, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertIn(
            "search_keys=('drainage slots', 'soap drainage', 'runoff')",
            repository.files[CANDIDATE_PATH],
        )

    # 10. generated function signature with no dependencies
    async def test_signature_with_no_dependencies(self):
        repository = self._repository()
        tool = CreateFeatureTool()
        arguments = valid_arguments(
            dependencies=(),
            function_body="radius = params.hole_diameter / 2\nreturn radius",
        )

        result = await tool.run(arguments, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertIn(
            "def cut_drainage_slots(\n    params: ModelParams,\n):",
            repository.files[CANDIDATE_PATH],
        )

    # 11. generated function signature with one or more dependencies
    async def test_signature_with_dependencies(self):
        repository = self._repository()
        tool = CreateFeatureTool()

        result = await tool.run(valid_arguments(), make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertIn(
            "def cut_drainage_slots(\n    params: ModelParams,\n    base,\n):",
            repository.files[CANDIDATE_PATH],
        )

    # 12. duplicate semantic ID rejection
    async def test_rejects_duplicate_semantic_id(self):
        tool = CreateFeatureTool()
        result = await tool.run(
            valid_arguments(semantic_id="mount_holes"),
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 13. duplicate function-name rejection
    async def test_rejects_duplicate_function_name(self):
        tool = CreateFeatureTool()
        result = await tool.run(
            valid_arguments(function_name="cut_mounting_holes"),
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 14. missing parameter rejection
    async def test_rejects_unknown_parameter(self):
        tool = CreateFeatureTool()
        result = await tool.run(
            valid_arguments(
                parameters=("not_a_real_parameter",),
                function_body=(
                    "value = params.not_a_real_parameter\n"
                    "return base.val(value)"
                ),
            ),
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 15. missing dependency rejection
    async def test_rejects_unknown_dependency(self):
        tool = CreateFeatureTool()
        result = await tool.run(
            valid_arguments(
                dependencies=({"semantic_id": "unknown_feature", "argument_name": "base"},)
            ),
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 16. invalid semantic ID rejection
    async def test_rejects_invalid_semantic_id(self):
        tool = CreateFeatureTool()
        result = await tool.run(
            valid_arguments(semantic_id="Drainage-Slots"),
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 17. invalid function-name rejection
    async def test_rejects_invalid_function_name(self):
        tool = CreateFeatureTool()
        for function_name in ("_cut_drainage_slots", "drainageSlots", "slots"):
            with self.subTest(function_name=function_name):
                result = await tool.run(
                    valid_arguments(function_name=function_name),
                    make_context(self._repository()),
                )
                self.assertIsInstance(result, ToolFailure)
                self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 18. duplicate parameter or dependency rejection
    async def test_rejects_duplicate_parameters_and_dependencies(self):
        tool = CreateFeatureTool()

        dup_params = await tool.run(
            valid_arguments(parameters=("hole_diameter", "hole_diameter")),
            make_context(self._repository()),
        )
        self.assertIsInstance(dup_params, ToolFailure)

        dup_deps = await tool.run(
            valid_arguments(
                dependencies=(
                    {"semantic_id": "mount_holes", "argument_name": "base"},
                    {"semantic_id": "mount_holes", "argument_name": "other"},
                )
            ),
            make_context(self._repository()),
        )
        self.assertIsInstance(dup_deps, ToolFailure)

        ok = await tool.run(
            valid_arguments(
                dependencies=(
                    {"semantic_id": "mount_holes", "argument_name": "base"},
                ),
                parameters=(),
                function_body="return base",
            ),
            make_context(self._repository()),
        )
        self.assertIsInstance(ok, ToolSuccess)

    # 19. malformed function-body rejection
    async def test_rejects_malformed_function_body(self):
        tool = CreateFeatureTool()
        result = await tool.run(
            valid_arguments(function_body="return (("),
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 20. missing return rejection
    async def test_rejects_missing_return(self):
        tool = CreateFeatureTool()
        result = await tool.run(
            valid_arguments(function_body="radius = params.hole_diameter / 2"),
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 21. forbidden import rejection
    async def test_rejects_forbidden_import(self):
        tool = CreateFeatureTool()
        result = await tool.run(
            valid_arguments(
                function_body="import os\nreturn base",
            ),
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 22. eval, exec, dynamic import, and I/O rejection
    async def test_rejects_eval_exec_dynamic_import_and_io(self):
        tool = CreateFeatureTool()
        bodies = [
            "eval('1 + 1')\nreturn base",
            "exec('x = 1')\nreturn base",
            "__import__('os')\nreturn base",
            "f = open('x.txt')\nreturn base",
        ]
        for body in bodies:
            with self.subTest(body=body):
                result = await tool.run(
                    valid_arguments(function_body=body),
                    make_context(self._repository()),
                )
                self.assertIsInstance(result, ToolFailure)
                self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 23. declared-versus-referenced parameter mismatch
    async def test_rejects_parameter_mismatch(self):
        tool = CreateFeatureTool()
        result = await tool.run(
            valid_arguments(
                parameters=(),
                function_body=(
                    "radius = params.hole_diameter / 2\n"
                    "return base.val(radius)"
                ),
            ),
            make_context(self._repository()),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    # 24. output-model validation
    async def test_output_model_fields(self):
        tool = CreateFeatureTool()
        result = await tool.run(valid_arguments(), make_context(self._repository()))

        self.assertIsInstance(result, ToolSuccess)
        data = result.data
        self.assertEqual(data.status, "created")
        self.assertEqual(data.semantic_id, "drainage_slots")
        self.assertEqual(data.function_name, "cut_drainage_slots")
        self.assertEqual(data.candidate_id, CANDIDATE_ID)
        self.assertEqual(data.source_path, CANDIDATE_PATH)
        self.assertEqual(data.parameters, ("hole_diameter",))
        self.assertEqual(data.dependency_semantic_ids, ("mount_holes",))
        self.assertTrue(data.requires_assembly_wiring)
        self.assertTrue(data.summary)

    # 25. expected failures returned through the standard tool failure envelope
    async def test_missing_candidate_returns_standard_failure_envelope(self):
        tool = CreateFeatureTool()
        result = await tool.run(
            valid_arguments(),
            make_context(self._repository(), candidate_id=None),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertFalse(result.error.retryable)

    # 26. unexpected execution failures do not leak raw exceptions
    async def test_unexpected_execution_failure_is_sanitized(self):
        tool = CreateFeatureTool()
        repository = ExplodingWriteRepository({CANDIDATE_PATH: MODEL_SOURCE})

        result = await tool.run(valid_arguments(), make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_EXECUTION_FAILED")
        self.assertNotIn("internal-storage-secret", result.error.message)


if __name__ == "__main__":
    unittest.main()
