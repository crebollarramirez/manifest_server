from __future__ import annotations

import unittest

import cadquery as cq

from workers.cad_validator.geometry_inspection import (
    execution_failed_geometry,
    measure_geometry,
)


class MeasureGeometryTests(unittest.TestCase):
    def test_rectangular_solid_solid_count(self):
        model = cq.Workplane("XY").box(10, 20, 5)

        result = measure_geometry(model)

        self.assertTrue(result["execution_ok"])
        self.assertTrue(result["geometry_valid"])
        self.assertEqual(result["solid_count"], 1)

    def test_bounding_box_size_and_position(self):
        model = cq.Workplane("XY").center(30, -10).box(10, 20, 5)

        result = measure_geometry(model)

        bbox = result["bounding_box"]
        self.assertAlmostEqual(bbox["min"][0], 25.0, places=6)
        self.assertAlmostEqual(bbox["max"][0], 35.0, places=6)
        self.assertAlmostEqual(bbox["min"][1], -20.0, places=6)
        self.assertAlmostEqual(bbox["max"][1], 0.0, places=6)
        self.assertAlmostEqual(bbox["size"][0], 10.0, places=6)
        self.assertAlmostEqual(bbox["size"][1], 20.0, places=6)
        self.assertAlmostEqual(bbox["size"][2], 5.0, places=6)

    def test_volume_within_tolerance(self):
        model = cq.Workplane("XY").box(10, 20, 5)

        result = measure_geometry(model)

        self.assertAlmostEqual(result["volume_mm3"], 1000.0, places=6)

    def test_center_of_mass_within_tolerance(self):
        model = cq.Workplane("XY").center(30, -10).box(10, 20, 5)

        result = measure_geometry(model)

        center = result["center_of_mass"]
        self.assertAlmostEqual(center[0], 30.0, places=6)
        self.assertAlmostEqual(center[1], -10.0, places=6)
        self.assertAlmostEqual(center[2], 0.0, places=6)

    def test_face_and_edge_counts_for_a_box(self):
        model = cq.Workplane("XY").box(10, 20, 5)

        result = measure_geometry(model)

        self.assertEqual(result["face_count"], 6)
        self.assertEqual(result["edge_count"], 12)

    def test_geometry_validity_is_reported(self):
        valid = measure_geometry(cq.Workplane("XY").box(10, 10, 10))
        empty = measure_geometry(cq.Workplane("XY"))

        self.assertTrue(valid["geometry_valid"])
        self.assertFalse(empty["geometry_valid"])
        self.assertEqual(empty["solid_count"], 0)

    def test_wrong_return_type_is_reported_as_invalid_not_raised(self):
        result = measure_geometry("not-a-shape")

        self.assertTrue(result["execution_ok"])
        self.assertFalse(result["geometry_valid"])
        self.assertIsNotNone(result["error_message"])

    def test_shape_return_type_is_also_supported(self):
        shape = cq.Workplane("XY").box(10, 10, 10).val()

        result = measure_geometry(shape)

        self.assertTrue(result["geometry_valid"])
        self.assertEqual(result["solid_count"], 1)

    def test_multi_solid_aggregates_volume_and_bbox(self):
        solids = (
            cq.Workplane("XY").box(10, 10, 10).solids().vals()
            + cq.Workplane("XY").center(50, 0).box(10, 10, 10).solids().vals()
        )
        compound = cq.Compound.makeCompound(solids)

        result = measure_geometry(compound)

        self.assertEqual(result["solid_count"], 2)
        self.assertAlmostEqual(result["volume_mm3"], 2000.0, places=6)
        self.assertAlmostEqual(result["bounding_box"]["min"][0], -5.0, places=6)
        self.assertAlmostEqual(result["bounding_box"]["max"][0], 55.0, places=6)

    def test_execution_failed_geometry_distinguishes_execution_from_validity(self):
        failed = execution_failed_geometry("build_model raised ValueError.")

        self.assertFalse(failed["execution_ok"])
        self.assertIsNone(failed["geometry_valid"])
        self.assertIsNone(failed["volume_mm3"])
        self.assertEqual(failed["error_message"], "build_model raised ValueError.")


if __name__ == "__main__":
    unittest.main()
