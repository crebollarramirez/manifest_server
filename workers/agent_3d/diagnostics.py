"""How validator diagnostics are shown to the model, in one place.

Two paths report a validator's findings to the agent: a step's completion
gate (via ``validate_candidate``) and the ``check_geometry`` tool. Both start
from the same CAD-validator diagnostic shape, and both must present it the
same way -- a bug the agent can act on should not read differently depending
on which path happened to notice it.

Keeping the projection and the message format here rather than in either
caller is deliberate. The bounds are a real constraint, not decoration:
diagnostics reach both the model's context and ``edit_jobs.history``, which
has no size cap, so a copy of this logic that drifted would either flood one
of those or silently under-report.
"""

from __future__ import annotations

from typing import Any


# Enough to describe what broke without turning one bad build into a wall of
# text. The CAD validator short-circuits at the first failing stage, so a
# report rarely carries more than a handful of genuinely distinct problems.
MAX_DIAGNOSTICS = 8
MAX_DIAGNOSTIC_MESSAGE = 600

# Fields carried through when present. Everything here is something the
# validator measured or located; nothing is inferred.
_OPTIONAL_FIELDS = ("stage", "line", "column", "function_name", "semantic_id")


def bounded_diagnostics(raw: Any) -> list[dict[str, Any]]:
    """Project raw validator diagnostics into a bounded, model-safe list.

    Only fields the validator actually produces are carried through -- this
    never invents a diagnostic fact. Anything unrecognizable is dropped
    rather than guessed at.
    """

    if not isinstance(raw, list):
        return []
    bounded: list[dict[str, Any]] = []
    for entry in raw[:MAX_DIAGNOSTICS]:
        if not isinstance(entry, dict):
            continue
        projected: dict[str, Any] = {
            "error_code": str(entry.get("error_code") or "VALIDATION_ERROR"),
            "message": str(entry.get("message") or "")[:MAX_DIAGNOSTIC_MESSAGE],
        }
        for key in _OPTIONAL_FIELDS:
            if entry.get(key) is not None:
                projected[key] = entry[key]
        related = entry.get("related_symbols")
        if isinstance(related, list) and related:
            projected["related_symbols"] = [str(item) for item in related[:8]]
        bounded.append(projected)
    return bounded


def diagnostics_message(lead: str, diagnostics: list[dict[str, Any]]) -> str:
    """Render diagnostics as a lead sentence followed by one line each.

    Location is included inline because it is what turns "something is
    wrong" into "this function, this line" -- the difference between a
    targeted correction and guesswork.
    """

    lines = []
    for item in diagnostics:
        where = ""
        if item.get("function_name"):
            where = f" in {item['function_name']}"
        if item.get("line") is not None:
            where += f" (line {item['line']})"
        lines.append(f"- {item['error_code']}{where}: {item['message']}")
    return "\n".join([lead, *lines]) if lines else lead
