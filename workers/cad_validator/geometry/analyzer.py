"""Derive a compact GeometrySnapshot from normalized root geometry.

The analyzer is the only thing in this system that reads numbers off OCCT
topology. Everything above it -- the geometry-check job, the comparison layer,
``check_geometry``, Agent3D -- consumes the dict this produces and never
touches a shape.

All lengths are millimeters and all volumes are cubic millimeters, matching
this repository's CadQuery/OpenCascade runtime convention (no unit scaling is
applied anywhere else in this codebase).

The analyzer does not call the planner, does not know about prompt context,
never modifies candidate source, and never returns raw B-rep structures --
a snapshot is a bounded summary by construction, which is what makes it safe
to hand to a language model.
"""

from __future__ import annotations

import math
from typing import Any

import cadquery as cq
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

# Below this volume, a "solid" is treated as degenerate rather than valid --
# floating-point noise from boolean operations can leave sliver volumes that
# are not zero but are not meaningful geometry either.
MIN_VALID_VOLUME_MM3 = 1e-6

# How many planar faces to report, largest first. A census exists to answer
# "what is this shape oriented like", which the big faces decide; past a
# handful the rest is noise the reader pays tokens for. `face_count` minus
# `non_planar_face_count` stays exact, so truncation is always visible.
MAX_PLANAR_FACES = 10

# Two faces meeting at less than this angle are treated as one smooth surface
# rather than an edge. A fillet replaces a sharp edge with a curved face whose
# boundaries are tangent to its neighbours, so filleting drives the sharp count
# down; this tolerance is what stops that tangency from being miscounted as a
# corner it no longer is.
SMOOTH_EDGE_TOLERANCE_DEG = 5.0

# The sharpness census visits every edge and evaluates two surface normals per
# edge. That is trivial for the parts this system builds and unbounded for a
# pathological one, so it declines rather than risking the sandbox timeout --
# which would cost the measurement that *did* succeed.
MAX_CENSUS_EDGES = 5_000

# The root geometry serialized fine but OCCT reports it as topologically
# invalid. Distinct from "the build raised" and from "no solids": there is a
# shape, it just is not sound.
GEOMETRY_INVALID = "GEOMETRY_INVALID"

# The order here is the persisted column order and the report key order. Any
# name added must also gain a `geometry_snapshots` column in the same change --
# `GeometrySnapshotStore` writes these verbatim, and PostgREST rejects the
# whole insert with PGRST204 for one that has no column behind it.
SNAPSHOT_FIELDS = (
    "execution_ok",
    "geometry_valid",
    "error_message",
    "diagnostics",
    "volume_mm3",
    "surface_area_mm2",
    "bounding_box",
    "center_of_mass",
    "solid_count",
    "face_count",
    "edge_count",
    "vertex_count",
    "planar_faces",
    "non_planar_face_count",
    "sharp_edge_count",
)


def empty_snapshot(
    *,
    execution_ok: bool,
    geometry_valid: bool | None,
    error_message: str | None,
    diagnostics: list[dict[str, Any]] | None = None,
    solid_count: int | None = None,
) -> dict[str, Any]:
    """Build a snapshot for geometry that could not be measured.

    Every measurement field is ``None`` and ``planar_faces`` is empty, because
    an empty census and "no census" are different claims -- a count of ``0``
    would assert the part has no curved faces and no corners. ``solid_count``
    is the one field a caller may fill in, since "I counted zero solids" is
    something the extractor can genuinely establish.
    """

    snapshot = dict.fromkeys(SNAPSHOT_FIELDS)
    snapshot.update(
        execution_ok=execution_ok,
        geometry_valid=geometry_valid,
        error_message=error_message,
        diagnostics=list(diagnostics or []),
        solid_count=solid_count,
        planar_faces=[],
    )
    return snapshot


def _planar_face_census(compound: Any) -> tuple[list[dict[str, Any]], int]:
    """Return the largest planar faces and a count of the non-planar ones.

    A bounding box says how far a shape reaches on each world axis and nothing
    about how it is oriented -- two parts whose support faces differ by six
    degrees have byte-identical boxes. This is the measurement that separates
    them: each planar face reported with its outward normal, its inclination
    from horizontal, its area, and where it sits.

    ``angle_from_horizontal_deg`` is derived rather than left to the reader
    because it is the form a requirement is written in ("a 65-degree viewing
    angle"), and because deriving it once here removes a place for the
    trigonometry to go wrong later. A horizontal face reports 0, a vertical
    face 90.

    Faces whose normal cannot be evaluated are skipped rather than raising --
    measurement reports on whatever geometry exists, and a face it cannot
    characterize is not a reason to lose the rest of the census.
    """

    records: list[dict[str, Any]] = []
    non_planar = 0
    for face in compound.Faces():
        try:
            if face.geomType() != "PLANE":
                non_planar += 1
                continue
            normal = face.normalAt()
            centroid = face.Center()
            area = face.Area()
        except Exception:  # pragma: no cover - defensive, OCC-version dependent
            continue
        records.append(
            {
                # `+ 0.0` normalizes OCC's signed zeros, which would otherwise
                # serialize as -0.0 and make two identical shapes compare unequal.
                "normal": [normal.x + 0.0, normal.y + 0.0, normal.z + 0.0],
                "angle_from_horizontal_deg": math.degrees(
                    math.acos(min(1.0, abs(normal.z)))
                ),
                "area_mm2": area,
                "centroid": [centroid.x + 0.0, centroid.y + 0.0, centroid.z + 0.0],
            }
        )
    records.sort(key=lambda record: record["area_mm2"], reverse=True)
    return records[:MAX_PLANAR_FACES], non_planar


