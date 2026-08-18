"""Execute one candidate source in the sandbox and bring its geometry back.

The bridge between the geometry layer and this worker's existing sandboxing.
It is kept separate from ``engine.py`` so the rest of the package stays
importable in contexts that do not have the worker's sandbox and AST modules on
the path.

What crosses the process boundary is unchanged by this refactor: argv in, a
JSON result file out, plus files on the shared filesystem. The native B-rep
travels as a file the parent named and the child wrote -- never pickled, never
base64'd into JSON, and never anywhere an agent-facing payload could pick it up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .analyzer import empty_snapshot
from .storage import ObjectStore, cad_part_storage_path

try:
    from ..cad_ast_validator import validate_cad_source
    from ..subprocess_sandbox import run_sandboxed_runner
except ImportError:  # pragma: no cover - flat layout inside the worker image
    from cad_ast_validator import validate_cad_source
    from subprocess_sandbox import run_sandboxed_runner

WORKER_DIR = Path(__file__).resolve().parent.parent
GEOMETRY_CHECK_RUNNER = WORKER_DIR / "geometry_check_runner.py"

MeasuredSource = tuple[dict[str, Any], dict[str, Any] | None, Path | None]


def _failed(error_message: str, diagnostics: list[dict] | None = None) -> MeasuredSource:
    return (
        empty_snapshot(
            execution_ok=False,
            geometry_valid=None,
            error_message=error_message,
            diagnostics=diagnostics,
        ),
        None,
        None,
    )


def execute_and_measure(
    objects: ObjectStore,
    *,
    project_id: str,
    part_id: str,
    source_bytes: bytes,
    workdir: Path,
    timeout_seconds: int,
) -> MeasuredSource:
    """Run one already hash-verified source; return snapshot, artifact, brep path."""

    local_dir = workdir / "source"
    local_dir.mkdir(parents=True, exist_ok=True)
    model_path = local_dir / "model.py"
    params_path = local_dir / "params.json"
    brep_path = local_dir / "model.brep"
    model_path.write_bytes(source_bytes)

    try:
        params_path.write_bytes(
            objects.download(cad_part_storage_path(project_id, part_id, "params.json"))
        )
    except Exception as exc:
        return _failed(f"Model parameters could not be read: {exc}")

    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _failed(f"Candidate source is not valid UTF-8: {exc}")

    ast_report = validate_cad_source(source, file_path="model.py")
    if not ast_report.get("safe_to_execute"):
        # The report names the rule, the function, and the line. Forwarding it
        # is the whole point: a caller told only that "static safety checks
        # failed" has to guess which of its edits broke, and an agent given
        # that message repeatedly will thrash instead of fixing the one thing
        # that is actually wrong.
        diagnostics = ast_report.get("diagnostics")
        diagnostics = diagnostics if isinstance(diagnostics, list) else []
        summary = "; ".join(
            str(item.get("message"))
            for item in diagnostics[:3]
            if isinstance(item, dict) and item.get("message")
        )
        return _failed(
            "Source failed static safety checks and was not executed."
            + (f" {summary}" if summary else ""),
            diagnostics=diagnostics,
        )

    result_path = local_dir / "geometry-result.json"
    outcome = run_sandboxed_runner(
        GEOMETRY_CHECK_RUNNER,
        [
            "--model",
            str(model_path),
            "--params",
            str(params_path),
            "--brep",
            str(brep_path),
        ],
        result_path=result_path,
        workdir=local_dir,
        timeout_seconds=timeout_seconds,
    )
    if outcome.timed_out:
        return _failed(
            f"CAD model execution timed out after {timeout_seconds} seconds."
        )
    if outcome.result_json is None:
        return _failed(
            f"Geometry runtime result could not be read: {outcome.result_read_error}"
        )

    snapshot = dict(outcome.result_json)
    artifact_descriptor = snapshot.pop("geometry_artifact", None)
    return snapshot, artifact_descriptor, brep_path if brep_path.exists() else None
