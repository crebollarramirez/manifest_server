from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from pathlib import Path


# Script execution adds only workers/project_planner to sys.path. Add
# workers/ so project_planner itself is importable, and the workspace root
# so workers.indexer.indexer imports (reusing the indexer package's
# SupabaseProjectRepository/IndexGetter) work outside Docker too.
WORKERS_ROOT = str(Path(__file__).resolve().parents[1])
WORKSPACE_ROOT = str(Path(__file__).resolve().parents[2])
for _root in (WORKERS_ROOT, WORKSPACE_ROOT):
    if _root not in sys.path:
        sys.path.insert(0, _root)

from supabase import create_client

from project_planner.contracts import AssemblySpec, ProjectPlan, ProjectPlanningLimits
from project_planner.planner import ProjectPlanner
from project_planner.planning_log import PlanningLogWriter
from project_planner.publish_service import process_assembly_publish_job, run_auto_publish
from project_planner.repository import ProjectPlanningRepository
from project_planner.service import process_project_planning_job


logging.basicConfig(
    level=os.environ.get("PROJECT_PLANNER_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = float(
    os.environ.get("PROJECT_PLANNER_POLL_INTERVAL_SECONDS", "2")
)
LIMITS = ProjectPlanningLimits(
    max_parts=int(os.environ.get("PROJECT_PLANNING_MAX_PARTS", "12")),
    max_interfaces=int(os.environ.get("PROJECT_PLANNING_MAX_INTERFACES", "24")),
)
MAX_REPAIR_ATTEMPTS = int(os.environ.get("PROJECT_PLANNING_MAX_REPAIR_ATTEMPTS", "2"))

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)
repository = ProjectPlanningRepository(supabase)
planner = ProjectPlanner()
planning_log_writer = PlanningLogWriter()


def _write_planning_log(**kwargs: object) -> None:
    # Best-effort: the job's real result already lives durably in
    # project_planning_jobs (via complete_job/fail_job above), so a
    # filesystem hiccup writing this human-readable debug copy must never
    # fail an otherwise-successful (or already-recorded-failed) job --
    # unlike agent_3d's planning log, which gates CAD execution and is
    # deliberately fatal on write failure.
    try:
        planning_log_writer.write(**kwargs)
    except Exception:
        LOGGER.warning("could not write planning log", exc_info=True)


def _process_planning_job(job: dict) -> None:
    job_id = str(job["id"])
    try:
        result = process_project_planning_job(
            repository, planner, job, limits=LIMITS,
            max_repair_attempts=MAX_REPAIR_ATTEMPTS,
        )
        repository.complete_job(
            job_id,
            project_plan=result["project_plan"],
            assembly_spec=result["assembly_spec"],
        )
        _write_planning_log(
            job=job,
            attempts=result.get("attempts") or [],
            plan=result["project_plan"],
            spec=result["assembly_spec"],
        )
        print(f"project-planner[{job_id}] completed", flush=True)
    except Exception as exc:
        code = getattr(exc, "code", "PROJECT_PLANNING_FAILED")
        message = str(exc) or traceback.format_exc()
        details = getattr(exc, "details", {}) or {}
        plan = details.get("plan")
        attempts = details.get("attempts") or []
        if plan is None:
            # PROJECT_PLAN_INVALID has no top-level "plan" key -- fall
            # back to the last attempted draft so the job row still
            # gets something inspectable in project_plan.
            if attempts:
                plan = attempts[-1].get("draft")
        repository.fail_job(
            job_id, code=code, message=message, project_plan=plan,
            error_details=details or None,
        )
        _write_planning_log(
            job=job,
            attempts=attempts,
            plan=plan,
            failure={"code": code, "message": message},
        )
        print(
            f"project-planner[{job_id}] failed code={code}:\n{traceback.format_exc()}",
            file=sys.stderr,
            flush=True,
        )
        return

    if not job.get("auto_publish"):
        return

    # A failed auto-publish must never overwrite the planning job's own
    # status -- the plan itself is valid and already 'completed'; only the
    # publish attempt failed. That failure is recorded on its own
    # assembly_publish_jobs row (see run_auto_publish), inspectable via
    # get_assembly_publish_job/list_assembly_revisions -- it deliberately
    # is not fed back into repository.fail_job here.
    try:
        plan = ProjectPlan.model_validate(result["project_plan"])
        spec = AssemblySpec.model_validate(result["assembly_spec"])
        publish_result = run_auto_publish(
            repository,
            project_id=str(job["project_id"]),
            design_request_id=job_id,
            target_assembly_id=job.get("target_assembly_id"),
            plan=plan,
            spec=spec,
            limits=LIMITS,
        )
        revision = publish_result["assembly_revision"]
        print(
            f"project-planner[{job_id}] auto-published assembly_id="
            f"{revision['assembly_id']} revision={revision['revision']}",
            flush=True,
        )
    except Exception as exc:
        code = getattr(exc, "code", "ASSEMBLY_PUBLISH_FAILED")
        print(
            f"project-planner[{job_id}] auto-publish failed code={code}:\n"
            f"{traceback.format_exc()}",
            file=sys.stderr,
            flush=True,
        )


def _process_publish_job(job: dict) -> None:
    job_id = str(job["id"])
    try:
        result = process_assembly_publish_job(repository, job, limits=LIMITS)
        repository.complete_publish_job(
            job_id, assembly_revision=result["assembly_revision"]
        )
        print(f"project-planner[{job_id}] publish completed", flush=True)
    except Exception as exc:
        code = getattr(exc, "code", "ASSEMBLY_PUBLISH_FAILED")
        message = str(exc) or traceback.format_exc()
        details = getattr(exc, "details", {}) or {}
        repository.fail_publish_job(
            job_id, code=code, message=message, error_details=details or None
        )
        print(
            f"project-planner[{job_id}] publish failed code={code}:\n"
            f"{traceback.format_exc()}",
            file=sys.stderr,
            flush=True,
        )


def main() -> None:
    while True:
        # Planning jobs are the slower, LLM-bound work, so they're claimed
        # first each tick; checking an idle publish queue afterward costs
        # nothing. One worker now serves both project_planning_jobs and
        # assembly_publish_jobs -- the domain code they call was already
        # one shared package, so nothing justified two separate processes
        # once auto_publish made the explicit /publish path optional.
        job = repository.claim_next_job()
        if job is not None:
            _process_planning_job(job)
            continue

        publish_job = repository.claim_next_publish_job()
        if publish_job is not None:
            _process_publish_job(publish_job)
            continue

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
