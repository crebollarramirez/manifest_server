from __future__ import annotations

import unittest
from types import SimpleNamespace

from workers.cad_editor.cad_editor.resolver import resolve_edit_target
from workers.indexer.indexer import IndexGetter, SourceFile, build_project_index
from workers.indexer.indexer.repository import is_uninitialized_cad_source


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
    def test_blank_initial_cad_source_is_not_indexable_but_an_empty_index_is_valid(self):
        blank_source = "from cadquery_runtime import cad_part, cq, dataclass\n"

        self.assertTrue(is_uninitialized_cad_source(blank_source))
        self.assertFalse(is_uninitialized_cad_source(blank_source + "\n"))

        index = build_project_index("project", "Blank Fixture", [])

        self.assertEqual(index["files"], [])
        self.assertEqual(index["parts"], [])

        established = SourceFile.from_content(
            part_id="part-a",
            part_name="Bracket",
            storage_path="project/parts/cad/part-a/model.py",
            content=cad_source(4.0),
        )
        mixed_index = build_project_index(
            "project",
            "Mixed Fixture",
            [established],
        )
        self.assertEqual(
            [part["part_id"] for part in mixed_index["parts"]],
            ["part-a"],
        )

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

    def test_linked_part_is_authoritative_even_when_request_matches_another_part(self):
        left = SourceFile.from_content(
            part_id="part-a",
            part_name="Left Bracket",
            storage_path="project/parts/cad/part-a/model.py",
            content=cad_source(4.0),
        )
        right = SourceFile.from_content(
            part_id="part-b",
            part_name="Soap Tray",
            storage_path="project/parts/cad/part-b/model.py",
            content=cad_source(5.0).replace(
                '"mount_holes"',
                '"drainage_slots"',
            ).replace(
                '"mounting holes", "screw holes"',
                '"soap drainage", "drainage slots"',
            ),
        )
        getter = IndexGetter(
            build_project_index("project", "Fixture", [left, right]),
            [left, right],
        )
        agent = SimpleNamespace(
            extract_search_queries=lambda _request: SimpleNamespace(
                queries=["soap drainage"]
            )
        )

        target = resolve_edit_target(
            getter,
            agent,
            "add soap drainage",
            requested_part_id="part-a",
        )

        self.assertEqual(target.part_id, "part-a")
        self.assertEqual(target.semantic_ids, ["mount_holes"])
        self.assertTrue(
            all(match["part_id"] == "part-a" for match in target.candidates)
        )

    def test_getter_resolves_functions_lines_parameters_and_reload(self):
        source = SourceFile.from_content(
            part_id="part-a",
            part_name="Left Bracket",
            storage_path="project/parts/cad/part-a/model.py",
            content=cad_source(4.0),
        )
        index = build_project_index("project", "Fixture", [source])
        loads = []

        def loader():
            loads.append(True)
            updated = SourceFile.from_content(
                part_id="part-a",
                part_name="Left Bracket",
                storage_path=source.storage_path,
                content=cad_source(6.0),
            )
            return build_project_index("project", "Fixture", [updated]), [updated]

        getter = IndexGetter(index, [source], loader=loader)
        function = getter.get_function("part-a", "cut_mounting_holes")
        self.assertEqual(function["semantic_id"], "mount_holes")
        self.assertIn("def cut_mounting_holes", function["source"])

        function_line = source.content.splitlines().index(
            "def cut_mounting_holes(params: ModelParams):"
        ) + 1
        symbol = getter.find_symbol_at_line(source.storage_path, function_line)
        self.assertEqual(symbol["kind"], "cad_part")
        self.assertEqual(symbol["semantic_id"], "mount_holes")

        matches = getter.search_parameters("mouting hole diameter", part_id="part-a")
        self.assertEqual(matches[0]["name"], "mounting_hole_diameter_mm")
        old_hash = getter.sources["part-a"].content_hash
        self.assertIs(getter.reload(), getter)
        self.assertEqual(len(loads), 1)
        self.assertNotEqual(getter.sources["part-a"].content_hash, old_hash)


if __name__ == "__main__":
    unittest.main()
