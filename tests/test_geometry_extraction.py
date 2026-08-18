"""Normalizing a build result into one authoritative root shape."""

import sys
import unittest
from pathlib import Path

import cadquery as cq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers" / "cad_validator"))

from geometry import (  # noqa: E402
    BUILD_MODEL_RETURN_ERROR,
    GEOMETRY_BUILD_ERROR,
    CadGeometryExtractor,
    GeometryAnalyzer,
    GeometryExtractionError,
)
from geometry_inspection import measure_geometry  # noqa: E402

EXTRACTOR = CadGeometryExtractor()
ANALYZER = GeometryAnalyzer()


class ExtractionTests(unittest.TestCase):
    def test_a_workplane_result_normalizes_to_a_root_shape(self):
        extracted = EXTRACTOR.extract(cq.Workplane("XY").box(10, 10, 10))

        self.assertIsInstance(extracted.root, cq.Shape)
        self.assertEqual(extracted.result_type, "cadquery.Workplane")
        self.assertEqual(len(extracted.root.Solids()), 1)

    def test_a_shape_result_normalizes_to_a_root_shape(self):
        """build_model may return a Shape directly rather than a Workplane."""

        solid = cq.Workplane("XY").box(6, 6, 6).val()
        self.assertNotIsInstance(solid, cq.Workplane)

        extracted = EXTRACTOR.extract(solid)

        self.assertIs(extracted.root, solid)
        self.assertEqual(extracted.result_type, "cadquery.Solid")

    def test_multiple_solids_are_all_kept_not_just_the_first(self):
        """A model that legitimately builds two bodies has both in its root.

        Taking the first stack value would silently discard half the part and
        every measurement downstream would describe something the build did
        not produce.
        """

        model = cq.Workplane("XY").box(4, 4, 4).union(
            cq.Workplane("XY").transformed(offset=(20, 0, 0)).box(2, 2, 2)
        )

        extracted = EXTRACTOR.extract(model)

        self.assertEqual(len(extracted.root.Solids()), 2)
        self.assertEqual(ANALYZER.analyze(extracted.root)["solid_count"], 2)

    def test_a_multi_value_stack_is_compounded_rather_than_truncated(self):
        model = cq.Workplane("XY").pushPoints([(0, 0), (20, 0)]).box(4, 4, 4, combine=False)
        self.assertGreater(len(model.vals()), 1)

        extracted = EXTRACTOR.extract(model)

        self.assertEqual(len(extracted.root.Solids()), len(model.solids().vals()))

    def test_construction_points_on_the_stack_do_not_break_normalization(self):
        """A Workplane stack holds Vectors as well as Shapes.

        Compounding a stack means filtering to shapes first; handing a Vector
        to makeCompound would raise instead of reporting.
        """

        model = cq.Workplane("XY").pushPoints([(0, 0), (20, 0)]).box(4, 4, 4, combine=False)
        mixed = cq.Workplane("XY").box(4, 4, 4).add(cq.Vector(1, 2, 3))
        self.assertTrue(any(isinstance(v, cq.Vector) for v in mixed.vals()))

        self.assertEqual(len(EXTRACTOR.extract(mixed).root.Solids()), 1)
        self.assertEqual(len(EXTRACTOR.extract(model).root.Solids()), 2)

    def test_a_stack_holding_only_points_reports_no_solids_as_it_always_did(self):
        """Normalization does not start reaching up the parent chain.

        ``Workplane.findSolid()`` would walk back and find the box this stack
        descends from, but the pre-refactor path measured the stack and this one
        must too -- quietly measuring a different shape than the build left
        behind would be a behavior change wearing a refactor's clothes.
        """

        model = cq.Workplane("XY").box(4, 4, 4).faces(">Z").workplane().pushPoints([(0, 0)])
        self.assertIsNotNone(model.findSolid())

        self.assertEqual(measure_geometry(model)["solid_count"], 0)
        with self.assertRaises(GeometryExtractionError) as caught:
            EXTRACTOR.extract(model)
        self.assertEqual(caught.exception.error_code, GEOMETRY_BUILD_ERROR)


