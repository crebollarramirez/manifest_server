"""The geometry service boundary.

``GeometryEngine`` is the only thing outside this package that anything needs
to talk to in order to get geometric facts about a candidate. Layers above it
-- the geometry-check job, CAD validation, and eventually agent-facing query
tools -- do not know how B-rep files are stored, how they are loaded, or how
OCCT topology is represented.

Current responsibilities:

    snapshot_for(ref)   derive (or reuse) the snapshot for one candidate source
    record(...)         adopt geometry a build already produced
    load_root(ref)      recover the native root shape from its artifact
    compare(a, b)       diff two snapshots

``load_root`` is the seam future bounded geometry queries will sit on -- finding
topology, measuring between references, sectioning. None of that is implemented
and no tool calls ``load_root`` today. It exists implemented and tested so the
architecture is not accidentally shaped in a way that would prevent it.

Whatever is added later, one rule holds: raw B-rep topology never leaves this
package. Callers receive bounded structured answers, never shapes and never
topology dumps.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analyzer import empty_snapshot
from .artifact import (
    GEOMETRY_ARTIFACT_UNAVAILABLE,
    GeometryArtifact,
    GeometryArtifactError,
)
from .artifact_store import GeometryArtifactStore
from .comparison import compare_geometry, derive_warnings
from .snapshot_store import GeometrySnapshotStore, snapshot_from_row
from .storage import ObjectStore, cad_part_storage_path


@dataclass(frozen=True)
class CandidateSourceRef:
    """Which exact candidate source geometry is being asked about.

    ``candidate_id`` is the edit-job id -- how a candidate is identified
    everywhere in this system -- and is ``None`` for accepted (committed)
    source, which belongs to no edit job.

    ``source_sha256`` is what actually resolves geometry. Two candidates with
    byte-identical source describe the same geometry and share it deliberately;
    two candidates whose source differs at all have different hashes and
    therefore cannot reach each other's artifact or snapshot. Isolation is a
    property of content addressing here, not of a permission check.
    """

    project_id: str
    part_id: str
    candidate_id: str | None
    source_storage_path: str
    source_sha256: str


@dataclass(frozen=True)
class GeometryResult:
    """One candidate's derived snapshot and the artifact it was derived from.

    ``artifact`` is ``None`` when the build produced no usable geometry, when
    serialization failed, or when the snapshot came from cache and predates its
    artifact. A snapshot without a reachable artifact is still a valid
    measurement of what was there.
    """

    snapshot: dict[str, Any]
    artifact: GeometryArtifact | None
    from_cache: bool


class GeometryEngine:
    def __init__(
        self,
        supabase,
        *,
        object_store: ObjectStore | None = None,
        artifact_store: GeometryArtifactStore | None = None,
        snapshot_store: GeometrySnapshotStore | None = None,
    ) -> None:
        self._supabase = supabase
        self._objects = object_store or ObjectStore(supabase)
        self._artifacts = artifact_store or GeometryArtifactStore(
            supabase, object_store=self._objects
        )
        self._snapshots = snapshot_store or GeometrySnapshotStore(supabase)

    # ---- reuse ---------------------------------------------------------

    def cached(self, source_sha256: str) -> GeometryResult | None:
        """Return an already-derived snapshot for this exact source, if any."""

        row = self._snapshots.find(source_sha256)
        if row is None:
            return None
        return GeometryResult(
            snapshot=snapshot_from_row(row),
            artifact=self._artifacts.find(source_sha256),
            from_cache=True,
        )

    # ---- derivation ----------------------------------------------------

    def snapshot_for(
        self,
        ref: CandidateSourceRef,
        *,
        source_bytes: bytes,
        workdir: Path,
        timeout_seconds: int,
    ) -> GeometryResult:
        """Derive the snapshot for one candidate source, reusing when possible.

        Executes the source only when nothing has measured this exact hash
        under the current checker version. Because full CAD validation now
        produces geometry too, and validation runs on every mutation, this is
        usually a cache read.
        """

        cached = self.cached(ref.source_sha256)
        if cached is not None:
            return cached

        snapshot, artifact_descriptor, brep_path = self.measure_source(
            ref,
            source_bytes=source_bytes,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
        )
        return self.record(
            ref,
            snapshot=snapshot,
            artifact_descriptor=artifact_descriptor,
            brep_path=brep_path,
        )

    def measure_source(
        self,
        ref: CandidateSourceRef,
        *,
        source_bytes: bytes,
        workdir: Path,
        timeout_seconds: int,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, Path | None]:
        """Execute one already hash-verified source and measure what it builds."""

        # Imported here rather than at module scope: `execution` reaches back
        # out to the worker's sandbox and AST modules, and importing it eagerly
        # would make the geometry package unimportable in any context that does
        # not have those on the path.
        from .execution import execute_and_measure

        return execute_and_measure(
            self._objects,
            project_id=ref.project_id,
            part_id=ref.part_id,
            source_bytes=source_bytes,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
        )

    def record(
        self,
        ref: CandidateSourceRef,
        *,
        snapshot: dict[str, Any],
        artifact_descriptor: dict[str, Any] | None,
        brep_path: Path | None,
    ) -> GeometryResult:
        """Persist geometry a build already produced.

        Artifact persistence is deliberately not allowed to cost the snapshot.
        A failed upload means later bounded queries cannot reach this
        candidate's topology; it does not mean the measurement that succeeded
        should be thrown away.
        """

        artifact: GeometryArtifact | None = None
        if artifact_descriptor and brep_path is not None and brep_path.exists():
            try:
                artifact = self._artifacts.store(
                    project_id=ref.project_id,
                    part_id=ref.part_id,
                    candidate_id=ref.candidate_id,
                    source_storage_path=ref.source_storage_path,
                    source_sha256=ref.source_sha256,
                    brep_path=brep_path,
                    artifact_digest=str(artifact_descriptor.get("digest") or ""),
                    artifact_bytes=int(artifact_descriptor.get("bytes") or 0),
                    geometry_runtime=artifact_descriptor.get("runtime"),
                )
            except Exception:
                artifact = None

        self._snapshots.store(
            project_id=ref.project_id,
            part_id=ref.part_id,
            candidate_id=ref.candidate_id,
            source_storage_path=ref.source_storage_path,
            source_sha256=ref.source_sha256,
            snapshot=snapshot,
            geometry_artifact_id=artifact.artifact_id if artifact else None,
        )
        return GeometryResult(snapshot=snapshot, artifact=artifact, from_cache=False)

    # ---- resolution ----------------------------------------------------

    def resolve(
        self,
        ref: CandidateSourceRef,
        *,
        workdir: Path,
        timeout_seconds: int,
    ) -> GeometryResult | None:
        """Return geometry for a source whose content is already immutable.

        Used for the *previous* side of a comparison: either already cached
        from the check that produced it, or backed by a stable storage path.
        The downloaded bytes are re-hashed and required to match before
        anything derived from them is treated as evidence for this ref -- a
        path that no longer holds the content it was recorded for describes a
        different candidate, and measuring it would attribute one candidate's
        geometry to another.
        """

        if not ref.source_sha256:
            return None

        cached = self.cached(ref.source_sha256)
        if cached is not None:
            return cached

        if not ref.source_storage_path:
            return None

        try:
            source_bytes = self._objects.download(ref.source_storage_path)
        except Exception:
            return None
        if hashlib.sha256(source_bytes).hexdigest() != ref.source_sha256:
            return None

        return self.snapshot_for(
            ref,
            source_bytes=source_bytes,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
        )

    # ---- native geometry ------------------------------------------------

    def load_root(self, ref: CandidateSourceRef, workdir: Path) -> Any:
        """Recover one candidate's native root shape from its artifact.

        The read side of the source of truth: a candidate's topology without
        re-executing its source. Reserved for the geometry layer's own use and
        for future bounded queries; the shape is never returned above this
        package.
        """

        artifact = self._artifacts.find(ref.source_sha256)
        if artifact is None:
            raise GeometryArtifactError(
                GEOMETRY_ARTIFACT_UNAVAILABLE,
                "No geometry artifact has been recorded for this source.",
            )
        return self._artifacts.load_root(artifact, workdir)

    # ---- comparison ------------------------------------------------------

    def compare(
        self,
        previous: GeometryResult | None,
        current: GeometryResult,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Diff two candidates' geometry.

        Both sides originate from exact candidate-bound native geometry, which
        is the improvement this refactor makes here. The diff itself is
        deliberately still snapshot-level: B-rep-to-B-rep change detection is
        future work, and inventing it now would replace a contract that is
        understood with one that is not.
        """

        if previous is None:
            return None, []
        delta = compare_geometry(previous.snapshot, current.snapshot)
        return delta, derive_warnings(previous.snapshot, current.snapshot, delta)

    @staticmethod
    def unmeasurable(error_message: str, diagnostics: list[dict] | None = None) -> GeometryResult:
        """A result for source that never executed."""

        return GeometryResult(
            snapshot=empty_snapshot(
                execution_ok=False,
                geometry_valid=None,
                error_message=error_message,
                diagnostics=diagnostics,
            ),
            artifact=None,
            from_cache=False,
        )
