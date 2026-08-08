from __future__ import annotations

import ast
import unittest

from workers.agent_3d.tools import (
    CreateFeatureTool,
    DeleteFeatureTool,
    EditFeatureTool,
    ToolSuccess,
)
from workers.agent_3d.tools.part.part_tools import _skeleton_source

from test_cad_feature_edit_delete_unit import CANDIDATE_PATH, FakeRepository, make_context


class FeatureEditDeleteIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_create_edit_use_delete_lifecycle(self):
        """create_feature -> edit_feature (metadata, then body, then deps)
        -> create a dependent -> delete blocked while depended-on ->
        remove the dependency via edit_feature -> delete succeeds.

        Starts from CreateCadPartTool's own empty skeleton, mirroring the
        parameter-tools integration test's chain style.
        """

        repository = FakeRepository({CANDIDATE_PATH: _skeleton_source()})
        context = make_context(repository)
        original_build_model = (
            "def build_model(params: ModelParams):\n"
            '    return cq.Workplane("XY")\n'
        )

        create_result = await CreateFeatureTool().run(
            {
                "semantic_id": "cut_hole",
                "function_name": "cut_hole",
                "role": "fastener_features",
                "parameters": (),
                "dependencies": (),
                "search_keys": ("hole",),
                "docstring": "Cut a mounting hole.",
                "function_body": 'return cq.Workplane("XY").circle(2).extrude(-2)',
            },
            context,
        )
        self.assertIsInstance(create_result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        self.assertIn(original_build_model, content)

        edit_role_result = await EditFeatureTool().run(
            {"semantic_id": "cut_hole", "role": "updated_fastener_features"}, context
        )
        self.assertIsInstance(edit_role_result, ToolSuccess)
        self.assertEqual(edit_role_result.data.updated_fields, ("role",))
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn("role='updated_fastener_features'", content)
        self.assertIn(original_build_model, content)  # unrelated source untouched

        edit_body_result = await EditFeatureTool().run(
            {
                "function_name": "cut_hole",
                "function_body": 'return cq.Workplane("XY").circle(3).extrude(-2)',
            },
            context,
        )
        self.assertIsInstance(edit_body_result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn("circle(3)", content)
        self.assertIn(original_build_model, content)

        dependent_result = await CreateFeatureTool().run(
            {
                "semantic_id": "base_plate",
                "function_name": "build_base_plate",
                "role": "structural",
                "parameters": (),
                "dependencies": ({"semantic_id": "cut_hole", "argument_name": "hole"},),
                "search_keys": ("plate",),
                "docstring": "Build the base plate.",
                "function_body": 'return hole.faces(">Z").workplane().box(10, 10, 2)',
            },
            context,
        )
        self.assertIsInstance(dependent_result, ToolSuccess)

        blocked_delete = await DeleteFeatureTool().run({"semantic_id": "cut_hole"}, context)
        self.assertFalse(isinstance(blocked_delete, ToolSuccess))
        self.assertEqual(blocked_delete.error.details["reason"], "in_use")
        self.assertEqual(blocked_delete.error.details["referenced_by"], ["build_base_plate"])
        unchanged = repository.files[CANDIDATE_PATH]

        remove_dependency = await EditFeatureTool().run(
            {"semantic_id": "base_plate", "dependencies": ()}, context
        )
        self.assertIsInstance(remove_dependency, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        self.assertNotEqual(content, unchanged)
        ast.parse(content)
        self.assertIn("def build_base_plate(\n    params: ModelParams,\n):", content)

        # build_base_plate's own body still references `hole` (the removed
        # dependency argument) -- but that's a body/declared-parameter
        # concern for a *separate* edit_feature call on base_plate, not
        # something this tool needs to catch when only touching cut_hole.
        final_delete = await DeleteFeatureTool().run({"semantic_id": "cut_hole"}, context)
        self.assertIsInstance(final_delete, ToolSuccess)
        final_content = repository.files[CANDIDATE_PATH]
        ast.parse(final_content)
        self.assertNotIn("cut_hole", final_content)
        self.assertIn(original_build_model, final_content)

    async def test_delete_then_recreate_same_semantic_id(self):
        """Deleting a feature must fully free its semantic_id and function_name."""

        repository = FakeRepository({CANDIDATE_PATH: _skeleton_source()})
        context = make_context(repository)
        args = {
            "semantic_id": "cut_hole",
            "function_name": "cut_hole",
            "role": "fastener_features",
            "parameters": (),
            "dependencies": (),
            "search_keys": ("hole",),
            "docstring": "Cut a mounting hole.",
            "function_body": 'return cq.Workplane("XY").circle(2).extrude(-2)',
        }

        await CreateFeatureTool().run(args, context)
        delete_result = await DeleteFeatureTool().run({"semantic_id": "cut_hole"}, context)
        self.assertIsInstance(delete_result, ToolSuccess)

        recreate_result = await CreateFeatureTool().run(args, context)
        self.assertIsInstance(recreate_result, ToolSuccess)
        ast.parse(repository.files[CANDIDATE_PATH])

    async def test_edit_rejected_input_does_not_mutate_candidate(self):
        repository = FakeRepository({CANDIDATE_PATH: _skeleton_source()})
        context = make_context(repository)
        await CreateFeatureTool().run(
            {
                "semantic_id": "cut_hole",
                "function_name": "cut_hole",
                "role": "fastener_features",
                "parameters": (),
                "dependencies": (),
                "search_keys": ("hole",),
                "docstring": "Cut a mounting hole.",
                "function_body": 'return cq.Workplane("XY").circle(2).extrude(-2)',
            },
            context,
        )
        before = repository.files[CANDIDATE_PATH]

        result = await EditFeatureTool().run(
            {"semantic_id": "cut_hole", "function_body": "import os\nreturn base"}, context
        )

        self.assertFalse(isinstance(result, ToolSuccess))
        self.assertEqual(repository.files[CANDIDATE_PATH], before)


if __name__ == "__main__":
    unittest.main()
