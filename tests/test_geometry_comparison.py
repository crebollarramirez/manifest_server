from __future__ import annotations

import unittest

import cadquery as cq

from workers.cad_validator.geometry_comparison import compare_geometry, derive_warnings
from workers.cad_validator.geometry_inspection import (
    execution_failed_geometry,
    measure_geometry,
)


def box(cx: float, cy: float, cz: float, w: float, h: float, d: float) -> dict:
    return measure_geometry(cq.Workplane("XY").center(cx, cy).box(w, h, d))


class CompareGeometryTests(unittest.TestCase):
    def test_identical_snapshots_produce_approximately_zero_deltas(self):
        before = box(0, 0, 0, 10, 10, 10)
        after = box(0, 0, 0, 10, 10, 10)

        delta = compare_geometry(before, after)

        self.assertEqual(delta["volume_mm3"], 0.0)
        self.assertFalse(delta["bbox_changed"])
        self.assertEqual(delta["center_of_mass_distance_mm"], 0.0)
        self.assertEqual(delta["solid_count"], 0)
        self.assertEqual(delta["face_count"], 0)
        self.assertEqual(delta["edge_count"], 0)
        self.assertFalse(delta["validity_changed"])

    def test_additive_change_produces_a_positive_volume_delta(self):
        before = box(0, 0, 0, 10, 10, 10)
        after = box(0, 0, 0, 20, 10, 10)

        delta = compare_geometry(before, after)

        self.assertGreater(delta["volume_mm3"], 0)
        self.assertAlmostEqual(delta["volume_mm3"], 1000.0, places=6)

    def test_subtractive_change_produces_a_negative_volume_delta(self):
        before = box(0, 0, 0, 20, 10, 10)
        after = box(0, 0, 0, 10, 10, 10)

        delta = compare_geometry(before, after)

        self.assertLess(delta["volume_mm3"], 0)
        self.assertAlmostEqual(delta["volume_mm3"], -1000.0, places=6)

    def test_internal_cavity_changes_volume_and_topology_but_not_outer_bbox(self):
        outer = measure_geometry(cq.Workplane("XY").box(20, 20, 20))
        with_cavity = measure_geometry(
            cq.Workplane("XY").box(20, 20, 20).faces(">Z").workplane().hole(5)
        )

        delta = compare_geometry(outer, with_cavity)

        self.assertFalse(delta["bbox_changed"])
        self.assertLess(delta["volume_mm3"], 0)
        self.assertNotEqual(delta["face_count"], 0)

    def test_translation_changes_bbox_position_and_com_with_unchanged_size(self):
        before = box(0, 0, 0, 10, 10, 10)
        after = box(50, 0, 0, 10, 10, 10)

        delta = compare_geometry(before, after)

        self.assertTrue(delta["bbox_changed"])
        self.assertAlmostEqual(delta["center_of_mass_distance_mm"], 50.0, places=6)
        # size is unchanged even though position moved -- bbox_changed must
        # not be conflated with a size change.
        self.assertEqual(before["bounding_box"]["size"], after["bounding_box"]["size"])

    def test_solid_count_changes_are_detected(self):
        one = box(0, 0, 0, 10, 10, 10)
        two_solids = cq.Compound.makeCompound(
            cq.Workplane("XY").box(10, 10, 10).solids().vals()
            + cq.Workplane("XY").center(50, 0).box(10, 10, 10).solids().vals()
        )
        two = measure_geometry(two_solids)

        delta = compare_geometry(one, two)

        self.assertEqual(delta["solid_count"], 1)
        self.assertIn("SOLID_COUNT_CHANGED", derive_warnings(one, two, delta))

    def test_validity_changes_are_detected(self):
        valid = box(0, 0, 0, 10, 10, 10)
        became_invalid = measure_geometry(cq.Workplane("XY"))

        delta = compare_geometry(valid, became_invalid)

        self.assertTrue(delta["validity_changed"])
        self.assertIn("GEOMETRY_BECAME_INVALID", derive_warnings(valid, became_invalid, delta))
        self.assertIn("NO_SOLIDS", derive_warnings(valid, became_invalid, delta))

    def test_execution_failure_is_distinguished_from_invalid_geometry_in_delta(self):
        valid = box(0, 0, 0, 10, 10, 10)
        failed = execution_failed_geometry("boom")

        delta = compare_geometry(valid, failed)

        self.assertIsNone(delta["volume_mm3"])
        self.assertIsNone(delta["bbox_changed"])
        self.assertTrue(delta["validity_changed"])

    def test_floating_point_noise_below_tolerance_reports_as_zero(self):
        before = box(0, 0, 0, 10, 10, 10)
        after = dict(before)
        after["volume_mm3"] = before["volume_mm3"] + 1e-9

        delta = compare_geometry(before, after)

        self.assertEqual(delta["volume_mm3"], 0.0)

    def test_no_geometric_change_warning_only_fires_when_nothing_changed(self):
        before = box(0, 0, 0, 10, 10, 10)
        same = box(0, 0, 0, 10, 10, 10)
        changed = box(0, 0, 0, 20, 10, 10)

        same_delta = compare_geometry(before, same)
        changed_delta = compare_geometry(before, changed)

        self.assertIn("NO_GEOMETRIC_CHANGE", derive_warnings(before, same, same_delta))
        self.assertNotIn(
            "NO_GEOMETRIC_CHANGE", derive_warnings(before, changed, changed_delta)
        )

    def test_large_bounding_box_change_warning(self):
        before = box(0, 0, 0, 10, 10, 10)
        after = box(0, 0, 0, 100, 10, 10)

        delta = compare_geometry(before, after)

        self.assertIn("LARGE_BOUNDING_BOX_CHANGE", derive_warnings(before, after, delta))

    def test_no_previous_state_produces_no_delta_fields_when_missing(self):
        # Simulates the "no predecessor" scenario at the delta layer: a caller
        # with no previous snapshot should never call compare_geometry at all
        # (job/tool layers return delta=None), but if a previous state lacks
        # measurements this must not fabricate zeros.
        no_measurements = execution_failed_geometry("no prior source")
        current = box(0, 0, 0, 10, 10, 10)

        delta = compare_geometry(no_measurements, current)

        self.assertIsNone(delta["volume_mm3"])
        self.assertIsNone(delta["solid_count"])
        self.assertIsNone(delta["sharp_edge_count"])

    def test_rounding_edges_moves_the_two_edge_counts_in_opposite_directions(self):
        # Why sharp_edge_count is reported alongside edge_count rather than
        # instead of it: rounding removes corners by adding faces, so the total
        # rises exactly when the corner count falls. A step that read only the
        # total would see filleting as if it had added geometry.
        before = measure_geometry(cq.Workplane("XY").box(10, 20, 30))
        after = measure_geometry(cq.Workplane("XY").box(10, 20, 30).edges().fillet(2.0))

        delta = compare_geometry(before, after)

        self.assertGreater(delta["edge_count"], 0)
        self.assertEqual(delta["sharp_edge_count"], -12)

    def test_rounding_that_missed_its_edges_reports_no_corner_change(self):
        # `.faces(">Z").edges()` selects one face's edges rather than the
        # part's. The volume barely moves and the total edge count rises, so
        # this delta is the only one that says the rounding did not land.
        before = measure_geometry(cq.Workplane("XY").box(10, 20, 30))
        after = measure_geometry(
            cq.Workplane("XY").box(10, 20, 30).faces(">Z").edges().fillet(2.0)
        )

        delta = compare_geometry(before, after)

        self.assertGreater(delta["edge_count"], 0)
        self.assertEqual(delta["sharp_edge_count"], 0)


if __name__ == "__main__":
    unittest.main()
