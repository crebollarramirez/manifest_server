"""The geometry layer: B-rep extraction, persistence, measurement, comparison.

See ``README.md`` in this directory for the architecture. In short:

    model.py            canonical, reproducible design source
    B-rep artifact      authoritative geometry of one built candidate
    GeometrySnapshot    compact derived observations of that artifact

Modules here own everything below the geometry-check and CAD-validation jobs.
Nothing in this package knows about the agent, its tools, its prompts, or the
semantic index.
"""

from __future__ import annotations

from .analyzer import (
    GEOMETRY_INVALID,
    SNAPSHOT_DERIVATION_FAILED,
    SNAPSHOT_FIELDS,
    GeometryAnalyzer,
    empty_snapshot,
)
from .artifact import (
    BREP_EXPORT_FAILED,
    GEOMETRY_ARTIFACT_UNAVAILABLE,
    GeometryArtifact,
    GeometryArtifactError,
    artifact_storage_path,
)
from .artifact_store import GeometryArtifactStore
from .build import BuiltGeometry, build_geometry
from .engine import CandidateSourceRef, GeometryEngine, GeometryResult
from .comparison import compare_geometry, derive_warnings
from .extraction import (
    BUILD_MODEL_RETURN_ERROR,
    GEOMETRY_BUILD_ERROR,
    CadGeometryExtractor,
    ExtractedGeometry,
    GeometryExtractionError,
)
from .runtime import GEOMETRY_CHECKER_VERSION, runtime_provenance
from .snapshot_store import GeometrySnapshotStore, snapshot_from_row
from .storage import BUCKET, ObjectStore

__all__ = [
    "BREP_EXPORT_FAILED",
    "BUCKET",
    "BUILD_MODEL_RETURN_ERROR",
    "BuiltGeometry",
    "CandidateSourceRef",
    "GEOMETRY_ARTIFACT_UNAVAILABLE",
    "GEOMETRY_BUILD_ERROR",
    "GEOMETRY_CHECKER_VERSION",
    "GEOMETRY_INVALID",
    "SNAPSHOT_FIELDS",
    "CadGeometryExtractor",
    "ExtractedGeometry",
    "GeometryAnalyzer",
    "GeometryArtifact",
    "GeometryArtifactError",
    "GeometryArtifactStore",
    "GeometryEngine",
    "GeometryExtractionError",
    "GeometryResult",
    "GeometrySnapshotStore",
    "ObjectStore",
    "SNAPSHOT_DERIVATION_FAILED",
    "artifact_storage_path",
    "build_geometry",
    "compare_geometry",
    "derive_warnings",
    "empty_snapshot",
    "runtime_provenance",
    "snapshot_from_row",
]
