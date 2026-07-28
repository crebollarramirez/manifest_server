from __future__ import annotations

import unittest
from types import SimpleNamespace

from workers.cad_editor.cad_editor.resolver import resolve_edit_target
from workers.indexer.indexer import (
    IndexGetter,
    IndexingError,
    SourceFile,
    build_project_index,
)
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


DEPENDENT_SOURCE = '''from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    holder_length_mm: float = 120.0
    holder_width_mm: float = 80.0
    leg_height_mm: float = 4.0

@cad_part(
    semantic_id="body",
    role="primary_body",
    library="cadquery",
    parameters=("holder_length_mm", "holder_width_mm"),
    depends_on=(),
    search_keys=("holder", "body"),
)
def make_body(params: ModelParams):
    return cq.Workplane("XY").box(params.holder_length_mm, params.holder_width_mm, 8)

@cad_part(
    semantic_id="fillets",
    role="finishing",
    library="cadquery",
    parameters=(),
    depends_on=("body",),
    search_keys=("fillets",),
)
def add_fillets(params: ModelParams, body):
    return body

@cad_part(
    semantic_id="legs",
    role="support_legs",
    library="cadquery",
    parameters=("leg_height_mm",),
    depends_on=("fillets",),
    search_keys=("legs",),
)
def add_legs(params: ModelParams, body):
    x_offset = params.holder_length_mm / 2
    y_offset = params.holder_width_mm / 2
    return body.translate((x_offset - x_offset, y_offset - y_offset, params.leg_height_mm))

def build_model(params: ModelParams):
    body = make_body(params)
    finished = add_fillets(params, body)
    return add_legs(params, finished)
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

    def test_dependency_graph_derives_reverse_impact_and_parameter_consumers(self):
        source = SourceFile.from_content(
            part_id="part-a",
            part_name="Soap holder",
            storage_path="project/parts/cad/part-a/model.py",
            content=DEPENDENT_SOURCE,
        )
        index = build_project_index("project", "Fixture", [source])
        getter = IndexGetter(index, [source])

        legs = getter.get_part("part-a", "legs")
        self.assertEqual(
            legs["parameter_references"],
            ["holder_length_mm", "holder_width_mm", "leg_height_mm"],
        )
        self.assertEqual(
            index["parts"][0]["metadata_warnings"][0]["missing_parameters"],
            ["holder_length_mm", "holder_width_mm"],
        )
        self.assertEqual(
            getter.get_dependency_path("part-a", "body", "legs"),
            ["body", "fillets", "legs"],
        )
        self.assertEqual(
            [
                feature["semantic_id"]
                for feature in getter.get_parameter_consumers(
                    "part-a",
                    "holder_width_mm",
                )
            ],
            ["body", "legs"],
        )

    def test_index_rejects_cycles_and_build_model_dependency_drift(self):
        cycle = DEPENDENT_SOURCE.replace(
            'depends_on=(),\n    search_keys=("holder", "body"),',
            'depends_on=("legs",),\n    search_keys=("holder", "body"),',
        )
        mismatch = DEPENDENT_SOURCE.replace(
            'depends_on=("fillets",),\n    search_keys=("legs",),',
            'depends_on=("body",),\n    search_keys=("legs",),',
        )
        for source_text, message in [
            (cycle, "acyclic"),
            (mismatch, "direct build_model dataflow"),
        ]:
            with self.assertRaisesRegex(IndexingError, message):
                build_project_index(
                    "project",
                    "Fixture",
                    [
                        SourceFile.from_content(
                            part_id="part-a",
                            part_name="Soap holder",
                            storage_path="project/model.py",
                            content=source_text,
                        )
                    ],
                )


if __name__ == "__main__":
    unittest.main()
