"""TRANSITIONAL SHIM -- the implementation now lives in ``geometry/``.

Kept deliberately, for two reasons:

1. ``tests/test_geometry_inspection.py`` is the executable statement of the
   pre-B-rep geometry contract. Leaving it and this module untouched turns it
   into the parity proof that routing measurement through
   ``CadGeometryExtractor`` -> ``GeometryAnalyzer`` reproduces exactly what the
   old direct-measurement path produced.
2. ``Dockerfile``, ``GEOMETRY_CHECK.md``, and the docs site still name this
   file. Repointing those is a separate, mechanical change.

Remove this module once those references move to ``geometry/``. Nothing new
should import it -- import from ``geometry`` directly.
"""

from __future__ import annotations

from typing import Any

try:
    from .geometry import (
        CadGeometryExtractor,
        GeometryAnalyzer,
        GeometryExtractionError,
        empty_snapshot,
    )
    from .geometry.analyzer import (
        MAX_CENSUS_EDGES,
        MAX_PLANAR_FACES,
        MIN_VALID_VOLUME_MM3,
        SMOOTH_EDGE_TOLERANCE_DEG,
    )
    from .geometry.runtime import GEOMETRY_CHECKER_VERSION
except ImportError:  # pragma: no cover - flat layout inside the worker image
    from geometry import (
        CadGeometryExtractor,
        GeometryAnalyzer,
        GeometryExtractionError,
        empty_snapshot,
    )
    from geometry.analyzer import (
        MAX_CENSUS_EDGES,
        MAX_PLANAR_FACES,
        MIN_VALID_VOLUME_MM3,
        SMOOTH_EDGE_TOLERANCE_DEG,
    )
    from geometry.runtime import GEOMETRY_CHECKER_VERSION

__all__ = [
    "GEOMETRY_CHECKER_VERSION",
    "MAX_CENSUS_EDGES",
    "MAX_PLANAR_FACES",
    "MIN_VALID_VOLUME_MM3",
    "SMOOTH_EDGE_TOLERANCE_DEG",
    "execution_failed_geometry",
    "measure_geometry",
]

_EXTRACTOR = CadGeometryExtractor()
_ANALYZER = GeometryAnalyzer()


def measure_geometry(model: object) -> dict[str, Any]:
    """Extract a root shape from a build result and measure it."""

    try:
        extracted = _EXTRACTOR.extract(model)
    except GeometryExtractionError as exc:
        return empty_snapshot(
            execution_ok=True,
            geometry_valid=False,
            error_message=exc.message,
            solid_count=exc.solid_count,
        )
    return _ANALYZER.analyze(extracted.root)


def execution_failed_geometry(
    error_message: str,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the snapshot for a source that failed to execute at all.

    ``diagnostics`` carries structured findings when the caller already has
    them -- notably the static-safety rejection, where the full AST report
    names the rule, the function, and the line. Passing them through is the
    difference between telling the agent "something is wrong" and telling it
    what to fix; a caller with nothing structured to add omits it.
    """

    return empty_snapshot(
        execution_ok=False,
        geometry_valid=None,
        error_message=error_message,
        diagnostics=diagnostics,
    )
