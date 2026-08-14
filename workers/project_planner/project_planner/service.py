from __future__ import annotations

import logging

from .context_builder import build_existing_parts_inventory
from .contracts import ProjectPlanningInput, ProjectPlanningLimits, finalize_plan_draft
from .failures import ProjectPlanningFailure
from .planner import ProjectPlanner
from .repository import ProjectPlanningRepository
from .spec_builder import build_assembly_spec
from .validator import validate_project_plan


LOGGER = logging.getLogger(__name__)


def process_project_planning_job(
    repository: ProjectPlanningRepository,
    planner: ProjectPlanner,
    job: dict,
    *,
    limits: ProjectPlanningLimits,
    max_repair_attempts: int = 2,
) -> dict:
    """Runs the whole Step 1 pipeline for one job: build the existing-parts
    snapshot, make one LLM call, validate deterministically, repair up to
    `max_repair_attempts` times when the draft has retryable violations,
    and derive the AssemblySpec. Nothing here creates parts, queues
    edit_jobs, or starts any CAD generation -- this function has no
    knowledge of those systems.

    `max_repair_attempts` is deliberately a separate parameter, not part
    of `ProjectPlanningLimits` -- that object is serialized into the LLM's
    own input as guidance, and the model has no business knowing its own
    retry budget."""

    project_id = str(job["project_id"])
    request_text = str(job["request_text"])

    existing_parts = build_existing_parts_inventory(repository, project_id)
    planning_input = ProjectPlanningInput(
        project_id=project_id,
        request_text=request_text,
        existing_parts=existing_parts,
        planning_limits=limits,
    )

    # No prior draft exists yet, so this call stays outside the
    # draft-attaching try/except below.
    draft = planner.create_plan_draft(planning_input)
    attempts: list[dict] = []

    try:
        for attempt in range(max_repair_attempts + 1):
            if draft.clarification.required:
                # Mirrors EditWorkflowOrchestrator's handling of
                # goal.clarification.required: terminal failed job,
                # never repaired -- this is the model correctly asking a
                # question, not a bug. Checked on every attempt, including
                # post-repair drafts.
                raise ProjectPlanningFailure(
                    "PROJECT_CLARIFICATION_REQUIRED",
                    draft.clarification.question,
                    details={
                        "question": draft.clarification.question,
                        "reason": draft.clarification.reason,
                    },
                )

            violations = validate_project_plan(
                draft, existing_parts=existing_parts, limits=limits
            )

            if not violations:
                plan = finalize_plan_draft(draft)
                spec = build_assembly_spec(plan, project_id=project_id)
                LOGGER.info(
                    "project planning completed project_id=%s plan_id=%s "
                    "part_count=%s interface_count=%s design_mode=%s "
                    "repair_attempts=%s",
                    project_id,
                    plan.plan_id,
                    len(plan.parts),
                    len(plan.interfaces),
                    plan.design_mode,
                    attempt,
                )
                LOGGER.debug(
                    "project planning validated plan=%s", plan.model_dump(mode="json")
                )
                LOGGER.debug(
                    "project planning derived spec=%s", spec.model_dump(mode="json")
                )
                return {
                    "project_plan": plan.model_dump(mode="json"),
                    "assembly_spec": spec.model_dump(mode="json"),
                    "attempts": attempts,
                }

            attempts.append(
                {
                    "attempt": attempt,
                    "draft": draft.model_dump(mode="json"),
                    "violations": [v.model_dump(mode="json") for v in violations],
                }
            )

            exhausted = attempt == max_repair_attempts
            unrepairable = not any(v.retryable for v in violations)
            if exhausted or unrepairable:
                codes_summary = ", ".join(sorted({v.code for v in violations}))
                reason = (
                    "the repair budget was exhausted"
                    if exhausted
                    else "a violation was not retryable"
                )
                LOGGER.warning(
                    "project planning failed permanently project_id=%s "
                    "attempts=%s unresolved_violation_count=%s "
                    "unresolved_violation_codes=%s",
                    project_id,
                    len(attempts),
                    len(violations),
                    sorted({v.code for v in violations}),
                )
                raise ProjectPlanningFailure(
                    "PROJECT_PLAN_INVALID",
                    f"The project plan still had {len(violations)} unresolved "
                    f"violation(s) after {attempt + 1} attempt(s) because "
                    f"{reason}: {codes_summary}.",
                    details={
                        "violations": [v.model_dump(mode="json") for v in violations],
                        "attempts": attempts,
                    },
                )

            LOGGER.info(
                "project planning repair attempt=%s violation_count=%s "
                "violation_codes=%s",
                attempt,
                len(violations),
                sorted({v.code for v in violations}),
            )
            draft = planner.repair_plan_draft(
                planning_input, previous_draft=draft, violations=violations
            )
    except ProjectPlanningFailure as exc:
        # PROJECT_PLAN_INVALID already carries its own rich details
        # (violations/attempts); attaching "plan" again would be
        # redundant. Every other failure raised in this loop
        # (clarification required, or a finalize/spec-build failure)
        # gets the latest draft attached for debugging.
        if exc.code != "PROJECT_PLAN_INVALID":
            exc.details = {**exc.details, "plan": draft.model_dump(mode="json")}
        raise

    raise AssertionError(
        "process_project_planning_job loop exited without returning or raising"
    )
