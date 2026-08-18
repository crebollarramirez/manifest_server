"""The candidate-bound native geometry artifact and its serialization.

A ``GeometryArtifact`` is the authoritative geometric representation of one
built CAD candidate: the normalized root shape, serialized to a native OCCT
B-rep file, bound to the candidate and to the exact source that produced it.

It is not a replacement for ``model.py``. ``model.py`` remains the canonical,
reproducible design source; the artifact is what that source produced on one
particular run of one particular CAD runtime.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cadquery as cq

ARTIFACT_FORMAT_BREP = "brep"

# The runtime tried to serialize a root shape it had successfully built and
# measured, and the export failed. Distinct from every build failure: the
# geometry exists, it just did not reach durable storage.
BREP_EXPORT_FAILED = "BREP_EXPORT_FAILED"

# The artifact could not be produced for use: no row, no object, a download
# failure, or bytes whose digest does not match what was recorded.
GEOMETRY_ARTIFACT_UNAVAILABLE = "GEOMETRY_ARTIFACT_UNAVAILABLE"


class GeometryArtifactError(Exception):
    """A structured failure in producing or retrieving a geometry artifact."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass(frozen=True)
class GeometryArtifact:
    """Provenance for one persisted B-rep file.

    ``candidate_id`` is the edit-job id, which is how a candidate is identified
    everywhere in this system; it is ``None`` for artifacts built from accepted
    (committed) source, which belongs to no edit job.

    ``source_hash`` and ``artifact_digest`` answer different questions and must
    not be conflated:

        source_hash      which source produced this geometry
        artifact_digest  which exact bytes are stored

    The digest is an integrity and identity check on the file. It is NOT a
    canonical identity for the geometry -- OCCT's B-rep serialization is
    deterministic for a given construction but not canonical across
    constructions, so two geometrically identical shapes built different ways
    produce different digests. Nothing may infer "different digest therefore
    different geometry" from it.
    """

    artifact_id: str | None
    project_id: str
    part_id: str
    candidate_id: str | None
    source_hash: str
    source_storage_path: str
    artifact_format: str
    artifact_storage_path: str
    artifact_digest: str
    artifact_bytes: int
    geometry_runtime: dict[str, Any] | None = None
    created_at: str | None = None


def artifact_storage_path(source_storage_path: str, source_sha256: str) -> str:
    """Derive where an artifact lives from where its source lives.

    Deriving rather than composing from ids handles every case with one rule --
    live candidate, ``original/`` backup, and accepted part source all sit at
    ``.../model.py`` -- and it keeps provenance visible in the path itself:

        {project}/candidates/cad/{part}/{edit_job}/model.py
            -> {project}/candidates/cad/{part}/{edit_job}/geometry/{sha}.brep

    The source hash, not the artifact digest, names the file. The artifact is
    cached and reused on the identity of the source that produced it, exactly
    as its snapshot is.
    """

    parent = source_storage_path.rsplit("/", 1)[0] if "/" in source_storage_path else ""
    prefix = f"{parent}/" if parent else ""
    return f"{prefix}geometry/{source_sha256}.brep"


def serialize_root(root: Any, destination: Path) -> tuple[str, int]:
    """Write ``root`` to ``destination`` as native B-rep; return digest and size.

    The digest is taken from the bytes actually on disk rather than from an
    in-memory buffer, because CadQuery's file and ``BytesIO`` export paths do
    not produce byte-identical output -- hashing what was written is the only
    thing that makes the digest verifiable on the way back in.
    """

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        exported = root.exportBrep(str(destination))
    except Exception as exc:
        raise GeometryArtifactError(
            BREP_EXPORT_FAILED,
            f"Built geometry could not be serialized to B-rep: {exc}",
        ) from exc
    if exported is False or not destination.exists():
        raise GeometryArtifactError(
            BREP_EXPORT_FAILED,
            "Built geometry could not be serialized to B-rep.",
        )

    raw = destination.read_bytes()
    if not raw:
        raise GeometryArtifactError(
            BREP_EXPORT_FAILED,
            "B-rep serialization produced an empty file.",
        )
    return hashlib.sha256(raw).hexdigest(), len(raw)


def load_root(raw: bytes, workdir: Path) -> Any:
    """Rebuild a root ``cq.Shape`` from serialized B-rep bytes.

    This is the read side of the artifact and the seam future bounded geometry
    queries will sit on: it is how a candidate's topology is recovered without
    re-executing its source. Nothing above ``GeometryEngine`` calls it, and no
    caller receives the shape itself.

    Note that a snapshot re-derived from a reloaded artifact is not bit-identical
    to the one derived from the in-process root -- OCCT round-trips floating
    point at roughly 1e-13 relative. That is far inside the comparison
    tolerances, but it is why snapshots are derived once, before serialization,
    rather than re-derived on load.
    """

    workdir.mkdir(parents=True, exist_ok=True)
    scratch = workdir / "artifact.brep"
    scratch.write_bytes(raw)
    try:
        return cq.Shape.importBrep(str(scratch))
    except Exception as exc:
        raise GeometryArtifactError(
            GEOMETRY_ARTIFACT_UNAVAILABLE,
            f"Stored geometry artifact could not be read: {exc}",
        ) from exc


def verify_digest(raw: bytes, expected_digest: str) -> None:
    """Raise unless ``raw`` hashes to ``expected_digest``."""

    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_digest:
        raise GeometryArtifactError(
            GEOMETRY_ARTIFACT_UNAVAILABLE,
            "Stored geometry artifact does not match its recorded digest.",
        )
