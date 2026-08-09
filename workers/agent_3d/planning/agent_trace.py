from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_LOG_DIRECTORY = Path(__file__).resolve().parent.parent / "logs"


def content_sha256(value: str) -> str:
    """Return a deterministic content hash for a prompt/tool-catalog string.

    Used for lightweight version diagnostics (has this instructions text or
    tool catalog changed between two traces?) without a prompt-management
    system -- a stable hash is sufficient to make traces comparable.
    """

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentTraceContext:
    """Correlation identifiers one Agent3D reasoning round needs but cannot
    derive from ``goal``/``plan``/``active_step`` alone.

    ``goal_id``, ``plan_id``, and ``step_id`` already come from the workflow
    state arguments already passed to ``start_step``/``continue_step``; only
    the edit job identity and the orchestrator's own turn counters are new.

    ``agent_turn`` counts model calls across the whole workflow run.
    ``step_attempt`` counts how many separate reasoning chains have been
    started for the active step (normally 1; increments only if a new chain
    must start for the same step, e.g. after a worker-process restart mid-
    step). ``reasoning_round`` counts model calls within the current chain,
    resetting to 1 whenever a new chain starts.
    """

    edit_job_id: str
    agent_turn: int
    step_attempt: int
    reasoning_round: int


def _json_safe(value: Any) -> Any:
    """Recursively convert ``value`` into a JSON-serializable structure.

    Handles Pydantic models (``model_dump``) and attribute-bag objects such
    as ``SimpleNamespace`` (used throughout this codebase's model-client test
    doubles), in addition to plain containers. ``json.dumps(..., default=str)``
    is the final fallback for anything still unrecognized after this pass.
    """

    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except TypeError:
            return _json_safe(value.model_dump())
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return value


class AgentTraceWriter:
    """Append-only structured trace of Agent3D's actual runtime execution.

    One JSONL file per edit job (``{edit_job_id}.trace.jsonl``): each line is
    one self-contained event -- the exact LLM request payload, the raw model
    response, tool execution, and orchestrator-owned state transitions --
    carrying enough correlation IDs (``edit_job_id``, ``goal_id``, ``plan_id``,
    ``step_id``, ``agent_turn``, ``step_attempt``, ``reasoning_round``,
    ``previous_response_id``, ``response_id``, ``tool_call_id``) to
    reconstruct one reasoning chain end to end. This is debugging
    infrastructure, not a chain-of-thought log: a model response may include
    an API-provided ``reasoning_summary``, never private reasoning tokens.

    Writing is centralized here; call sites only assemble one flat dict of
    fields per event. The file is opened with ``0600`` permissions at
    creation (private -- request/observation text may be sensitive) and each
    write is flushed and fsynced so a crash mid-turn cannot corrupt or lose
    prior lines.
    """

    def __init__(self, directory: str | Path | None = None):
        configured = os.environ.get("CAD_EDITOR_LOG_DIRECTORY", "").strip()
        self.directory = Path(directory or configured or DEFAULT_LOG_DIRECTORY)
        self._lock = threading.Lock()

    def log(self, event: str, *, edit_job_id: str, **fields: Any) -> str:
        """Append one structured event for ``edit_job_id``; return the trace path.

        Raises on write failure -- callers decide whether that failure should
        be mandatory (fail the workflow) or best-effort (log a warning and
        continue), since the right answer differs by call site.
        """

        record = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "edit_job_id": edit_job_id,
            **fields,
        }
        line = json.dumps(_json_safe(record), ensure_ascii=False, default=str)

        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{edit_job_id}.trace.jsonl"
        with self._lock:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return str(path.resolve())
