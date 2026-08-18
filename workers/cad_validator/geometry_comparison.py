"""TRANSITIONAL SHIM -- the implementation now lives in ``geometry/comparison.py``.

Kept for the same reasons as ``geometry_inspection.py``: it lets
``tests/test_geometry_comparison.py`` stand unchanged as the parity proof for
the comparison contract, and the Dockerfile and docs still name this file.
Remove once those references move to ``geometry/``.
"""

from __future__ import annotations

try:
    from .geometry.comparison import (
        LARGE_BBOX_CHANGE_FRACTION,
        LENGTH_TOLERANCE_MM,
        VOLUME_TOLERANCE_MM3,
        compare_geometry,
        derive_warnings,
    )
except ImportError:  # pragma: no cover - flat layout inside the worker image
    from geometry.comparison import (
        LARGE_BBOX_CHANGE_FRACTION,
        LENGTH_TOLERANCE_MM,
        VOLUME_TOLERANCE_MM3,
        compare_geometry,
        derive_warnings,
    )

__all__ = [
    "LARGE_BBOX_CHANGE_FRACTION",
    "LENGTH_TOLERANCE_MM",
    "VOLUME_TOLERANCE_MM3",
    "compare_geometry",
    "derive_warnings",
]
