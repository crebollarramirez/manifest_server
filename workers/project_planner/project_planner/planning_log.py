from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


DEFAULT_LOG_DIRECTORY = Path(__file__).resolve().parent.parent / "logs"


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


class PlanningLogWriter:
    """Writes one private, human-readable text file per planning job to
    workers/project_planner/logs/<job_id>.txt -- the plan the planning
    agent actually produced (or its failed repair attempts), reviewable
    without re-running the CLI or querying Postgres directly. Mirrors
    workers/agent_3d/planning/planning_log.py's PlanningLogWriter (same
    atomic-write, 0600, reclaim-safe-by-filename shape)."""

    def __init__(self, directory: str | Path | None = None):
        configured = os.environ.get("PROJECT_PLANNER_LOG_DIRECTORY", "").strip()
        self.directory = Path(directory or configured or DEFAULT_LOG_DIRECTORY)

    def write(
        self,
        *,
        job: dict[str, Any],
        attempts: list[dict[str, Any]] | None = None,
        plan: Any = None,
        spec: Any = None,
        failure: dict[str, Any] | None = None,
    ) -> str:
        job_id = str(UUID(str(job["id"])))
        sections = [
            "Project Planner Log",
            "====================",
            f"generated_at: {datetime.now(timezone.utc).isoformat()}",
            f"job_id: {job_id}",
            f"project_id: {job.get('project_id')}",
            f"auto_publish: {job.get('auto_publish', False)}",
            "",
            "REQUEST",
            "-------",
            str(job.get("request_text") or ""),
            "",
        ]
        if attempts:
            sections += ["REPAIR ATTEMPTS", "---------------", _json(attempts), ""]
        if plan is not None:
            sections += ["PROJECT PLAN", "------------", _json(plan), ""]
        if spec is not None:
            sections += ["ASSEMBLY SPEC", "-------------", _json(spec), ""]
        if failure is not None:
            sections += ["FAILURE", "-------", _json(failure), ""]
        content = "\n".join(sections)
        return self._write_atomic(f"{job_id}.txt", content)

    def _write_atomic(self, filename: str, content: str) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / filename
        prefix = f".{filename.removesuffix('.txt')}-"

        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=prefix,
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = temporary.name
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
        except Exception:
            if temporary_path:
                try:
                    Path(temporary_path).unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        return str(path.resolve())
