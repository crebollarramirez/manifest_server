"""Version identity for the geometry layer and the runtime that produced it."""

from __future__ import annotations

from typing import Any

# Bumped to 2 when the planar-face census and sharp-edge count were added.
# Bumped to 3 when snapshots became derived observations of a persisted B-rep
# artifact and gained `surface_area_mm2` / `vertex_count`.
#
# `geometry_snapshots` and `geometry_artifacts` are both keyed on
# (source_sha256, geometry_checker_version), so bumping this retires every
# cached row measured under an older, smaller vocabulary rather than letting
# one be served as if it answered the same questions.
GEOMETRY_CHECKER_VERSION = 3


def runtime_provenance() -> dict[str, Any]:
    """Describe the CAD runtime that produced an artifact.

    Recorded alongside each artifact because a B-rep file is only meaningful
    with respect to the kernel that wrote it: an artifact produced by a
    different OCCT may not reload identically, and knowing which one wrote it
    is the difference between diagnosing that and guessing at it.

    Every field is best-effort. A runtime that does not expose its version is
    a reason to record less, not a reason to fail a build that succeeded.
    """

    provenance: dict[str, Any] = {
        "geometry_checker_version": GEOMETRY_CHECKER_VERSION
    }
    try:
        import cadquery as cq

        provenance["cadquery"] = str(cq.__version__)
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        import OCP

        provenance["occt"] = str(OCP.__version__)
    except Exception:  # pragma: no cover - defensive
        pass
    return provenance