def _sharp_edge_count(compound: Any) -> int | None:
    """Count edges whose two faces still meet at a corner.

    This is what makes edge treatment measurable. Filleting replaces a sharp
    edge with a curved face that meets its neighbours tangentially, so a part
    whose edges were rounded reports few sharp edges and one where the rounding
    only reached a handful reports nearly as many as before it was attempted --
    a distinction total edge count cannot draw, since filleting *raises* that
    number.

    Only edges bounded by exactly two faces are considered. A seam or a free
    boundary has no dihedral to measure, and counting it either way would say
    something about topology rather than about corners.

    Returns ``None`` when the shape has too many edges to census, which is a
    different statement from zero and is reported as such.
    """

    edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(
        compound.wrapped, TopAbs_EDGE, TopAbs_FACE, edge_faces
    )
    if edge_faces.Extent() > MAX_CENSUS_EDGES:
        return None

    smooth_limit = math.cos(math.radians(SMOOTH_EDGE_TOLERANCE_DEG))
    sharp = 0
    for index in range(1, edge_faces.Extent() + 1):
        adjacent = list(edge_faces.FindFromIndex(index))
        if len(adjacent) != 2:
            continue
        try:
            midpoint = cq.Edge(edge_faces.FindKey(index)).positionAt(0.5)
            first, second = (cq.Face(shape).normalAt(midpoint) for shape in adjacent)
            alignment = first.normalized().dot(second.normalized())
        except Exception:  # pragma: no cover - defensive, OCC-version dependent
            continue
        if alignment < smooth_limit:
            sharp += 1
    return sharp


class GeometryAnalyzer:
    """Derive one GeometrySnapshot from one normalized root shape."""

    def analyze(self, root: Any) -> dict[str, Any]:
        """Measure ``root`` and return the snapshot dict.

        Measurement runs against a compound of the root's *solids* rather than
        the root itself. A Workplane's stack can carry construction wires and
        sketch faces alongside the bodies, and counting those would make
        `face_count` and `edge_count` describe the build's scaffolding instead
        of the part. The root stays the authoritative record of everything the
        build produced; the snapshot describes the material.
        """

        solids = cq.Compound.makeCompound(root.Solids())

        volume_mm3 = solids.Volume()
        surface_area_mm2 = solids.Area()
        bbox = solids.BoundingBox()
        center = solids.Center()
        solid_count = len(solids.Solids())
        face_count = len(solids.Faces())
        edge_count = len(solids.Edges())
        vertex_count = len(solids.Vertices())
        planar_faces, non_planar_face_count = _planar_face_census(solids)
        sharp_edge_count = _sharp_edge_count(solids)

        geometry_valid = volume_mm3 > MIN_VALID_VOLUME_MM3
        error_message = None
        diagnostics: list[dict[str, Any]] = []
        if geometry_valid:
            try:
                geometry_valid = bool(solids.isValid())
            except Exception:  # pragma: no cover - defensive, OCC-version dependent
                pass
        if not geometry_valid:
            error_message = "Built geometry has degenerate or invalid solids."
            diagnostics.append(
                {
                    "error_code": GEOMETRY_INVALID,
                    "message": error_message,
                    "stage": "geometry",
                    "file_path": "model.py",
                    "function_name": "build_model",
                    "related_symbols": ["build_model"],
                }
            )

        return {
            "execution_ok": True,
            "geometry_valid": geometry_valid,
            "error_message": error_message,
            "diagnostics": diagnostics,
            "volume_mm3": volume_mm3,
            "surface_area_mm2": surface_area_mm2,
            "bounding_box": {
                "min": [bbox.xmin, bbox.ymin, bbox.zmin],
                "max": [bbox.xmax, bbox.ymax, bbox.zmax],
                "size": [bbox.xlen, bbox.ylen, bbox.zlen],
            },
            "center_of_mass": [center.x, center.y, center.z],
            "solid_count": solid_count,
            "face_count": face_count,
            "edge_count": edge_count,
            "vertex_count": vertex_count,
            "planar_faces": planar_faces,
            "non_planar_face_count": non_planar_face_count,
            "sharp_edge_count": sharp_edge_count,
        }


# The root shape existed and serialized, but measuring it raised. Distinct from
# a build failure and from invalid geometry: the geometry is there and this
# layer could not describe it.
SNAPSHOT_DERIVATION_FAILED = "SNAPSHOT_DERIVATION_FAILED"
