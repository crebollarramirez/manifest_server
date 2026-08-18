"""Process one ``geometry_check`` generation_jobs row.

Mirrors ``validate_cad_job.py``'s structure: verify the current source has not
gone stale since the job was queued, ask the geometry layer for both sides of
the comparison, and return a ``{"status", "report", "error_message"}`` envelope
the worker loop already knows how to dispatch on.

This module resolves *which two exact source versions* to compare and shapes the
report. It does not execute CAD, serialize geometry, compute a metric, or decide
what a delta means -- all of that lives behind ``GeometryEngine``.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

try:
    from .geometry import (
        SNAPSHOT_FIELDS,
        CandidateSourceRef,
        GeometryEngine,
        ObjectStore,
    )
    from .subprocess_sandbox import validation_timeout_seconds
except ImportError:  # pragma: no cover - flat layout inside the worker image
    from geometry import (
        SNAPSHOT_FIELDS,
        CandidateSourceRef,
        GeometryEngine,
        ObjectStore,
    )
    from subprocess_sandbox import validation_timeout_seconds


WORKER_DIR = Path(__file__).resolve().parent

# Re-exported for tests that pin the persisted column vocabulary against the
# migrations. The tuple itself now lives with the analyzer that produces it.
_GEOMETRY_FIELDS = SNAPSHOT_FIELDS


def _cancelled_report(source_sha256: str, actual_sha256: str) -> dict:
    return {
        "schema_version": 1,
        "status": "cancelled",
        "source_sha256": actual_sha256,
        "requested_source_sha256": source_sha256,
        "previous_source_sha256": None,
        "geometry": None,
        "delta": None,
        "warnings": [],
        "superseded": True,
        "error_message": (
            "The candidate changed after this geometry check was queued; "
            "the queued source hash no longer matches the stored candidate."
        ),
    }


def geometry_check_job(supabase, job: dict) -> dict:
    job_id = str(job["id"])
    project_id = str(job["project_id"])
    part_id = str(job["part_id"])
    edit_job_id = job.get("edit_job_id")
    source_sha256 = str(job.get("source_sha256") or "")
    source_storage_path = str(job.get("source_storage_path") or "")
    previous_source_sha256 = job.get("previous_source_sha256")
    previous_source_storage_path = job.get("previous_source_storage_path")

    workdir = Path(f"/tmp/geometry_check_jobs/{job_id}")
    shutil.rmtree(workdir, ignore_errors=True)
    timeout_seconds = validation_timeout_seconds()

    objects = ObjectStore(supabase)
    engine = GeometryEngine(supabase, object_store=objects)

    try:
        # The candidate can change while this job waits in the queue -- verify
        # the stored bytes still match the exact hash the job was queued for
        # before treating anything downstream as evidence for it.
        current_bytes = objects.download(source_storage_path)
        actual_sha256 = hashlib.sha256(current_bytes).hexdigest()
        if actual_sha256 != source_sha256:
            report = _cancelled_report(source_sha256, actual_sha256)
            return {
                "status": "cancelled",
                "report": report,
                "error_message": report["error_message"],
            }

        current = engine.snapshot_for(
            CandidateSourceRef(
                project_id=project_id,
                part_id=part_id,
                candidate_id=edit_job_id,
                source_storage_path=source_storage_path,
                source_sha256=source_sha256,
            ),
            source_bytes=current_bytes,
            workdir=workdir / "current",
            timeout_seconds=timeout_seconds,
        )

        previous = engine.resolve(
            CandidateSourceRef(
                project_id=project_id,
                part_id=part_id,
                candidate_id=edit_job_id,
                source_storage_path=str(previous_source_storage_path or ""),
                source_sha256=str(previous_source_sha256 or ""),
            ),
            workdir=workdir / f"previous-{str(previous_source_sha256 or '')[:16]}",
            timeout_seconds=timeout_seconds,
        )

        delta, warnings = engine.compare(previous, current)

        geometry = current.snapshot
        job_status = "completed" if geometry["execution_ok"] else "failed"
        report = {
            "schema_version": 1,
            "status": job_status,
            "source_sha256": source_sha256,
            "previous_source_sha256": (
                previous_source_sha256 if previous is not None else None
            ),
            "geometry": geometry,
            "delta": delta,
            "warnings": warnings,
            "error_message": geometry.get("error_message"),
        }
        return {
            "status": job_status,
            "report": report,
            "error_message": None if job_status == "completed" else geometry.get(
                "error_message"
            ),
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
