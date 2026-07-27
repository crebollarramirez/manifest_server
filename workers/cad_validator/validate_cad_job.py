from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from .cad_ast_validator import symbol_at_line, validate_cad_source
except ImportError:
    from cad_ast_validator import symbol_at_line, validate_cad_source


BUCKET = "3dProjects"
WORKER_DIR = Path(__file__).resolve().parent
VALIDATION_RUNNER = WORKER_DIR / "cad_validation_runner.py"
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 60
MAX_CAPTURED_OUTPUT = 16_000


def cad_part_storage_path(project_id: str, part_id: str, filename: str) -> str:
    return f"{project_id}/parts/cad/{part_id}/{filename}"


def validation_source_path(job: dict) -> str:
    project_id = str(job["project_id"])
    part_id = str(job["part_id"])
    if str(job.get("source_kind") or "accepted") == "accepted":
        return cad_part_storage_path(project_id, part_id, "model.py")

    edit_job_id = str(job.get("edit_job_id") or "")
    storage_path = str(job.get("source_storage_path") or "")
    prefix = f"{project_id}/candidates/cad/{part_id}/{edit_job_id}/"
    if (
        not edit_job_id
        or not storage_path.startswith(prefix)
        or not storage_path.endswith("/model.py")
        or ".." in storage_path.split("/")
    ):
        raise ValueError("Candidate validation source path is outside its edit job.")
    return storage_path


def download_file(supabase, storage_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    data = supabase.storage.from_(BUCKET).download(storage_path)
    local_path.write_bytes(data)


def validation_timeout_seconds() -> int:
    value = os.environ.get(
        "CAD_VALIDATION_TIMEOUT_SECONDS",
        str(DEFAULT_VALIDATION_TIMEOUT_SECONDS),
    )
    try:
        timeout = int(value)
    except ValueError as exc:
        raise ValueError("CAD_VALIDATION_TIMEOUT_SECONDS must be an integer.") from exc
    if timeout <= 0:
        raise ValueError("CAD_VALIDATION_TIMEOUT_SECONDS must be greater than zero.")
    return timeout


def sanitized_validation_environment(workdir: Path) -> dict[str, str]:
    environment = os.environ.copy()
    sensitive_names = {
        "OPENAI_API_KEY",
        "SUPABASE_ANON_KEY",
        "SUPABASE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_URL",
    }
    for name in tuple(environment):
        if name in sensitive_names or name.endswith(("_TOKEN", "_SECRET")):
            environment.pop(name, None)

    home_dir = workdir / "home"
    temp_dir = workdir / "tmp"
    home_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    environment["HOME"] = str(home_dir)
    environment["TMPDIR"] = str(temp_dir)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def bounded_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-MAX_CAPTURED_OUTPUT:]


