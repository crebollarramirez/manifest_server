"""Normalize the result of ``build_model(params)`` into one root shape.

This is the seam between "the agent's CAD source ran" and "there is geometry to
reason about". Everything downstream -- serialization, measurement, comparison
-- operates on the single normalized root this module produces, so there is
exactly one definition of what a build produced rather than one per consumer.

The extractor persists nothing, knows nothing about the agent or its tools,
performs no comparison, and never touches CAD source. It converts a value into
a shape or explains why it could not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cadquery as cq

# The build returned something that is neither a Workplane nor a Shape, so
# there is nothing to normalize. Repairable: the agent wrote a build_model that
# returns the wrong thing.
BUILD_MODEL_RETURN_ERROR = "BUILD_MODEL_RETURN_ERROR"

# The build returned a CadQuery result that contains no solid. Sketch geometry,
# an empty Workplane, and a shell with no enclosed volume all land here.
GEOMETRY_BUILD_ERROR = "GEOMETRY_BUILD_ERROR"


class GeometryExtractionError(Exception):
    """A build result that cannot be normalized into usable root geometry.

    ``solid_count`` distinguishes "I counted the solids and there were none"
    (``0``) from "there was nothing I could count solids on" (``None``). Both
    are reported outcomes rather than crashes -- a build that returns unusable
    geometry is a normal thing for an in-progress edit to do -- and callers
    that persist measurements need the distinction, because a fabricated ``0``
    would assert something the extractor never established.
    """

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        solid_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.solid_count = solid_count


@dataclass(frozen=True)
class ExtractedGeometry:
    """One normalized root shape plus what the build actually returned."""

    root: Any
    result_type: str

    @property
    def solids(self) -> list:
        return self.root.Solids()


class CadGeometryExtractor:
    """Convert a ``build_model(...)`` return value into one root ``cq.Shape``.

    A Workplane is normalized by compounding *every* shape left on its stack,
    not by taking the first one and not by taking only its solids. A model that
    legitimately produces two disjoint bodies has both of them in the root, and
    a model whose stack also holds construction geometry keeps that too -- the
    root is meant to be the authoritative record of what the build produced,
    and dropping part of it here would make it a summary instead.

    Vectors are filtered out because a Workplane's stack holds points as well
    as shapes (``pushPoints``, ``moveTo``), and only shapes can be compounded.
    """

    def extract(self, model: object) -> ExtractedGeometry:
        if isinstance(model, cq.Workplane):
            shapes = [value for value in model.vals() if isinstance(value, cq.Shape)]
            root = cq.Compound.makeCompound(shapes)
            result_type = "cadquery.Workplane"
        elif isinstance(model, cq.Shape):
            root = model
            result_type = f"cadquery.{type(model).__name__}"
        else:
            raise GeometryExtractionError(
                BUILD_MODEL_RETURN_ERROR,
                "build_model must return a CadQuery Workplane or Shape; "
                f"received {type(model).__name__}.",
                solid_count=None,
            )

        if not root.Solids():
            raise GeometryExtractionError(
                GEOMETRY_BUILD_ERROR,
                "build_model returned geometry with no solids.",
                solid_count=0,
            )

        return ExtractedGeometry(root=root, result_type=result_type)
