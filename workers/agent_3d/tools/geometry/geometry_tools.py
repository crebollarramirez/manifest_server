"""check_geometry: deterministic geometric evidence for the current candidate.

Inspects the current edit-scoped candidate's actual executed geometry and
compares it against the candidate immediately before the latest mutation. All
CadQuery/OpenCascade execution happens in the validation container via the
existing generation_jobs queue -- this tool never executes CAD source itself,
and never modifies it. See ``queue_geometry_check`` on the repository for how
the immediately-previous source version is resolved, and
``workers/cad_validator/geometry_check_job.py`` for how the job is processed.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Literal

from ...failures import WorkflowFailure
from ..base import AgentTool, StrictToolModel, ToolExecutionContext, ToolInputRejected
from ..hashing import source_hash

_TERMINAL_JOB_STATUSES = ("completed", "failed", "cancelled")


class CheckGeometryInput(StrictToolModel):
    """No caller-provided arguments.

    check_geometry always inspects the current edit-scoped candidate, which
    is already known from ``ToolExecutionContext`` -- an agent never supplies
    project, part, candidate, or source-hash identity for this tool.
    """


class GeometryBoundingBox(StrictToolModel):
    """Axis-aligned bounding box, preserving position as well as size."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]
    size: tuple[float, float, float]


class GeometryFacts(StrictToolModel):
    """Deterministic measured geometry for one exact source version.

    ``execution_ok`` and ``valid`` are deliberately distinct: a source can
    execute successfully and still produce invalid or absent geometry.
    """

    execution_ok: bool
    valid: bool | None
    volume_mm3: float | None
    bounding_box: GeometryBoundingBox | None
    center_of_mass: tuple[float, float, float] | None
    solid_count: int | None
    face_count: int | None
    edge_count: int | None


class GeometryDelta(StrictToolModel):
    """Deterministic geometric differences between two source versions.

    Any field the underlying comparison could not compute (because one side
    lacks a measurement) is ``None`` rather than a fabricated zero.
    """

    volume_mm3: float | None
    volume_percent: float | None
    bbox_changed: bool | None
    center_of_mass_distance_mm: float | None
    solid_count: int | None
    face_count: int | None
    edge_count: int | None
    validity_changed: bool | None


class CheckGeometryOutput(StrictToolModel):
    """Structured geometry-check result for the current candidate.

    ``status`` distinguishes a completed check from every expected failure
    mode (the candidate could not be read, the geometry-check job failed, or
    it did not finish before its bounded timeout) so the agent can observe
    the specific reason rather than a generic tool failure.
    """

    status: Literal["completed", "source_not_found", "job_failed", "timeout"]
    source_hash: str | None
    previous_source_hash: str | None
    geometry: GeometryFacts | None
    delta: GeometryDelta | None
    warnings: tuple[str, ...]
    message: str | None = None


def _candidate_source_path(context: ToolExecutionContext) -> str:
    return f"{context.project_id}/candidates/cad/{context.part_id}/{context.candidate_id}/model.py"


