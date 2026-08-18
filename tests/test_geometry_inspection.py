from __future__ import annotations

import unittest

import cadquery as cq

from workers.cad_validator.geometry_inspection import (
    MAX_PLANAR_FACES,
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


def wedge(front_top_x: float):
    """A 90 mm-wide stand body whose support face closes at ``front_top_x``.

    Reproduces the shape of a committed phone stand. The support face runs from
    the origin to ``(front_top_x, 100)``, so its inclination is
    ``atan(100 / front_top_x)`` -- and the bounding box is ``[80, 90, 100]``
    for every value of it.
    """

    return (
        cq.Workplane("XZ")
        .polyline([(0.0, 0.0), (80.0, 0.0), (80.0, 100.0), (front_top_x, 100.0)])
        .close()
        .extrude(90.0)
    )


def sloped_faces(result: dict) -> list[dict]:
    """Planar faces that are neither horizontal nor vertical."""

    return [
        face
        for face in result["planar_faces"]
        if 0.01 < face["angle_from_horizontal_deg"] < 89.99
    ]


class PlanarFaceCensusTests(unittest.TestCase):
    """Orientation is measurable, which a bounding box cannot make it.

    The motivating defect: a stand whose support face was required to sit at
    65 degrees was built at 71.6 because ``cot(65)`` was applied to the wrong
    vertex. Volume moved, the bounding box did not, and nothing else in the
    measured vocabulary could tell the two shapes apart.
    """

    def test_a_wrong_support_angle_is_visible(self):
        # 80 - cot(65)*100 = 33.37 -- the committed mistake.
        result = measure_geometry(wedge(33.37))

        self.assertAlmostEqual(
            sloped_faces(result)[0]["angle_from_horizontal_deg"], 71.55, places=1
        )

    def test_a_correct_support_angle_reports_the_angle_it_was_asked_for(self):
        # cot(65) * 100 = 46.63 -- what the requirement actually implies.
        result = measure_geometry(wedge(46.63))

        self.assertAlmostEqual(
            sloped_faces(result)[0]["angle_from_horizontal_deg"], 65.0, places=1
        )

    def test_the_two_are_indistinguishable_by_bounding_box(self):
        # The whole reason this measurement exists: the vocabulary that
        # shipped the defect cannot separate these, and the new one must.
        wrong = measure_geometry(wedge(33.37))
        right = measure_geometry(wedge(46.63))

        for axis in range(3):
            self.assertAlmostEqual(
                wrong["bounding_box"]["size"][axis],
                right["bounding_box"]["size"][axis],
                places=6,
            )
        self.assertNotAlmostEqual(
            sloped_faces(wrong)[0]["angle_from_horizontal_deg"],
            sloped_faces(right)[0]["angle_from_horizontal_deg"],
            places=1,
        )

    def test_a_box_reports_only_horizontal_and_vertical_faces(self):
        result = measure_geometry(cq.Workplane("XY").box(10, 20, 30))

        angles = sorted(
            round(face["angle_from_horizontal_deg"], 6)
            for face in result["planar_faces"]
        )
        self.assertEqual(len(result["planar_faces"]), 6)
        self.assertEqual(angles, [0.0, 0.0, 90.0, 90.0, 90.0, 90.0])
        self.assertEqual(result["non_planar_face_count"], 0)

    def test_faces_are_reported_largest_first_with_area_and_position(self):
        result = measure_geometry(cq.Workplane("XY").box(10, 20, 30))

        areas = [face["area_mm2"] for face in result["planar_faces"]]
        self.assertEqual(areas, sorted(areas, reverse=True))
        self.assertAlmostEqual(max(areas), 600.0, places=6)
        # The centroid is what tells two parallel faces apart.
        top = max(result["planar_faces"], key=lambda face: face["centroid"][2])
        self.assertAlmostEqual(top["centroid"][2], 15.0, places=6)

    def test_curved_faces_are_counted_rather_than_described(self):
        result = measure_geometry(cq.Workplane("XY").circle(5).extrude(10))

        self.assertEqual(result["non_planar_face_count"], 1)
        self.assertEqual(len(result["planar_faces"]), 2)

    def test_the_census_truncates_while_the_counts_stay_exact(self):
        model = cq.Workplane("XY").box(40, 40, 10)
        for index in range(6):
            model = model.union(
                cq.Workplane("XY").box(4, 4, 30).translate((index * 6 - 15, 0, 0))
            )

        result = measure_geometry(model)

        # Truncation is detectable: a complete list is the one whose length
        # equals face_count minus non_planar_face_count.
        self.assertEqual(len(result["planar_faces"]), MAX_PLANAR_FACES)
        self.assertGreater(
            result["face_count"] - result["non_planar_face_count"], MAX_PLANAR_FACES
        )


class SharpEdgeCensusTests(unittest.TestCase):
    """Edge treatment is measurable, which total edge count cannot make it.

    Filleting *raises* ``edge_count`` -- each rounded edge becomes a face with
    boundaries of its own -- so that number cannot say whether rounding
    happened. This one falls as corners are removed.
    """

    def test_an_unrounded_box_is_all_corners(self):
        result = measure_geometry(cq.Workplane("XY").box(10, 20, 30))

        self.assertEqual(result["edge_count"], 12)
        self.assertEqual(result["sharp_edge_count"], 12)

    def test_rounding_every_edge_leaves_no_corners(self):
        result = measure_geometry(cq.Workplane("XY").box(10, 20, 30).edges().fillet(2.0))

        self.assertEqual(result["sharp_edge_count"], 0)
        # Rounding added edges while removing corners -- the two counts move in
        # opposite directions, which is why both are reported.
        self.assertGreater(result["edge_count"], 12)

    def test_rounding_only_the_top_face_leaves_the_corners_behind(self):
        # The committed defect's exact shape: `.faces(">Z").edges().fillet(...)`
        # selects one face's edges, not the part's. Edge count rises, corners
        # do not fall, and volume barely moves.
        result = measure_geometry(
            cq.Workplane("XY").box(10, 20, 30).faces(">Z").edges().fillet(2.0)
        )

        self.assertGreater(result["edge_count"], 12)
        self.assertGreaterEqual(result["sharp_edge_count"], 8)

    def test_a_smooth_seam_is_not_a_corner(self):
        result = measure_geometry(cq.Workplane("XY").circle(5).extrude(10))

        # Three edges: two rims where the flat caps meet the wall, and the
        # cylinder's own tangent seam, which is not a corner.
        self.assertEqual(result["edge_count"], 3)
        self.assertEqual(result["sharp_edge_count"], 2)


class ShapeCensusShapeContractTests(unittest.TestCase):
    def test_every_return_path_carries_the_census_keys(self):
        """The dict shape is uniform by construction and has to stay that way.

        ``_GEOMETRY_FIELDS`` reads these names off whichever dict it is handed,
        so a path that omits one persists nothing for it and silently reports
        ``None`` on every later cache hit.
        """

        paths = {
            "measured": measure_geometry(cq.Workplane("XY").box(1, 1, 1)),
            "wrong return type": measure_geometry("not a shape"),
            "no solids": measure_geometry(cq.Workplane("XY").rect(1, 1)),
            "execution failed": execution_failed_geometry("boom"),
        }
        for label, result in paths.items():
            with self.subTest(path=label):
                self.assertIn("planar_faces", result)
                self.assertIn("non_planar_face_count", result)
                self.assertIn("sharp_edge_count", result)

    def test_unmeasurable_paths_report_absence_rather_than_zero(self):
        failed = execution_failed_geometry("build_model raised ValueError.")

        # An empty census and "no census" are different claims; a count of 0
        # would assert the part has no curved faces and no corners.
        self.assertEqual(failed["planar_faces"], [])
        self.assertIsNone(failed["non_planar_face_count"])
        self.assertIsNone(failed["sharp_edge_count"])


if __name__ == "__main__":
    unittest.main()
