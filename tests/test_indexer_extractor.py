from __future__ import annotations

import unittest

from workers.indexer.indexer.extractor import extract_part_index
from workers.indexer.indexer.models import IndexingError, SourceFile


# Kept in sync by hand with workers/agent_3d/tools/part/part_tools.py's
# _skeleton_source() and services/cad_agent/src/cad-actions.service.ts's
# CAD_MODEL_SKELETON_SOURCE -- this is the exact content a newly created,
# untouched CAD part should have.
EMPTY_SKELETON_SOURCE = (
    "from cadquery_runtime import cad_part, cq, dataclass\n"
    "\n"
    "@dataclass(frozen=True)\n"
    "class ModelParams:\n"
    "    pass\n"
    "\n"
    "\n"
    "def build_model(params: ModelParams):\n"
    '    return cq.Workplane("XY")\n'
)


def source(content: str) -> SourceFile:
    return SourceFile.from_content(
        part_id="11111111-1111-4111-8111-111111111111",
        part_name="Test Part",
        storage_path="project/parts/cad/part/model.py",
        content=content,
    )


class ExtractorEmptySkeletonTests(unittest.TestCase):
    def test_empty_model_params_and_zero_features_index_successfully(self):
        result = extract_part_index(source(EMPTY_SKELETON_SOURCE))

        self.assertEqual(result["model_params"], [])
        self.assertEqual(result["cad_parts"], [])
        self.assertEqual(result["build_model"]["name"], "build_model")
        self.assertEqual(result["metadata_warnings"], [])

    def test_the_create_cad_part_tool_skeleton_matches_this_fixture(self):
        from workers.agent_3d.tools.part.part_tools import _skeleton_source

        self.assertEqual(_skeleton_source(), EMPTY_SKELETON_SOURCE)

    def test_missing_build_model_still_errors(self):
        broken = EMPTY_SKELETON_SOURCE.replace(
            "def build_model(params: ModelParams):", "def not_build_model(params):"
        )

        with self.assertRaises(IndexingError):
            extract_part_index(source(broken))

    def test_malformed_decorator_field_order_still_errors(self):
        malformed = (
            "from cadquery_runtime import cad_part, cq, dataclass\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class ModelParams:\n"
            "    base_length_mm: float = 100.0\n"
            "\n"
            "@cad_part(\n"
            '    role="primary",\n'
            '    semantic_id="base",\n'
            '    library="cadquery",\n'
            '    parameters=("base_length_mm",),\n'
            "    depends_on=(),\n"
            '    search_keys=("base",),\n'
            ")\n"
            "def build_base(params: ModelParams):\n"
            '    return cq.Workplane("XY")\n'
            "\n"
            "\n"
            "def build_model(params: ModelParams):\n"
            "    return build_base(params)\n"
        )

        with self.assertRaises(IndexingError):
            extract_part_index(source(malformed))


if __name__ == "__main__":
    unittest.main()
