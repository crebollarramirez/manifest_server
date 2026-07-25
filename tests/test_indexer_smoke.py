from __future__ import annotations

import unittest

from workers.indexer.indexer import IndexGetter, SourceFile, build_project_index


def cad_source(default_diameter: float) -> str:
    return f'''from dataclasses import dataclass

@dataclass(frozen=True)
class ModelParams:
    mounting_hole_diameter_mm: float = {default_diameter}

@cad_part(
    semantic_id="mount_holes",
    role="fastener_features",
    library="cadquery",
    parameters=("mounting_hole_diameter_mm",),
    depends_on=(),
    search_keys=("mounting holes", "screw holes"),
)
def cut_mounting_holes(params: ModelParams):
    """Cut the mounting holes."""
    return params.mounting_hole_diameter_mm

def build_model(params: ModelParams):
    return cut_mounting_holes(params)
'''


class IndexerSmokeTests(unittest.TestCase):
    def test_indexes_two_parts_and_retrieves_typo_match_context(self):
        sources = [
            SourceFile.from_content(
                part_id="part-a",
                part_name="Left Bracket",
                storage_path="project/parts/cad/part-a/model.py",
                content=cad_source(4.0),
            ),
            SourceFile.from_content(
                part_id="part-b",
                part_name="Right Bracket",
                storage_path="project/parts/cad/part-b/model.py",
                content=cad_source(5.0),
            ),
        ]

        index = build_project_index("project", "Fixture", sources)
        self.assertEqual(
            [
                (part["part_id"], part["cad_parts"][0]["semantic_id"])
                for part in index["parts"]
            ],
            [("part-a", "mount_holes"), ("part-b", "mount_holes")],
        )

        result = IndexGetter(index, sources).test_request("mousing holes")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["matches"][0]["semantic_id"], "mount_holes")
        self.assertIn("def cut_mounting_holes", result["context"]["source"])
        self.assertNotIn("def build_model", result["context"]["source"])


if __name__ == "__main__":
    unittest.main()
