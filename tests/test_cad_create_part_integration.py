from __future__ import annotations

import ast
import unittest

from workers.agent_3d.tools import (
    CreateCadPartTool,
    CreateFeatureTool,
    ToolExecutionContext,
    ToolServices,
    ToolSuccess,
)

from test_cad_create_part_unit import PROJECT_ID, RUN_ID, FakeRepository, make_context


def candidate_context(repository: FakeRepository, part_id: str, candidate_id: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        part_id=part_id,
        candidate_id=candidate_id,
        services=ToolServices(repository=repository),
    )


class CreateCadPartIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_part_row_and_source_file(self):
        repository = FakeRepository()

        result = await CreateCadPartTool().run(
            {"part_name": "Bracket Assembly"}, make_context(repository)
        )

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(len(repository.parts), 1)
        created_part = repository.parts[0]
        self.assertEqual(created_part["project_id"], PROJECT_ID)
        self.assertEqual(created_part["part_name"], "Bracket Assembly")
        self.assertEqual(created_part["part_type"], "cad")
        self.assertEqual(created_part["id"], result.data.part_id)
        self.assertIn(result.data.source_path, repository.files)

    async def test_resulting_source_parses_with_ast(self):
        repository = FakeRepository()

        result = await CreateCadPartTool().run(
            {"part_name": "Bracket Assembly"}, make_context(repository)
        )

        self.assertIsInstance(result, ToolSuccess)
        ast.parse(repository.files[result.data.source_path])

    async def test_new_part_source_immediately_unblocks_create_feature(self):
        """The whole point of this tool: create_feature must succeed against it.

        This chains CreateCadPartTool -> CreateFeatureTool, proving the empty
        skeleton (ModelParams with no fields, valid build_model) is sufficient
        for a subsequent create_feature call to add a real feature -- the
        exact scenario that was previously blocked when testing against a
        still-blank real part.
        """

        repository = FakeRepository()
        part_result = await CreateCadPartTool().run(
            {"part_name": "Bracket Assembly"}, make_context(repository)
        )
        self.assertIsInstance(part_result, ToolSuccess)
        part_id = part_result.data.part_id
        canonical_path = part_result.data.source_path

        # Candidate seeding (copying accepted source into a candidate path
        # before an edit) is a separate, already-existing concern outside
        # both tools -- reproduced here only as test setup.
        candidate_id = "candidate-1"
        candidate_path = f"{PROJECT_ID}/candidates/cad/{part_id}/{candidate_id}/model.py"
        repository.files[candidate_path] = repository.files[canonical_path]

        feature_result = await CreateFeatureTool().run(
            {
                "semantic_id": "cut_mounting_hole",
                "function_name": "cut_mounting_hole",
                "role": "fastener_features",
                "parameters": (),
                "dependencies": (),
                "search_keys": ("mounting hole",),
                "docstring": "Cut a single mounting hole.",
                "function_body": 'return cq.Workplane("XY").circle(2).extrude(-2)',
            },
            candidate_context(repository, part_id, candidate_id),
        )

        self.assertIsInstance(feature_result, ToolSuccess)
        candidate_content = repository.files[candidate_path]
        ast.parse(candidate_content)
        self.assertIn("semantic_id='cut_mounting_hole'", candidate_content)
        self.assertIn(
            "def build_model(params: ModelParams):\n    return cq.Workplane(\"XY\")\n",
            candidate_content,
        )

    async def test_duplicate_part_name_leaves_no_new_row_or_file(self):
        repository = FakeRepository()
        first = await CreateCadPartTool().run(
            {"part_name": "Bracket Assembly"}, make_context(repository)
        )
        self.assertIsInstance(first, ToolSuccess)
        file_count_before = len(repository.files)
        part_count_before = len(repository.parts)

        second = await CreateCadPartTool().run(
            {"part_name": "bracket assembly"}, make_context(repository)
        )

        self.assertFalse(isinstance(second, ToolSuccess))
        self.assertEqual(len(repository.files), file_count_before)
        self.assertEqual(len(repository.parts), part_count_before)


if __name__ == "__main__":
    unittest.main()
