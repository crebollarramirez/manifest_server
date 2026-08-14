"""Process one ``geometry_check`` generation_jobs row.

Mirrors ``validate_cad_job.py``'s structure: verify the current source has not
gone stale since the job was queued, gate execution on the same AST safety
check full validation uses, execute in the same sandboxed subprocess pattern,
and return a ``{"status", "report", "error_message"}`` envelope the worker
loop already knows how to dispatch on.

The comparison layer (``geometry_comparison.py``) operates purely on measured
geometry facts, never on source text -- this module's job is only to resolve
which two exact source versions to measure and compare.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

try:
    from .cad_ast_validator import validate_cad_source
    from .geometry_comparison import compare_geometry, derive_warnings
    from .geometry_inspection import GEOMETRY_CHECKER_VERSION, execution_failed_geometry
    from .subprocess_sandbox import run_sandboxed_runner, validation_timeout_seconds
    from .validate_cad_job import cad_part_storage_path, download_file
except ImportError:
    from cad_ast_validator import validate_cad_source
    from geometry_comparison import compare_geometry, derive_warnings
    from geometry_inspection import GEOMETRY_CHECKER_VERSION, execution_failed_geometry
    from subprocess_sandbox import run_sandboxed_runner, validation_timeout_seconds
    from validate_cad_job import cad_part_storage_path, download_file


WORKER_DIR = Path(__file__).resolve().parent
GEOMETRY_CHECK_RUNNER = WORKER_DIR / "geometry_check_runner.py"

_GEOMETRY_FIELDS = (
    "execution_ok",
    "geometry_valid",
    "error_message",
    "diagnostics",
    "volume_mm3",
    "bounding_box",
    "center_of_mass",
    "solid_count",
    "face_count",
    "edge_count",
)


def _snapshot_row_to_geometry(row: dict) -> dict:
    return {field: row.get(field) for field in _GEOMETRY_FIELDS}


def _load_cached_snapshot(supabase, source_sha256: str) -> dict | None:
    response = (
        supabase.table("geometry_snapshots")
        .select("*")
        .eq("source_sha256", source_sha256)
        .eq("geometry_checker_version", GEOMETRY_CHECKER_VERSION)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def _persist_snapshot(
    supabase,
    *,
    project_id: str,
    part_id: str,
    edit_job_id: str | None,
    source_storage_path: str,
    source_sha256: str,
    geometry: dict,
) -> None:
    payload = {
        "project_id": project_id,
        "part_id": part_id,
        "edit_job_id": edit_job_id,
        "source_storage_path": source_storage_path,
        "source_sha256": source_sha256,
        "geometry_checker_version": GEOMETRY_CHECKER_VERSION,
        **{field: geometry.get(field) for field in _GEOMETRY_FIELDS},
    }
    try:
        supabase.table("geometry_snapshots").insert(payload).execute()
    except Exception as exc:
        # A concurrent geometry check for the exact same (source_sha256,
        # geometry_checker_version) may have persisted this snapshot first --
        # that snapshot describes the same immutable content, so losing the
        # race is safe to ignore. Anything else is a real persistence failure.
        code = str(getattr(exc, "code", "") or "")
        if code != "23505" and "duplicate key" not in str(exc).lower():
            raise


def _execute_and_measure(
    model_path: Path,
    params_path: Path,
    workdir: Path,
    timeout_seconds: int,
) -> dict:
    result_path = workdir / "geometry-result.json"
    outcome = run_sandboxed_runner(
        GEOMETRY_CHECK_RUNNER,
        ["--model", str(model_path), "--params", str(params_path)],
        result_path=result_path,
        workdir=workdir,
        timeout_seconds=timeout_seconds,
    )
    if outcome.timed_out:
        return execution_failed_geometry(
            f"CAD model execution timed out after {timeout_seconds} seconds."
        )
    if outcome.result_json is None:
        return execution_failed_geometry(
            "Geometry runtime result could not be read: "
            f"{outcome.result_read_error}"
        )
    return outcome.result_json


def _measure_source(
    supabase,
    *,
    project_id: str,
    part_id: str,
    source_bytes: bytes,
    workdir: Path,
    timeout_seconds: int,
) -> dict:
    """Execute one already-downloaded, hash-verified source and measure it."""

    local_dir = workdir / "source"
    local_dir.mkdir(parents=True, exist_ok=True)
    model_path = local_dir / "model.py"
    params_path = local_dir / "params.json"
    model_path.write_bytes(source_bytes)
    try:
        download_file(
            supabase,
            cad_part_storage_path(project_id, part_id, "params.json"),
            params_path,
        )
    except Exception as exc:
        return execution_failed_geometry(f"Model parameters could not be read: {exc}")

    source = source_bytes.decode("utf-8")
    ast_report = validate_cad_source(source, file_path="model.py")
    if not ast_report.get("safe_to_execute"):
        # The report names the rule, the function, and the line. Forwarding
        # it is the whole point: a caller told only that "static safety
        # checks failed" has to guess which of its edits broke, and an agent
        # given that message repeatedly will thrash instead of fixing the one
        # thing that is actually wrong.
        diagnostics = ast_report.get("diagnostics")
        diagnostics = diagnostics if isinstance(diagnostics, list) else []
        summary = "; ".join(
            str(item.get("message"))
            for item in diagnostics[:3]
            if isinstance(item, dict) and item.get("message")
        )
        return execution_failed_geometry(
            "Source failed static safety checks and was not executed."
            + (f" {summary}" if summary else ""),
            diagnostics=diagnostics,
        )
    return _execute_and_measure(model_path, params_path, local_dir, timeout_seconds)


def _resolve_geometry(
    supabase,
    *,
    project_id: str,
    part_id: str,
    edit_job_id: str | None,
    storage_path: str | None,
    source_sha256: str | None,
    workdir: Path,
    timeout_seconds: int,
) -> dict | None:
    """Return cached or freshly-measured geometry facts for one source hash.

    Used for the *previous* source, whose content is immutable by the time a
    geometry check runs (either already cached from the check that produced
    it, or backed by ``original_storage_path``'s stable accepted-source
    backup) -- so, unlike the current source, no live staleness check applies
    here.
    """

    if not source_sha256:
        return None

    cached = _load_cached_snapshot(supabase, source_sha256)
    if cached is not None:
        return _snapshot_row_to_geometry(cached)

    if not storage_path:
        # No cached snapshot and nothing to (re-)inspect this source from.
        # Reporting "no previous available" is safer than fabricating one.
        return None

    try:
        source_bytes = _download_bytes(supabase, storage_path)
    except Exception:
        return None
    if hashlib.sha256(source_bytes).hexdigest() != source_sha256:
        return None

    geometry = _measure_source(
        supabase,
        project_id=project_id,
        part_id=part_id,
        source_bytes=source_bytes,
        workdir=workdir / f"previous-{source_sha256[:16]}",
        timeout_seconds=timeout_seconds,
    )
    _persist_snapshot(
        supabase,
        project_id=project_id,
        part_id=part_id,
        edit_job_id=edit_job_id,
        source_storage_path=storage_path,
        source_sha256=source_sha256,
        geometry=geometry,
    )
    return geometry


def _download_bytes(supabase, storage_path: str) -> bytes:
    return bytes(supabase.storage.from_("3dProjects").download(storage_path))


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

    try:
        # The candidate can change while this job waits in the queue -- verify
        # the stored bytes still match the exact hash the job was queued for
        # before treating anything downstream as evidence for it.
        current_bytes = _download_bytes(supabase, source_storage_path)
        actual_sha256 = hashlib.sha256(current_bytes).hexdigest()
        if actual_sha256 != source_sha256:
            report = _cancelled_report(source_sha256, actual_sha256)
            return {
                "status": "cancelled",
                "report": report,
                "error_message": report["error_message"],
            }

        cached_current = _load_cached_snapshot(supabase, source_sha256)
        if cached_current is not None:
            current = _snapshot_row_to_geometry(cached_current)
        else:
            current = _measure_source(
                supabase,
                project_id=project_id,
                part_id=part_id,
                source_bytes=current_bytes,
                workdir=workdir / "current",
                timeout_seconds=timeout_seconds,
            )
            _persist_snapshot(
                supabase,
                project_id=project_id,
                part_id=part_id,
                edit_job_id=edit_job_id,
                source_storage_path=source_storage_path,
                source_sha256=source_sha256,
                geometry=current,
            )

        previous = _resolve_geometry(
            supabase,
            project_id=project_id,
            part_id=part_id,
            edit_job_id=edit_job_id,
            storage_path=previous_source_storage_path,
            source_sha256=previous_source_sha256,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
        )

        delta = compare_geometry(previous, current) if previous is not None else None
        warnings = (
            derive_warnings(previous, current, delta) if previous is not None else []
        )

        job_status = "completed" if current["execution_ok"] else "failed"
        report = {
            "schema_version": 1,
            "status": job_status,
            "source_sha256": source_sha256,
            "previous_source_sha256": (
                previous_source_sha256 if previous is not None else None
            ),
            "geometry": current,
            "delta": delta,
            "warnings": warnings,
            "error_message": current.get("error_message"),
        }
        return {
            "status": job_status,
            "report": report,
            "error_message": None if job_status == "completed" else current.get(
                "error_message"
            ),
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
