"""Deterministic before/after comparison of two geometry-facts dicts.

Operates purely on snapshots (as produced by ``GeometryAnalyzer.analyze`` /
``empty_snapshot``, or loaded back from a persisted ``geometry_snapshots``
row) -- never on source text, never on a shape, never on semantic IDs or any
other CAD-intent metadata. Both sides of a comparison originate from
candidate-bound native geometry, but this layer sees only the derived numbers.
All tolerances used to decide "did this actually change" are centralized here
rather than scattered across callers.
"""

from __future__ import annotations

from typing import Any

# Floating-point noise from CadQuery/OpenCascade boolean operations can leave
# sub-tolerance differences even when nothing meaningful changed. A delta
# smaller than these tolerances is reported as exactly zero/unchanged.
VOLUME_TOLERANCE_MM3 = 1e-6
LENGTH_TOLERANCE_MM = 1e-6

# A bounding-box size change larger than this fraction of the prior size (on
# any single axis) is flagged as LARGE_BOUNDING_BOX_CHANGE.
LARGE_BBOX_CHANGE_FRACTION = 0.5


def _bbox_axis_deltas(before_bbox: dict, after_bbox: dict) -> list[float]:
    return [
        abs(a - b)
        for key in ("min", "max", "size")
        for a, b in zip(after_bbox[key], before_bbox[key])
    ]


def compare_geometry(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare two geometry-facts dicts and return a GeometryDelta dict.

    Any field that cannot be computed because one side lacks a measurement
    (execution failed, or geometry was invalid) is ``None`` rather than a
    fabricated zero -- only ``validity_changed`` is always populated, since a
    validity transition is meaningful even when neither side has numeric
    geometry to compare.
    """

    delta: dict[str, Any] = {}

    volume_before = before.get("volume_mm3")
    volume_after = after.get("volume_mm3")
    if volume_before is not None and volume_after is not None:
        raw_delta = volume_after - volume_before
        volume_delta = 0.0 if abs(raw_delta) <= VOLUME_TOLERANCE_MM3 else raw_delta
        delta["volume_mm3"] = volume_delta
        delta["volume_percent"] = (
            (volume_delta / volume_before) * 100.0
            if abs(volume_before) > VOLUME_TOLERANCE_MM3
            else None
        )
    else:
        delta["volume_mm3"] = None
        delta["volume_percent"] = None

    bbox_before = before.get("bounding_box")
    bbox_after = after.get("bounding_box")
    if bbox_before is not None and bbox_after is not None:
        axis_deltas = _bbox_axis_deltas(bbox_before, bbox_after)
        delta["bbox_changed"] = any(d > LENGTH_TOLERANCE_MM for d in axis_deltas)
    else:
        delta["bbox_changed"] = None

    com_before = before.get("center_of_mass")
    com_after = after.get("center_of_mass")
    if com_before is not None and com_after is not None:
        distance = sum((a - b) ** 2 for a, b in zip(com_after, com_before)) ** 0.5
        delta["center_of_mass_distance_mm"] = (
            0.0 if distance <= LENGTH_TOLERANCE_MM else distance
        )
    else:
        delta["center_of_mass_distance_mm"] = None

    # sharp_edge_count moves opposite to edge_count under filleting -- rounding
    # an edge replaces it with a curved face bounded by two tangent ones, so
    # the total rises while the corner count falls. Reporting only the total
    # would let "I rounded four of forty-eight edges" read as progress.
    for key in ("solid_count", "face_count", "edge_count", "sharp_edge_count"):
        before_value = before.get(key)
        after_value = after.get(key)
        delta[key] = (
            after_value - before_value
            if before_value is not None and after_value is not None
            else None
        )

    delta["validity_changed"] = before.get("geometry_valid") != after.get(
        "geometry_valid"
    )

    return delta


def derive_warnings(
    before: dict[str, Any],
    after: dict[str, Any],
    delta: dict[str, Any],
) -> list[str]:
    """Derive deterministic, semantic-independent warnings from measured geometry.

    Only conditions objectively derivable from the measured facts and their
    delta are considered -- nothing here inspects semantic IDs, feature
    names, or any other CAD-intent metadata.
    """

    warnings: list[str] = []

    if after.get("execution_ok") is True and after.get("solid_count") == 0:
        warnings.append("NO_SOLIDS")

    if before.get("geometry_valid") is True and after.get("geometry_valid") is not True:
        warnings.append("GEOMETRY_BECAME_INVALID")

    if delta.get("solid_count") not in (None, 0):
        warnings.append("SOLID_COUNT_CHANGED")

    bbox_before = before.get("bounding_box")
    bbox_after = after.get("bounding_box")
    if delta.get("bbox_changed") and bbox_before is not None and bbox_after is not None:
        for before_size, after_size in zip(bbox_before["size"], bbox_after["size"]):
            denominator = max(abs(before_size), LENGTH_TOLERANCE_MM)
            if abs(after_size - before_size) / denominator > LARGE_BBOX_CHANGE_FRACTION:
                warnings.append("LARGE_BOUNDING_BOX_CHANGE")
                break

    if (
        delta.get("volume_mm3") == 0.0
        and delta.get("bbox_changed") is False
        and delta.get("solid_count") == 0
        and delta.get("face_count") == 0
        and delta.get("edge_count") == 0
        and delta.get("validity_changed") is False
    ):
        warnings.append("NO_GEOMETRIC_CHANGE")

    return warnings