def run_model(model_path: Path, params_path: Path, workdir: Path) -> dict:
    timeout = validation_timeout_seconds()
    result_path = workdir / "runtime-result.json"
    command = [
        sys.executable,
        "-I",
        str(VALIDATION_RUNNER),
        "--model",
        str(model_path),
        "--params",
        str(params_path),
        "--result",
        str(result_path),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=workdir,
            env=sanitized_validation_environment(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = bounded_output(exc.stdout)
        stderr = bounded_output(exc.stderr)
        message = f"CAD model execution timed out after {timeout} seconds."
        if stderr:
            stderr = f"{stderr}\n{message}"
        else:
            stderr = message
        return {
            "passed": False,
            "skipped": False,
            "exit_code": None,
            "timed_out": True,
            "stdout": stdout,
            "stderr": bounded_output(stderr),
            "errors": [{"code": "runtime_timeout", "message": message}],
            "validation_result": {
                "status": "failed",
                "stage": "cadquery_runtime",
                "repairable_hint": False,
                "diagnostics": [
                    {
                        "error_code": "VALIDATION_TIMEOUT",
                        "message": message,
                        "stage": "cadquery_runtime",
                        "file_path": "model.py",
                        "related_symbols": [],
                    }
                ],
                "build_artifacts": None,
            },
        }

    stdout = bounded_output(result.stdout)
    stderr = bounded_output(result.stderr)
    if result_path.exists():
        try:
            validation_result = json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            validation_result = {
                "status": "failed",
                "stage": "cadquery_runtime",
                "repairable_hint": False,
                "diagnostics": [
                    {
                        "error_code": "VALIDATION_WORKER_ERROR",
                        "message": f"Runtime result could not be read: {exc}",
                        "stage": "cadquery_runtime",
                        "file_path": "model.py",
                        "related_symbols": [],
                    }
                ],
                "build_artifacts": None,
            }
    else:
        validation_result = {
            "status": "failed",
            "stage": "cadquery_runtime",
            "repairable_hint": False,
            "diagnostics": [
                {
                    "error_code": "VALIDATION_WORKER_ERROR",
                    "message": (
                        "CAD runtime exited without writing a structured result."
                    ),
                    "stage": "cadquery_runtime",
                    "file_path": "model.py",
                    "related_symbols": [],
                }
            ],
            "build_artifacts": None,
        }
    errors = [
        {
            "code": str(diagnostic.get("error_code", "runtime_error")).lower(),
            "message": diagnostic.get("message", "CAD runtime failed."),
        }
        for diagnostic in validation_result.get("diagnostics", [])
    ]
    return {
        "passed": validation_result.get("status") == "passed",
        "skipped": False,
        "exit_code": result.returncode,
        "timed_out": False,
        "stdout": stdout,
        "stderr": stderr,
        "errors": errors,
        "validation_result": validation_result,
    }


def validation_summary(report: dict) -> str:
    if report.get("superseded"):
        return "CAD validation was cancelled because model.py was superseded."

    messages: list[str] = []
    for diagnostic in report.get("diagnostics", []):
        message = diagnostic.get("message")
        if isinstance(message, str) and message not in messages:
            messages.append(message)
    for check in report.get("checks", {}).values():
        for error in check.get("errors", []):
            message = error.get("message")
            if isinstance(message, str) and message not in messages:
                messages.append(message)
    runtime = report.get("runtime", {})
    runtime_stderr = runtime.get("stderr")
    if isinstance(runtime_stderr, str) and runtime_stderr.strip():
        messages.append(runtime_stderr.strip())
    return "\n".join(messages)[:4000] or "CAD validation failed."


def enrich_runtime_result(
    source: str,
    source_path: str,
    result: dict,
) -> dict:
    try:
        import ast

        tree = ast.parse(source, filename=source_path)
    except SyntaxError:
        tree = None
    diagnostics = []
    for raw in result.get("diagnostics", []):
        diagnostic = dict(raw)
        diagnostic["file_path"] = source_path
        line = diagnostic.get("line")
        if tree is not None and isinstance(line, int) and line > 0:
            function_name, semantic_id = symbol_at_line(tree, line)
            if function_name is not None:
                diagnostic["function_name"] = function_name
            if semantic_id is not None:
                diagnostic["semantic_id"] = semantic_id
        diagnostics.append(diagnostic)
    return {
        **result,
        "diagnostics": diagnostics,
    }


def validate_cad_job(supabase, job: dict) -> dict:
    job_id = str(job["id"])
    project_id = str(job["project_id"])
    part_id = str(job["part_id"])
    expected_hash = str(job.get("source_sha256") or "")
    source_path = validation_source_path(job)
    workdir = Path(f"/tmp/validation_jobs/{job_id}")
    shutil.rmtree(workdir, ignore_errors=True)

    try:
        source_dir = workdir / "source"
        model_path = source_dir / "model.py"
        params_path = source_dir / "params.json"
        download_file(
            supabase,
            source_path,
            model_path,
        )
        download_file(
            supabase,
            cad_part_storage_path(project_id, part_id, "params.json"),
            params_path,
        )

        model_bytes = model_path.read_bytes()
        actual_hash = hashlib.sha256(model_bytes).hexdigest()
        if actual_hash != expected_hash:
            hash_error_code = (
                "CANDIDATE_SOURCE_HASH_MISMATCH"
                if str(job.get("source_kind") or "accepted") == "candidate"
                else "SOURCE_HASH_MISMATCH"
            )
            report = {
                "schema_version": 2,
                "status": "failed",
                "stage": "source",
                "repairable_hint": False,
                "diagnostics": [
                    {
                        "error_code": hash_error_code,
                        "message": (
                            "Validation source did not match the queued source hash."
                        ),
                        "stage": "source",
                        "file_path": source_path,
                        "related_symbols": [],
                    }
                ],
                "build_artifacts": None,
                "valid": False,
                "superseded": True,
                "source_sha256": actual_hash,
                "expected_source_sha256": expected_hash,
                "checks": {},
                "runtime": {
                    "passed": False,
                    "skipped": True,
                    "stdout": "",
                    "stderr": "",
                    "errors": [
                        {
                            "code": "runtime_skipped",
                            "message": "Runtime execution was skipped for superseded source.",
                        }
                    ],
                },
            }
            return {
                "status": "cancelled",
                "report": report,
                "error_message": validation_summary(report),
            }

        source = model_bytes.decode("utf-8")
        report = validate_cad_source(source, file_path=source_path)
        report["source_sha256"] = actual_hash
        if report["safe_to_execute"]:
            runtime = run_model(model_path, params_path, workdir)
            runtime_result = enrich_runtime_result(
                source,
                source_path,
                runtime.pop("validation_result"),
            )
        else:
            runtime = {
                "passed": False,
                "skipped": True,
                "exit_code": None,
                "timed_out": False,
                "stdout": "",
                "stderr": "",
                "errors": [
                    {
                        "code": "runtime_skipped",
                        "message": "Runtime execution was skipped because source was unsafe to execute.",
                    }
                ],
            }
            runtime_result = None
        report["runtime"] = runtime
        if runtime_result is not None:
            report.update(runtime_result)
            report["valid"] = runtime_result["status"] == "passed"
        else:
            report["valid"] = False
        return {
            "status": "completed" if report["valid"] else "failed",
            "report": report,
            "error_message": None if report["valid"] else validation_summary(report),
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
