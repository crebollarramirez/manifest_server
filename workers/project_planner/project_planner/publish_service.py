from __future__ import annotations

import logging

from .context_builder import build_existing_parts_inventory
from .contracts import (
    AssemblyRevision,
    AssemblySpec,
    ProjectPlan,
    ProjectPlanningLimits,
)
from .digest import compute_definition_digest
from .failures import ProjectPlanningFailure
from .repository import ProjectPlanningRepository
from .validator import validate_project_plan


LOGGER = logging.getLogger(__name__)


def _validate_and_persist(
    repository: ProjectPlanningRepository,
    *,
    project_id: str,
    design_request_id: str,
    target_assembly_id: str | None,
    plan: ProjectPlan,
    spec: AssemblySpec,
    limits: ProjectPlanningLimits,
) -> dict:
    """Shared core of both publish paths: re-validate a plan against a
    freshly rebuilt existing-parts roster -> compute the content digest ->
    persist a new AssemblyRevision -> return it. Re-validating here (even
    though validate_project_plan already ran once during planning) matters
    because the world can change between planning and publishing -- a part
    referenced as kind="existing" could have been deleted in the meantime,
    even if publishing happens moments later via auto_publish. Deliberately
    reuses the AssemblySpec already produced during planning rather than
    calling build_assembly_spec() again -- that mints fresh node_id/
    interface_id/spec_id values via uuid4() on every call, so a second call
    here would silently diverge from the IDs a caller already saw."""
    existing_parts = build_existing_parts_inventory(repository, project_id)
    violations = validate_project_plan(plan, existing_parts=existing_parts, limits=limits)
    if violations:
        LOGGER.warning(
            "assembly publish rejected project_id=%s design_request_id=%s "
            "violation_count=%s violation_codes=%s",
            project_id,
            design_request_id,
            len(violations),
            sorted({v.code for v in violations}),
        )
        raise ProjectPlanningFailure(
            "ASSEMBLY_PUBLISH_PLAN_INVALID",
            f"The project plan failed canonical validation with "
            f"{len(violations)} violation(s) prior to publishing.",
            details={"violations": [v.model_dump(mode="json") for v in violations]},
        )

    definition_digest = compute_definition_digest(spec)
    row = repository.publish_revision(
        project_id=project_id,
        design_request_id=design_request_id,
        assembly_id=target_assembly_id,
        schema_version=plan.schema_version,
        definition_digest=definition_digest,
        definition_json=spec.model_dump(mode="json"),
    )

    revision = AssemblyRevision(
        revision_id=str(row["id"]),
        assembly_id=str(row["assembly_id"]),
        revision=int(row["revision"]),
        parent_revision=row["parent_revision"],
        design_request_id=str(row["design_request_id"]),
        schema_version=int(row["schema_version"]),
        definition_digest=str(row["definition_digest"]),
        definition=spec,
        created_at=str(row["created_at"]),
    )
    LOGGER.info(
        "assembly revision published project_id=%s assembly_id=%s revision=%s "
        "parent_revision=%s definition_digest=%s",
        project_id,
        revision.assembly_id,
        revision.revision,
        revision.parent_revision,
        revision.definition_digest,
    )
    return {"assembly_revision": revision.model_dump(mode="json")}


def process_assembly_publish_job(
    repository: ProjectPlanningRepository,
    job: dict,
    *,
    limits: ProjectPlanningLimits,
) -> dict:
    """The explicit /publish path: job is an already-claimed
    assembly_publish_jobs row (status='running', set by
    claim_next_assembly_publish_job()). Fetches the completed planning job
    it names, then delegates to _validate_and_persist."""
    project_id = str(job["project_id"])
    design_request_id = str(job["design_request_id"])
    target_assembly_id = job.get("target_assembly_id")

    planning_job = repository.get_completed_planning_job(project_id, design_request_id)
    plan = ProjectPlan.model_validate(planning_job["project_plan"])
    spec = AssemblySpec.model_validate(planning_job["assembly_spec"])

    return _validate_and_persist(
        repository,
        project_id=project_id,
        design_request_id=design_request_id,
        target_assembly_id=target_assembly_id,
        plan=plan,
        spec=spec,
        limits=limits,
    )


def run_auto_publish(
    repository: ProjectPlanningRepository,
    *,
    project_id: str,
    design_request_id: str,
    target_assembly_id: str | None,
    plan: ProjectPlan,
    spec: AssemblySpec,
    limits: ProjectPlanningLimits,
) -> dict:
    """The auto_publish path: called inline by project_planner_worker.py
    right after a planning job is marked completed, when the originating
    request had auto_publish=true. Plan/spec are already in memory -- no
    second queue round-trip is needed to reach them.

    Still creates (and completes/fails) its own assembly_publish_jobs row
    via create_running_publish_job, so auto-published and explicitly
    published revisions are indistinguishable in the publish history:
    list_assembly_revisions/get_assembly_publish_job callers don't need to
    know which path produced a given row. Raises on failure -- the caller
    (project_planner_worker.py's planning branch) decides how to log this
    without touching the already-completed planning job's own status."""
    publish_job = repository.create_running_publish_job(
        project_id=project_id,
        design_request_id=design_request_id,
        target_assembly_id=target_assembly_id,
    )
    try:
        result = _validate_and_persist(
            repository,
            project_id=project_id,
            design_request_id=design_request_id,
            target_assembly_id=target_assembly_id,
            plan=plan,
            spec=spec,
            limits=limits,
        )
    except ProjectPlanningFailure as exc:
        repository.fail_publish_job(
            str(publish_job["id"]),
            code=exc.code,
            message=str(exc),
            error_details=exc.details or None,
        )
        raise

    repository.complete_publish_job(
        str(publish_job["id"]), assembly_revision=result["assembly_revision"]
    )
    return result
