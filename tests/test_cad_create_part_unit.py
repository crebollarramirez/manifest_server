from __future__ import annotations

import ast
import unittest
from typing import Any

from pydantic import ValidationError

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.tools import (
    CreateCadPartInput,
    CreateCadPartTool,
    ToolExecutionContext,
    ToolFailure,
    ToolServices,
    ToolSuccess,
)
from workers.agent_3d.tools.feature.feature_generation import _source_layout
from workers.agent_3d.tools.part.part_tools import _skeleton_source


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "33333333-3333-4333-8333-333333333333"


class FakeRepository:
    """In-memory storage + ``parts`` table stand-in for the strict-tool lifecycle."""

    def __init__(
        self,
        files: dict[str, str] | None = None,
        parts: list[dict[str, Any]] | None = None,
    ):
        self.files = dict(files or {})
        self.parts: list[dict[str, Any]] = [dict(part) for part in (parts or [])]
        self.deleted_part_ids: list[str] = []
        self._next_id = 1

    def read_text(self, path: str) -> str:
        try:
            return self.files[path]
        except KeyError as exc:
            raise WorkflowFailure("SOURCE_MISSING", f"{path} was not found.") from exc

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content

    def find_part_by_name(self, project_id: str, part_name: str) -> dict[str, Any] | None:
        normalized = part_name.strip().lower()
        for part in self.parts:
            if part["project_id"] == project_id and part["part_name"].strip().lower() == normalized:
                return dict(part)
        return None

    def create_part(self, project_id: str, part_name: str, part_type: str) -> dict[str, Any]:
        if self.find_part_by_name(project_id, part_name) is not None:
            raise WorkflowFailure(
                "PART_EXISTS",
                f'A part named "{part_name}" already exists in this project.',
            )
        part = {
            "id": f"part-{self._next_id}",
            "project_id": project_id,
            "part_name": part_name,
            "part_type": part_type,
        }
        self._next_id += 1
        self.parts.append(part)
        return dict(part)

    def delete_part(self, part_id: str) -> None:
        self.deleted_part_ids.append(part_id)
        self.parts = [part for part in self.parts if part["id"] != part_id]


class ExplodingWriteRepository(FakeRepository):
    def write_text(self, path: str, content: str) -> None:
        raise RuntimeError("internal-storage-secret")


def make_context(repository: FakeRepository) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        part_id="",
        candidate_id=None,
        services=ToolServices(repository=repository),
    )


class CreateCadPartUnitTests(unittest.IsolatedAsyncioTestCase):
    # 1. valid strict input parsing
    def test_valid_input_parses(self):
        parsed = CreateCadPartInput.model_validate({"part_name": "Bracket Assembly"})
        self.assertEqual(parsed.part_name, "Bracket Assembly")

    # 2. rejection of unknown fields / invalid types
    def test_rejects_unknown_field_and_invalid_type(self):
        with self.assertRaises(ValidationError):
            CreateCadPartInput.model_validate({"part_name": "Bracket", "part_type": "cad"})
        with self.assertRaises(ValidationError):
            CreateCadPartInput.model_validate({"part_name": 123})

    # 3. normalization (trimming)
    async def test_normalization_trims_part_name(self):
        tool = CreateCadPartTool()
        repository = FakeRepository()

        result = await tool.run({"part_name": "  Bracket Assembly  "}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.part_name, "Bracket Assembly")

    # 4. duplicate part_name rejection
    async def test_rejects_duplicate_part_name(self):
        tool = CreateCadPartTool()
        repository = FakeRepository(
            parts=[{"id": "part-existing", "project_id": PROJECT_ID, "part_name": "Bracket", "part_type": "cad"}]
        )

        result = await tool.run({"part_name": "bracket"}, make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertFalse(result.error.retryable)

    # 5. successful creation returns correct output fields
    async def test_successful_creation_output_fields(self):
        tool = CreateCadPartTool()
        repository = FakeRepository()

        result = await tool.run({"part_name": "Bracket Assembly"}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        data = result.data
        self.assertEqual(data.status, "created")
        self.assertEqual(data.project_id, PROJECT_ID)
        self.assertEqual(data.part_name, "Bracket Assembly")
        self.assertEqual(data.part_type, "cad")
        self.assertTrue(data.part_id)
        self.assertIn(data.part_id, data.source_path)
        self.assertTrue(data.content_hash)
        self.assertTrue(data.summary)
        self.assertIn(data.source_path, repository.files)

    # 6. generated source parses and has an empty ModelParams + valid build_model
    async def test_generated_source_is_valid_empty_skeleton(self):
        tool = CreateCadPartTool()
        repository = FakeRepository()

        result = await tool.run({"part_name": "Bracket Assembly"}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[result.data.source_path]
        tree = ast.parse(content)
        self.assertIn("class ModelParams:\n    pass", content)
        self.assertIn("def build_model(params: ModelParams):", content)
        class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        function_names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("ModelParams", class_names)
        self.assertIn("build_model", function_names)

    # 7. storage-write failure triggers rollback and sanitized failure
    async def test_write_failure_rolls_back_part_and_does_not_leak_exception(self):
        tool = CreateCadPartTool()
        repository = ExplodingWriteRepository()

        result = await tool.run({"part_name": "Bracket Assembly"}, make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_EXECUTION_FAILED")
        self.assertNotIn("internal-storage-secret", result.error.message)
        self.assertEqual(repository.parts, [])
        self.assertEqual(len(repository.deleted_part_ids), 1)

    # 8. _source_layout no longer rejects a zero-field ModelParams
    def test_source_layout_accepts_empty_model_params(self):
        source = _skeleton_source()

        _, definition_insert, names, parameters, semantic_ids = _source_layout(source)

        self.assertEqual(parameters, set())
        self.assertEqual(semantic_ids, set())
        self.assertIn("ModelParams", names)
        self.assertIn("build_model", names)
        self.assertEqual(source[definition_insert : definition_insert + 3], "def")


if __name__ == "__main__":
    unittest.main()