class ExtractionFailureTests(unittest.TestCase):
    def test_a_non_cadquery_result_is_a_structured_failure(self):
        with self.assertRaises(GeometryExtractionError) as caught:
            EXTRACTOR.extract("not a shape")

        self.assertEqual(caught.exception.error_code, BUILD_MODEL_RETURN_ERROR)
        self.assertIn("must return a CadQuery Workplane or Shape", caught.exception.message)
        # Nothing was counted, so nothing is claimed about the count.
        self.assertIsNone(caught.exception.solid_count)

    def test_a_result_with_no_solids_is_a_different_failure_from_a_wrong_type(self):
        """Sketch geometry built but produced no material.

        ``solid_count`` is 0 here and None above: "I counted zero solids" and
        "there was nothing to count solids on" are different claims, and the
        persisted snapshot has to be able to tell them apart.
        """

        with self.assertRaises(GeometryExtractionError) as caught:
            EXTRACTOR.extract(cq.Workplane("XY").rect(10, 10))

        self.assertEqual(caught.exception.error_code, GEOMETRY_BUILD_ERROR)
        self.assertEqual(caught.exception.solid_count, 0)

    def test_an_empty_workplane_has_no_usable_geometry(self):
        with self.assertRaises(GeometryExtractionError) as caught:
            EXTRACTOR.extract(cq.Workplane("XY"))

        self.assertEqual(caught.exception.error_code, GEOMETRY_BUILD_ERROR)

    def test_a_none_result_is_reported_rather_than_crashing(self):
        with self.assertRaises(GeometryExtractionError) as caught:
            EXTRACTOR.extract(None)

        self.assertEqual(caught.exception.error_code, BUILD_MODEL_RETURN_ERROR)
        self.assertIn("received NoneType", caught.exception.message)


class NormalizationParityTests(unittest.TestCase):
    """The new root must measure exactly as the old solids-only path did.

    This is the gate that makes the refactor a change of representation rather
    than a change of behavior. Compounding the whole stack could in principle
    pull construction wires into the face and edge counts; measuring the root's
    *solids* is what keeps the snapshot describing the part instead of the
    build's scaffolding.
    """

    CASES = {
        "box_with_boss": lambda: cq.Workplane("XY").box(10, 10, 10).faces(">Z").workplane().circle(2).extrude(3),
        "two_solids": lambda: cq.Workplane("XY").box(4, 4, 4).union(
            cq.Workplane("XY").transformed(offset=(20, 0, 0)).box(2, 2, 2)
        ),
        "filleted": lambda: cq.Workplane("XY").box(10, 10, 10).edges().fillet(1.0),
        "internal_cavity": lambda: cq.Workplane("XY").box(10, 10, 10).faces(">Z").workplane().circle(3).cutBlind(-5),
        "shape_return": lambda: cq.Workplane("XY").box(6, 6, 6).val(),
    }

    def test_extract_then_analyze_matches_the_measured_contract(self):
        for label, build in self.CASES.items():
            with self.subTest(model=label):
                model = build()
                legacy = measure_geometry(model)
                derived = ANALYZER.analyze(EXTRACTOR.extract(model).root)

                for key in (
                    "geometry_valid",
                    "solid_count",
                    "face_count",
                    "edge_count",
                    "non_planar_face_count",
                    "sharp_edge_count",
                ):
                    self.assertEqual(legacy[key], derived[key], f"{label}.{key}")
                self.assertEqual(legacy["volume_mm3"], derived["volume_mm3"], label)
                self.assertEqual(legacy["bounding_box"], derived["bounding_box"], label)
                self.assertEqual(legacy["center_of_mass"], derived["center_of_mass"], label)
                self.assertEqual(legacy["planar_faces"], derived["planar_faces"], label)


class DerivedMetricTests(unittest.TestCase):
    def test_surface_area_and_vertex_count_are_derived_from_the_root(self):
        snapshot = ANALYZER.analyze(EXTRACTOR.extract(cq.Workplane("XY").box(10, 10, 10)).root)

        self.assertAlmostEqual(snapshot["surface_area_mm2"], 600.0, places=6)
        self.assertEqual(snapshot["vertex_count"], 8)
        self.assertEqual(snapshot["face_count"], 6)
        self.assertEqual(snapshot["edge_count"], 12)

    def test_surface_area_separates_shapes_a_volume_delta_calls_unchanged(self):
        """Hollowing raises area sharply while barely moving volume."""

        solid = ANALYZER.analyze(EXTRACTOR.extract(cq.Workplane("XY").box(20, 20, 20)).root)
        hollow = ANALYZER.analyze(
            EXTRACTOR.extract(cq.Workplane("XY").box(20, 20, 20).faces(">Z").shell(-0.5)).root
        )

        self.assertLess(hollow["volume_mm3"], solid["volume_mm3"])
        self.assertGreater(hollow["surface_area_mm2"], solid["surface_area_mm2"])

    def test_invalid_geometry_is_named_by_a_structured_code(self):
        """A shape that exists but is not sound is its own failure kind."""

        from geometry import GEOMETRY_INVALID
        from geometry.analyzer import MIN_VALID_VOLUME_MM3

        sliver = cq.Workplane("XY").box(1e-4, 1e-4, 1e-4)
        snapshot = ANALYZER.analyze(EXTRACTOR.extract(sliver).root)

        self.assertLess(snapshot["volume_mm3"], MIN_VALID_VOLUME_MM3)
        self.assertFalse(snapshot["geometry_valid"])
        self.assertEqual(
            [d["error_code"] for d in snapshot["diagnostics"]], [GEOMETRY_INVALID]
        )


if __name__ == "__main__":
    unittest.main()
