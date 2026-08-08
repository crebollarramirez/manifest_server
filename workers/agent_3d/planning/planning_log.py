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
    """Write private, human-readable planning/reasoning artifacts per edit job."""

    def __init__(self, directory: str | Path | None = None):
        configured = os.environ.get("CAD_EDITOR_LOG_DIRECTORY", "").strip()
        self.directory = Path(directory or configured or DEFAULT_LOG_DIRECTORY)

    def write(self, *, job: dict[str, Any], goal: Any, plan: Any) -> str:
        edit_job_id = str(UUID(str(job["id"])))
        content = "\n".join(
            [
                "CAD Editor Planning Log",
                "=======================",
                f"generated_at: {datetime.now(timezone.utc).isoformat()}",
                f"edit_job_id: {edit_job_id}",
                f"project_id: {job.get('project_id')}",
                f"requested_part_id: {job.get('requested_part_id')}",
                f"resolved_part_id: {job.get('resolved_part_id')}",
                f"workflow_mode: {job.get('workflow_mode')}",
                "",
                "REQUEST",
                "-------",
                str(job.get("request_text") or ""),
                "",
                "CONVERSATION",
                "------------",
                _json(list(job.get("messages") or [])),
                "",
                "GOAL",
                "----",
                _json(goal),
                "",
                "HIGH-LEVEL PLAN",
                "---------------",
                _json(plan),
                "",
            ]
        )
        return self._write_atomic(f"{edit_job_id}.txt", content)

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
