from __future__ import annotations

import ast
import unittest

from workers.agent_3d.tools import CreateFeatureTool, EditCadBuildModelTool, ToolSuccess
from workers.agent_3d.tools.part.part_tools import _skeleton_source

from test_cad_build_model_unit import CANDIDATE_PATH, FakeRepository, make_context


class EditCadBuildModelIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_cad_part_then_create_feature_then_wire_into_build_model(self):
        """The whole point of this tool: it's the missing wiring step.

        create_feature explicitly never touches build_model -- this chains
        create_cad_part's empty skeleton -> create_feature -> a real
        edit_cad_build_model call, proving that's genuinely the only way to
        make a newly created feature affect the final model.
        """

        repository = FakeRepository({CANDIDATE_PATH: _skeleton_source()})
        context = make_context(repository)

        feature_result = await CreateFeatureTool().run(
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
        self.assertIsInstance(feature_result, ToolSuccess)
        self.assertTrue(feature_result.data.requires_assembly_wiring)
        content_before_wiring = repository.files[CANDIDATE_PATH]
        self.assertIn(
            'def build_model(params: ModelParams):\n    return cq.Workplane("XY")\n',
            content_before_wiring,
        )

        wire_result = await EditCadBuildModelTool().run(
            {"function_body": "return cut_hole(params)"}, context
        )

        self.assertIsInstance(wire_result, ToolSuccess)
        final_content = repository.files[CANDIDATE_PATH]
        ast.parse(final_content)
        self.assertIn(
            "def build_model(params: ModelParams):\n    return cut_hole(params)\n", final_content
        )
        # The feature itself is completely untouched by the wiring step.
        self.assertIn("def cut_hole(\n    params: ModelParams,\n):", final_content)
        self.assertIn("# PART-START: cut_hole", final_content)

    async def test_rejected_wiring_leaves_candidate_unchanged(self):
        repository = FakeRepository({CANDIDATE_PATH: _skeleton_source()})
        context = make_context(repository)
        before = repository.files[CANDIDATE_PATH]

        result = await EditCadBuildModelTool().run({"function_body": "x = 1"}, context)

        self.assertFalse(isinstance(result, ToolSuccess))
        self.assertEqual(repository.files[CANDIDATE_PATH], before)


if __name__ == "__main__":
    unittest.main()
