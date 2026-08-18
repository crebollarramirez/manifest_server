"""Persistence for native B-rep geometry artifacts.

Artifacts are stored the way candidate source already is: bytes in the
``3dProjects`` bucket, a row in Postgres pointing at them. The database
references the artifact rather than holding it, so no ``select *`` anywhere can
put native topology one careless join away from an agent-facing payload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifact import (
    ARTIFACT_FORMAT_BREP,
    GEOMETRY_ARTIFACT_UNAVAILABLE,
    GeometryArtifact,
    GeometryArtifactError,
    artifact_storage_path,
    load_root,
    verify_digest,
)
from .runtime import GEOMETRY_CHECKER_VERSION, runtime_provenance
from .storage import ObjectStore

TABLE = "geometry_artifacts"
CONTENT_TYPE = "application/octet-stream"

_COLUMNS = (
    "project_id",
    "part_id",
    "edit_job_id",
    "source_storage_path",
    "source_sha256",
    "geometry_checker_version",
    "artifact_format",
    "artifact_storage_path",
    "artifact_digest",
    "artifact_bytes",
    "geometry_runtime",
)


def _row_to_artifact(row: dict[str, Any]) -> GeometryArtifact:
    return GeometryArtifact(
        artifact_id=row.get("id"),
        project_id=str(row.get("project_id") or ""),
        part_id=str(row.get("part_id") or ""),
        candidate_id=row.get("edit_job_id"),
        source_hash=str(row.get("source_sha256") or ""),
        source_storage_path=str(row.get("source_storage_path") or ""),
        artifact_format=str(row.get("artifact_format") or ARTIFACT_FORMAT_BREP),
        artifact_storage_path=str(row.get("artifact_storage_path") or ""),
        artifact_digest=str(row.get("artifact_digest") or ""),
        artifact_bytes=int(row.get("artifact_bytes") or 0),
        geometry_runtime=row.get("geometry_runtime"),
        created_at=row.get("created_at"),
    )


class GeometryArtifactStore:
    """Store, look up, and reload candidate-bound B-rep artifacts."""

    def __init__(self, supabase, object_store: ObjectStore | None = None) -> None:
        self._supabase = supabase
        self._objects = object_store or ObjectStore(supabase)

    def find(self, source_sha256: str) -> GeometryArtifact | None:
        """Return the artifact for one exact source hash, if one exists.

        Lookup is by source hash under the current checker version, never by
        candidate. Two candidates whose source is byte-identical describe the
        same geometry, and re-deriving it for the second would be waste; two
        candidates whose source differs at all have different hashes and
        therefore cannot see each other's artifact.
        """

        response = (
            self._supabase.table(TABLE)
            .select("*")
            .eq("source_sha256", source_sha256)
            .eq("geometry_checker_version", GEOMETRY_CHECKER_VERSION)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return _row_to_artifact(rows[0]) if rows else None

    def store(
        self,
        *,
        project_id: str,
        part_id: str,
        candidate_id: str | None,
        source_storage_path: str,
        source_sha256: str,
        brep_path: Path,
        artifact_digest: str,
        artifact_bytes: int,
        geometry_runtime: dict[str, Any] | None = None,
    ) -> GeometryArtifact:
        """Upload one serialized B-rep and record it.

        The bytes are verified against the digest the runtime reported before
        anything is uploaded -- a digest recorded for bytes that were never
        checked would make the whole integrity claim decorative.
        """

        raw = brep_path.read_bytes()
        verify_digest(raw, artifact_digest)

        storage_path = artifact_storage_path(source_storage_path, source_sha256)
        self._objects.upload(storage_path, raw, CONTENT_TYPE)

        payload = {
            "project_id": project_id,
            "part_id": part_id,
            "edit_job_id": candidate_id,
            "source_storage_path": source_storage_path,
            "source_sha256": source_sha256,
            "geometry_checker_version": GEOMETRY_CHECKER_VERSION,
            "artifact_format": ARTIFACT_FORMAT_BREP,
            "artifact_storage_path": storage_path,
            "artifact_digest": artifact_digest,
            "artifact_bytes": artifact_bytes,
            "geometry_runtime": geometry_runtime or runtime_provenance(),
        }
        try:
            response = self._supabase.table(TABLE).insert(payload).execute()
            rows = response.data or []
            if rows:
                return _row_to_artifact(rows[0])
        except Exception as exc:
            # A concurrent job measuring the exact same (source_sha256,
            # geometry_checker_version) may have won the race. That artifact
            # describes the same immutable source, so adopting it is correct;
            # anything else is a real persistence failure.
            code = str(getattr(exc, "code", "") or "")
            if code != "23505" and "duplicate key" not in str(exc).lower():
                raise
            existing = self.find(source_sha256)
            if existing is not None:
                return existing

        recorded = self.find(source_sha256)
        if recorded is not None:
            return recorded
        return _row_to_artifact({k: payload[k] for k in _COLUMNS})

    def load_root(self, artifact: GeometryArtifact, workdir: Path) -> Any:
        """Reload one artifact's root shape, verifying integrity on the way in.

        The seam future bounded geometry queries will sit on. Nothing above
        ``GeometryEngine`` calls this, and no caller outside the geometry layer
        ever receives the shape.
        """

        try:
            raw = self._objects.download(artifact.artifact_storage_path)
        except GeometryArtifactError:
            raise
        except Exception as exc:
            raise GeometryArtifactError(
                GEOMETRY_ARTIFACT_UNAVAILABLE,
                f"Stored geometry artifact could not be read: {exc}",
            ) from exc
        verify_digest(raw, artifact.artifact_digest)
        return load_root(raw, workdir)
