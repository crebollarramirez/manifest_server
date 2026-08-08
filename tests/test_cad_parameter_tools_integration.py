from __future__ import annotations

import ast
import unittest

from workers.agent_3d.tools import (
    CreateFeatureTool,
    CreateParameterTool,
    DeleteParameterTool,
    EditParameterTool,
    ToolExecutionContext,
    ToolServices,
    ToolSuccess,
)
from workers.agent_3d.tools.part.part_tools import _skeleton_source

from test_cad_parameter_tools_unit import CANDIDATE_PATH, FakeRepository, make_context


class ParameterToolsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_create_edit_use_delete_lifecycle(self):
        """create_parameter -> edit_parameter -> create_feature -> delete_parameter.

        Starts from a fresh CreateCadPartTool-style empty skeleton (proving
        the parameter tools work immediately on a brand-new part), and
        confirms the source stays valid and ModelParams reflects the right
        field set at every step.
        """

        repository = FakeRepository({CANDIDATE_PATH: _skeleton_source()})
        context = make_context(repository)
        original_build_model = (
            "def build_model(params: ModelParams):\n"
            '    return cq.Workplane("XY")\n'
        )
        self.assertIn(original_build_model, repository.files[CANDIDATE_PATH])

        create_result = await CreateParameterTool().run(
            {"parameter_name": "plate_width", "value": 20.0}, context
        )
        self.assertIsInstance(create_result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn("plate_width: float = 20.0", content)
        self.assertIn(original_build_model, content)  # unrelated source untouched

        edit_result = await EditParameterTool().run(
            {"parameter_name": "plate_width", "value": 25.0}, context
        )
        self.assertIsInstance(edit_result, ToolSuccess)
        self.assertEqual(edit_result.data.previous_value, 20.0)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn("plate_width: float = 25.0", content)
        self.assertNotIn("plate_width: float = 20.0", content)
        self.assertIn(original_build_model, content)

        feature_result = await CreateFeatureTool().run(
            {
                "semantic_id": "build_base_plate",
                "function_name": "build_base_plate",
                "role": "structural",
                "parameters": ("plate_width",),
                "dependencies": (),
                "search_keys": ("base plate",),
                "docstring": "Build the base plate.",
                "function_body": (
                    'return cq.Workplane("XY").box(params.plate_width, params.plate_width, 2)'
                ),
            },
            context,
        )
        self.assertIsInstance(feature_result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn(original_build_model, content)  # build_model still untouched by feature creation

        # Deleting while still referenced by the new feature must be rejected.
        blocked_delete = await DeleteParameterTool().run(
            {"parameter_name": "plate_width"}, context
        )
        self.assertFalse(isinstance(blocked_delete, ToolSuccess))
        self.assertEqual(blocked_delete.error.details["referenced_by"], ["build_base_plate"])
        unchanged_after_block = repository.files[CANDIDATE_PATH]
        self.assertEqual(unchanged_after_block, content)

        # Remove the feature's usage manually (a separate, not-yet-built
        # "delete_feature" tool's job) so the parameter becomes deletable.
        content_without_feature = content.replace(
            content[content.index("@cad_part(\n    semantic_id='build_base_plate'") : content.index(
                "def build_model"
            )],
            "",
        )
        repository.files[CANDIDATE_PATH] = content_without_feature
        ast.parse(repository.files[CANDIDATE_PATH])

        delete_result = await DeleteParameterTool().run(
            {"parameter_name": "plate_width"}, context
        )
        self.assertIsInstance(delete_result, ToolSuccess)
        self.assertEqual(delete_result.data.removed_value, 25.0)
        final_content = repository.files[CANDIDATE_PATH]
        ast.parse(final_content)
        self.assertIn("class ModelParams:\n    pass\n", final_content)
        self.assertIn(original_build_model, final_content)  # unrelated source untouched throughout

    async def test_create_then_immediate_delete_while_referenced_is_rejected_and_unchanged(self):
        repository = FakeRepository({CANDIDATE_PATH: _skeleton_source()})
        context = make_context(repository)

        await CreateParameterTool().run({"parameter_name": "hole_diameter", "value": 4.0}, context)
        await CreateFeatureTool().run(
            {
                "semantic_id": "cut_hole",
                "function_name": "cut_hole",
                "role": "fastener",
                "parameters": ("hole_diameter",),
                "dependencies": (),
                "search_keys": ("hole",),
                "docstring": "Cut a hole.",
                "function_body": (
                    'return cq.Workplane("XY").circle(params.hole_diameter / 2).extrude(-2)'
                ),
            },
            context,
        )
        before = repository.files[CANDIDATE_PATH]

        result = await DeleteParameterTool().run({"parameter_name": "hole_diameter"}, context)

        self.assertFalse(isinstance(result, ToolSuccess))
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertEqual(result.error.details["reason"], "in_use")
        self.assertEqual(repository.files[CANDIDATE_PATH], before)

    async def test_create_then_delete_unused_leaves_source_otherwise_identical(self):
        repository = FakeRepository({CANDIDATE_PATH: _skeleton_source()})
        context = make_context(repository)
        original = repository.files[CANDIDATE_PATH]

        await CreateParameterTool().run({"parameter_name": "wall_height", "value": 10.0}, context)
        result = await DeleteParameterTool().run({"parameter_name": "wall_height"}, context)

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(repository.files[CANDIDATE_PATH], original)


if __name__ == "__main__":
    unittest.main()
