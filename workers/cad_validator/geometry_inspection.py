"""Deterministic geometry measurement primitives for one executed CAD model.

Reused by both the lightweight geometry-check job (``geometry_check_job.py`` /
``geometry_check_runner.py``) and, potentially, full CAD validation -- this
module only measures what geometry exists. It never inspects semantic IDs,
feature names, or any other CAD-intent metadata.

All lengths are millimeters and all volumes are cubic millimeters, matching
this repository's CadQuery/OpenCascade runtime convention (no unit scaling is
applied anywhere else in this codebase).
"""

from __future__ import annotations

from typing import Any

import cadquery as cq

GEOMETRY_CHECKER_VERSION = 1

# Below this volume, a "solid" is treated as degenerate rather than valid --
# floating-point noise from boolean operations can leave sliver volumes that
# are not zero but are not meaningful geometry either.
MIN_VALID_VOLUME_MM3 = 1e-6


def _resolve_solids(model: object) -> tuple[list, str] | None:
    """Return ``(solids, result_type)`` for a Workplane/Shape, or ``None``."""

    if isinstance(model, cq.Workplane):
        return model.solids().vals(), "cadquery.Workplane"
    if isinstance(model, cq.Shape):
        return model.Solids(), f"cadquery.{type(model).__name__}"
    return None


def measure_geometry(model: object) -> dict[str, Any]:
    """Measure one built CAD model's geometry.

    Returns a dict with ``execution_ok`` always ``True`` (the caller is
    expected to have already confirmed the model built without raising), plus
    ``geometry_valid``, ``error_message``, ``diagnostics`` (always empty here
    -- measurement locates nothing; see ``execution_failed_geometry``), and
    -- when a usable result was produced -- ``volume_mm3``, ``bounding_box``,
    ``center_of_mass``, ``solid_count``, ``face_count``, ``edge_count``.
    Measurement failure
    (wrong return type, no solids) is reported as ``geometry_valid: False``
    with every measurement field ``None``, never raised, since a build that
    returns unusable geometry is a normal, expected outcome to report on.
    """

    resolved = _resolve_solids(model)
    if resolved is None:
        return {
            "execution_ok": True,
            "geometry_valid": False,
            "error_message": (
                "build_model must return a CadQuery Workplane or Shape; "
                f"received {type(model).__name__}."
            ),
            "diagnostics": [],
            "volume_mm3": None,
            "bounding_box": None,
            "center_of_mass": None,
            "solid_count": None,
            "face_count": None,
            "edge_count": None,
        }

    solids, _result_type = resolved
    if not solids:
        return {
            "execution_ok": True,
            "geometry_valid": False,
            "error_message": "build_model returned geometry with no solids.",
            "diagnostics": [],
            "volume_mm3": None,
            "bounding_box": None,
            "center_of_mass": None,
            "solid_count": 0,
            "face_count": None,
            "edge_count": None,
        }

    compound = cq.Compound.makeCompound(solids)
    volume_mm3 = compound.Volume()
    bbox = compound.BoundingBox()
    center = compound.Center()
    solid_count = len(compound.Solids())
    face_count = len(compound.Faces())
    edge_count = len(compound.Edges())

    geometry_valid = volume_mm3 > MIN_VALID_VOLUME_MM3
    error_message = None
    if geometry_valid:
        try:
            geometry_valid = bool(compound.isValid())
        except Exception:  # pragma: no cover - defensive, OCC-version dependent
            pass
    if not geometry_valid:
        error_message = "Built geometry has degenerate or invalid solids."

    return {
        "execution_ok": True,
        "geometry_valid": geometry_valid,
        "error_message": error_message,
        "diagnostics": [],
        "volume_mm3": volume_mm3,
        "bounding_box": {
            "min": [bbox.xmin, bbox.ymin, bbox.zmin],
            "max": [bbox.xmax, bbox.ymax, bbox.zmax],
            "size": [bbox.xlen, bbox.ylen, bbox.zlen],
        },
        "center_of_mass": [center.x, center.y, center.z],
        "solid_count": solid_count,
        "face_count": face_count,
        "edge_count": edge_count,
    }


def execution_failed_geometry(
    error_message: str,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the geometry-facts dict for a source that failed to execute.

    ``diagnostics`` carries structured findings when the caller already has
    them -- notably the static-safety rejection, where the full AST report
    names the rule, the function, and the line. Passing them through is the
    difference between telling the agent "something is wrong" and telling it
    what to fix; a caller with nothing structured to add omits it.
    """

    return {
        "execution_ok": False,
        "geometry_valid": None,
        "error_message": error_message,
        "diagnostics": list(diagnostics or []),
        "volume_mm3": None,
        "bounding_box": None,
        "center_of_mass": None,
        "solid_count": None,
        "face_count": None,
        "edge_count": None,
    }
