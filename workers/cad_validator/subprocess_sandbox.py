"""Shared sandboxed-subprocess execution for CAD runner scripts.

Both full CAD validation (``validate_cad_job.py`` / ``cad_validation_runner.py``)
and lightweight geometry checks (``geometry_check_job.py`` /
``geometry_check_runner.py``) need to execute untrusted generated CAD source in
an isolated subprocess with a stripped environment, a bounded timeout, and
bounded captured output, then read back a JSON result file the runner script
writes. This module is the one place that sandboxing logic lives.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_CAPTURED_OUTPUT = 16_000
DEFAULT_TIMEOUT_SECONDS = 60


def bounded_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-MAX_CAPTURED_OUTPUT:]


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


def validation_timeout_seconds(
    env_var: str = "CAD_VALIDATION_TIMEOUT_SECONDS",
    default_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> int:
    value = os.environ.get(env_var, str(default_seconds))
    try:
        timeout = int(value)
    except ValueError as exc:
        raise ValueError(f"{env_var} must be an integer.") from exc
    if timeout <= 0:
        raise ValueError(f"{env_var} must be greater than zero.")
    return timeout


@dataclass(frozen=True)
class SandboxRunOutcome:
    """Raw outcome of one sandboxed runner-script invocation.

    ``result_json`` is the parsed contents of the runner's ``--result`` file,
    or ``None`` if the process timed out, the file was never written, or it
    could not be parsed -- ``result_read_error`` explains which. Callers shape
    this raw outcome into their own job-specific result envelope.
    """

    timed_out: bool
    exit_code: int | None
    stdout: str
    stderr: str
    result_json: dict | None
    result_read_error: str | None


def run_sandboxed_runner(
    runner_path: Path,
    extra_args: list[str],
    *,
    result_path: Path,
    workdir: Path,
    timeout_seconds: int,
) -> SandboxRunOutcome:
    """Run ``python -I runner_path *extra_args --result result_path`` sandboxed."""

    command = [
        sys.executable,
        "-I",
        str(runner_path),
        *extra_args,
        "--result",
        str(result_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=workdir,
            env=sanitized_validation_environment(workdir),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxRunOutcome(
            timed_out=True,
            exit_code=None,
            stdout=bounded_output(exc.stdout),
            stderr=bounded_output(exc.stderr),
            result_json=None,
            result_read_error="timeout",
        )

    stdout = bounded_output(completed.stdout)
    stderr = bounded_output(completed.stderr)
    if result_path.exists():
        try:
            result_json = json.loads(result_path.read_text(encoding="utf-8"))
            result_read_error = None
        except (json.JSONDecodeError, OSError) as exc:
            result_json = None
            result_read_error = str(exc)
    else:
        result_json = None
        result_read_error = "missing"

    return SandboxRunOutcome(
        timed_out=False,
        exit_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        result_json=result_json,
        result_read_error=result_read_error,
    )