def _vector3(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    return (float(value[0]), float(value[1]), float(value[2]))


def _bounding_box(value: Any) -> GeometryBoundingBox | None:
    if not isinstance(value, dict):
        return None
    bounds = (_vector3(value.get("min")), _vector3(value.get("max")), _vector3(value.get("size")))
    if any(bound is None for bound in bounds):
        return None
    return GeometryBoundingBox(min=bounds[0], max=bounds[1], size=bounds[2])


def _geometry_facts(value: Any) -> GeometryFacts | None:
    if not isinstance(value, dict):
        return None
    return GeometryFacts(
        execution_ok=bool(value.get("execution_ok")),
        valid=value.get("geometry_valid"),
        volume_mm3=value.get("volume_mm3"),
        bounding_box=_bounding_box(value.get("bounding_box")),
        center_of_mass=_vector3(value.get("center_of_mass")),
        solid_count=value.get("solid_count"),
        face_count=value.get("face_count"),
        edge_count=value.get("edge_count"),
    )


def _geometry_delta(value: Any) -> GeometryDelta | None:
    if not isinstance(value, dict):
        return None
    return GeometryDelta(
        volume_mm3=value.get("volume_mm3"),
        volume_percent=value.get("volume_percent"),
        bbox_changed=value.get("bbox_changed"),
        center_of_mass_distance_mm=value.get("center_of_mass_distance_mm"),
        solid_count=value.get("solid_count"),
        face_count=value.get("face_count"),
        edge_count=value.get("edge_count"),
        validity_changed=value.get("validity_changed"),
    )


def _warnings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _failure(
    status: Literal["source_not_found", "job_failed", "timeout"],
    *,
    source_hash_value: str | None,
    message: str,
) -> CheckGeometryOutput:
    return CheckGeometryOutput(
        status=status,
        source_hash=source_hash_value,
        previous_source_hash=None,
        geometry=None,
        delta=None,
        warnings=(),
        message=message,
    )


class CheckGeometryTool(AgentTool[CheckGeometryInput, CheckGeometryOutput]):
    """Inspect the current candidate's actual geometry and its immediate delta.

    check_geometry inspects the current edit-scoped candidate's actual
    executed geometry -- volume, bounding box, center of mass, and
    solid/face/edge counts -- by running it in the validation container, and
    compares it against the candidate immediately before the latest
    mutation. It never modifies CAD source. The result is deterministic
    geometric evidence only: it reports what changed, not whether that
    change matches the intent behind the last mutation -- use it to confirm
    a mutation actually had a geometric effect before trusting that it did.
    """

    tool_id = "check_geometry"
    version = 1
    description = (
        "Inspect the current edit-scoped candidate's actual executed geometry "
        "(volume, bounding box, center of mass, solid/face/edge counts) and "
        "compare it against the candidate immediately before the latest "
        "mutation. Runs the candidate in the validation container; never "
        "modifies CAD source. Returns deterministic geometric facts and "
        "warnings only -- it does not judge whether the change matches your "
        "intent. Call this after a mutation to confirm it actually had a "
        "geometric effect (for example, a volume delta of zero after "
        "attempting to cut a cavity means the mutation likely had no effect)."
    )
    input_model = CheckGeometryInput
    output_model = CheckGeometryOutput

    def __init__(
        self,
        *,
        timeout_seconds: float = 45.0,
        poll_interval_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._sleep = sleep
        self._monotonic = monotonic

    async def validate_input(
        self,
        _tool_input: CheckGeometryInput,
        context: ToolExecutionContext,
    ) -> None:
        if not context.candidate_id:
            raise ToolInputRejected(
                "check_geometry requires an edit-scoped candidate."
            )

    async def execute(
        self,
        _tool_input: CheckGeometryInput,
        context: ToolExecutionContext,
    ) -> CheckGeometryOutput:
        candidate_path = _candidate_source_path(context)
        try:
            source = context.services.repository.read_text(candidate_path)
        except WorkflowFailure as exc:
            return _failure(
                "source_not_found", source_hash_value=None, message=str(exc)
            )

        content_hash = source_hash(source)
        job_id = context.services.repository.queue_geometry_check(
            str(context.candidate_id), content_hash
        )

        deadline = self._monotonic() + self._timeout_seconds
        job = context.services.repository.generation_job(job_id)
        while str(job.get("status") or "") not in _TERMINAL_JOB_STATUSES:
            if self._monotonic() >= deadline:
                return _failure(
                    "timeout",
                    source_hash_value=content_hash,
                    message="Geometry check did not complete before its timeout.",
                )
            self._sleep(self._poll_interval_seconds)
            job = context.services.repository.generation_job(job_id)

        report = job.get("result") if isinstance(job.get("result"), dict) else {}
        source_matches = str(job.get("source_sha256") or "") == content_hash
        if job.get("status") != "completed" or not source_matches:
            message = str(
                job.get("error_message")
                or report.get("error_message")
                or "Geometry check job did not complete successfully."
            )
            return _failure(
                "job_failed", source_hash_value=content_hash, message=message
            )

        previous_hash = report.get("previous_source_sha256")
        return CheckGeometryOutput(
            status="completed",
            source_hash=content_hash,
            previous_source_hash=(
                str(previous_hash) if isinstance(previous_hash, str) else None
            ),
            geometry=_geometry_facts(report.get("geometry")),
            delta=_geometry_delta(report.get("delta")),
            warnings=_warnings(report.get("warnings")),
            message=None,
        )
