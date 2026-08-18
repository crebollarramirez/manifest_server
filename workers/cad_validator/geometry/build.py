"""Turn one ``build_model(...)`` return value into geometry the system owns.

This is the sequence both CAD-validation and geometry-check runs follow, in the
sandboxed child process where the live CadQuery object exists:

    build result -> normalize -> serialize B-rep -> derive snapshot

Having one implementation is the point. Before this module, full validation and
geometry checks each measured the same build independently, with different key
sets, and neither was grounded in a persisted shape.

Serialization runs from the system side, never from ``model.py``. Agent-authored
CAD source defines geometry and nothing else -- ``cad_ast_validator`` forbids it
from calling ``exporters.export`` at all -- so a candidate cannot influence, skip,
or fake the artifact that represents it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analyzer import SNAPSHOT_DERIVATION_FAILED, GeometryAnalyzer, empty_snapshot
from .artifact import GeometryArtifactError, serialize_root
from .extraction import CadGeometryExtractor
from .runtime import runtime_provenance


@dataclass(frozen=True)
class BuiltGeometry:
    """One build's normalized root, its snapshot, and its serialized artifact.

    ``artifact`` is ``None`` when serialization failed. That is a degraded
    outcome, not a failed build: the geometry was produced and measured, it
    just did not reach durable storage, and reporting the measurement as lost
    would throw away the evidence that actually exists.
    """

    root: Any
    result_type: str
    snapshot: dict[str, Any]
    artifact: dict[str, Any] | None


def build_geometry(
    model: object,
    brep_path: Path | None,
    *,
    extractor: CadGeometryExtractor | None = None,
    analyzer: GeometryAnalyzer | None = None,
) -> BuiltGeometry:
    """Normalize, serialize, and measure one build result.

    Raises ``GeometryExtractionError`` when the result cannot be normalized at
    all; every other failure is reported inside the returned snapshot's
    ``diagnostics`` so the caller keeps whatever was successfully established.
    """

    extractor = extractor or CadGeometryExtractor()
    analyzer = analyzer or GeometryAnalyzer()

    extracted = extractor.extract(model)

    artifact: dict[str, Any] | None = None
    artifact_diagnostic: dict[str, Any] | None = None
    if brep_path is not None:
        try:
            digest, size = serialize_root(extracted.root, brep_path)
            artifact = {
                "format": "brep",
                "digest": digest,
                "bytes": size,
                "runtime": runtime_provenance(),
            }
        except GeometryArtifactError as exc:
            artifact_diagnostic = {
                "error_code": exc.error_code,
                "message": exc.message,
                "stage": "geometry",
                "file_path": "model.py",
                "related_symbols": [],
            }

    try:
        snapshot = analyzer.analyze(extracted.root)
    except Exception as exc:
        snapshot = empty_snapshot(
            execution_ok=True,
            geometry_valid=False,
            error_message=f"Built geometry could not be measured: {exc}",
            diagnostics=[
                {
                    "error_code": SNAPSHOT_DERIVATION_FAILED,
                    "message": f"Built geometry could not be measured: {exc}",
                    "stage": "geometry",
                    "file_path": "model.py",
                    "related_symbols": [],
                }
            ],
        )

    if artifact_diagnostic is not None:
        snapshot["diagnostics"] = [*snapshot.get("diagnostics", []), artifact_diagnostic]

    return BuiltGeometry(
        root=extracted.root,
        result_type=extracted.result_type,
        snapshot=snapshot,
        artifact=artifact,
    )
