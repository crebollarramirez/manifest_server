from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cad_ast_validator import validate_cad_source


BUCKET = "3dProjects"
WORKER_DIR = Path(__file__).resolve().parent
VALIDATION_RUNNER = WORKER_DIR / "cad_validation_runner.py"
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 60
MAX_CAPTURED_OUTPUT = 16_000


def cad_part_storage_path(project_id: str, part_id: str, filename: str) -> str:
    return f"{project_id}/parts/cad/{part_id}/{filename}"


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
    command = [
        sys.executable,
        "-I",
        str(VALIDATION_RUNNER),
        "--model",
        str(model_path),
        "--params",
        str(params_path),
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
        }

    stdout = bounded_output(result.stdout)
    stderr = bounded_output(result.stderr)
    errors = []
    if result.returncode != 0:
        errors.append(
            {
                "code": "runtime_error",
                "message": f"CAD model execution exited with code {result.returncode}.",
            }
        )
    return {
        "passed": result.returncode == 0,
        "skipped": False,
        "exit_code": result.returncode,
        "timed_out": False,
        "stdout": stdout,
        "stderr": stderr,
        "errors": errors,
    }


def validation_summary(report: dict) -> str:
    if report.get("superseded"):
        return "CAD validation was cancelled because model.py was superseded."

    messages: list[str] = []
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


def validate_cad_job(supabase, job: dict) -> dict:
    job_id = str(job["id"])
    project_id = str(job["project_id"])
    part_id = str(job["part_id"])
    expected_hash = str(job.get("source_sha256") or "")
    workdir = Path(f"/tmp/validation_jobs/{job_id}")
    shutil.rmtree(workdir, ignore_errors=True)

    try:
        source_dir = workdir / "source"
        model_path = source_dir / "model.py"
        params_path = source_dir / "params.json"
        download_file(
            supabase,
            cad_part_storage_path(project_id, part_id, "model.py"),
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
            report = {
                "schema_version": 1,
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
        report = validate_cad_source(source)
        report["source_sha256"] = actual_hash
        if report.pop("safe_to_execute"):
            runtime = run_model(model_path, params_path, workdir)
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
        report["runtime"] = runtime
        report["valid"] = bool(report["valid"] and runtime["passed"])
        return {
            "status": "completed" if report["valid"] else "failed",
            "report": report,
            "error_message": None if report["valid"] else validation_summary(report),
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
