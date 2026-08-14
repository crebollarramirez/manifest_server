from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from workers.indexer.indexer.repository import SupabaseProjectRepository

from .failures import ProjectPlanningFailure


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectPlanningRepository:
    def __init__(self, supabase: Any):
        self.supabase = supabase
        self.projects = SupabaseProjectRepository(supabase)

    def claim_next_job(self) -> dict | None:
        result = self.supabase.rpc("claim_next_project_planning_job").execute()
        return result.data[0] if result.data else None

    def complete_job(
        self, job_id: str, *, project_plan: dict, assembly_spec: dict
    ) -> None:
        (
            self.supabase.table("project_planning_jobs")
            .update(
                {
                    "status": "completed",
                    "project_plan": project_plan,
                    "assembly_spec": assembly_spec,
                    "error_code": None,
                    "error_message": None,
                    "error_details": None,
                    "completed_at": _now(),
                }
            )
            .eq("id", job_id)
            .execute()
        )

    def fail_job(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
        project_plan: dict | None = None,
        error_details: dict | None = None,
    ) -> None:
        (
            self.supabase.table("project_planning_jobs")
            .update(
                {
                    "status": "failed",
                    "error_code": code,
                    "error_message": message[-4000:],
                    "project_plan": project_plan,
                    "error_details": error_details,
                    "completed_at": _now(),
                }
            )
            .eq("id", job_id)
            .execute()
        )

    # --- assembly publishing (Phase 2) -----------------------------------

    def claim_next_publish_job(self) -> dict | None:
        result = self.supabase.rpc("claim_next_assembly_publish_job").execute()
        return result.data[0] if result.data else None

    def create_running_publish_job(
        self, *, project_id: str, design_request_id: str, target_assembly_id: str | None
    ) -> dict:
        """Inserts an assembly_publish_jobs row already 'running', for the
        auto_publish path -- there's no queued interval to wait through
        since the worker is about to process it inline, in the same tick
        that just completed planning. The partial unique indexes that
        normally stop two concurrent explicit /publish calls from racing
        also protect this path (e.g. against an explicit /publish call
        racing an auto_publish for the same target_assembly_id)."""
        try:
            result = (
                self.supabase.table("assembly_publish_jobs")
                .insert(
                    {
                        "project_id": project_id,
                        "design_request_id": design_request_id,
                        "target_assembly_id": target_assembly_id,
                        "status": "running",
                        "started_at": _now(),
                    }
                )
                .execute()
            )
        except Exception as exc:
            if str(getattr(exc, "code", "") or "") == "23505":
                raise ProjectPlanningFailure(
                    "ASSEMBLY_PUBLISH_ALREADY_IN_PROGRESS",
                    "A publish job is already queued or running for this "
                    "assembly or design request.",
                ) from exc
            raise
        rows = result.data or []
        if not rows:
            raise ProjectPlanningFailure(
                "ASSEMBLY_PUBLISH_FAILED",
                "Could not create an assembly publish job row.",
            )
        return rows[0]

    def get_completed_planning_job(self, project_id: str, design_request_id: str) -> dict:
        result = (
            self.supabase.table("project_planning_jobs")
            .select("id, project_id, status, project_plan, assembly_spec")
            .eq("project_id", project_id)
            .eq("id", design_request_id)
            .execute()
        )
        rows = result.data or []
        job = rows[0] if rows else None
        if job is None:
            raise ProjectPlanningFailure(
                "ASSEMBLY_PUBLISH_DESIGN_REQUEST_NOT_FOUND",
                f'No project planning job was found with id "{design_request_id}".',
            )
        if job["status"] != "completed":
            raise ProjectPlanningFailure(
                "ASSEMBLY_PUBLISH_DESIGN_REQUEST_NOT_COMPLETED",
                f'Project planning job "{design_request_id}" is '
                f'"{job["status"]}", not "completed".',
            )
        return job

    def publish_revision(
        self,
        *,
        project_id: str,
        design_request_id: str,
        assembly_id: str | None,
        schema_version: int,
        definition_digest: str,
        definition_json: dict,
    ) -> dict:
        result = self.supabase.rpc(
            "publish_assembly_revision",
            {
                "p_project_id": project_id,
                "p_design_request_id": design_request_id,
                "p_assembly_id": assembly_id,
                "p_schema_version": schema_version,
                "p_definition_digest": definition_digest,
                "p_definition_json": definition_json,
            },
        ).execute()
        row = result.data[0] if isinstance(result.data, list) else result.data
        if not row:
            raise ProjectPlanningFailure(
                "ASSEMBLY_PUBLISH_FAILED",
                "publish_assembly_revision did not return a row.",
            )
        return row

    def complete_publish_job(self, job_id: str, *, assembly_revision: dict) -> None:
        (
            self.supabase.table("assembly_publish_jobs")
            .update(
                {
                    "status": "completed",
                    "assembly_revision": assembly_revision,
                    "error_code": None,
                    "error_message": None,
                    "error_details": None,
                    "completed_at": _now(),
                }
            )
            .eq("id", job_id)
            .execute()
        )

    def fail_publish_job(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
        error_details: dict | None = None,
    ) -> None:
        (
            self.supabase.table("assembly_publish_jobs")
            .update(
                {
                    "status": "failed",
                    "error_code": code,
                    "error_message": message[-4000:],
                    "error_details": error_details,
                    "completed_at": _now(),
                }
            )
            .eq("id", job_id)
            .execute()
        )
