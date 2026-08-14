from __future__ import annotations

from typing import Any

from workers.commons.storage import BUCKET
from workers.indexer.indexer.repository import SupabaseProjectRepository

from .failures import WorkflowFailure


def _scalar(data: Any) -> str:
    if isinstance(data, list):
        if not data:
            raise RuntimeError("RPC returned no value.")
        data = data[0]
    if isinstance(data, dict):
        if len(data) != 1:
            raise RuntimeError("RPC returned an unexpected object.")
        data = next(iter(data.values()))
    if data is None:
        raise RuntimeError("RPC returned no value.")
    return str(data)


def _row(data: Any, label: str) -> dict[str, Any]:
    current = data[0] if isinstance(data, list) and data else data
    if not isinstance(current, dict):
        raise RuntimeError(f"{label} returned an unexpected value.")
    return dict(current)


def _lease_failure(error: Exception) -> WorkflowFailure | None:
    if "EDIT_LEASE_LOST" not in str(error):
        return None
    return WorkflowFailure(
        "EDIT_LEASE_LOST",
        "The editor worker no longer owns this edit job.",
    )


def _storage_object_missing(error: Exception) -> bool:
    status = getattr(error, "status_code", None) or getattr(error, "statusCode", None)
    code = str(getattr(error, "code", "")).lower()
    message = str(error).lower()
    return (
        str(status) == "404"
        or code in {"404", "not_found", "nosuchkey"}
        or "object not found" in message
        or "no such object" in message
    )


def _postgres_error_code(error: Exception) -> str:
    return str(getattr(error, "code", "") or "")


class SupabaseEditRepository:
    def __init__(self, supabase: Any):
        self.supabase = supabase
        self.projects = SupabaseProjectRepository(supabase)

    def edit_job(self, edit_job_id: str) -> dict[str, Any]:
        response = (
            self.supabase.table("edit_jobs")
            .select("*")
            .eq("id", edit_job_id)
            .single()
            .execute()
        )
        if not response.data:
            raise WorkflowFailure(
                "EDIT_JOB_MISSING",
                f'Edit job "{edit_job_id}" no longer exists.',
            )
        return dict(response.data)

    def claim_next_edit_job(
        self,
        worker_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        response = self.supabase.rpc(
            "claim_next_edit_job",
            {
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        ).execute()
        if not response.data:
            return None
        return _row(response.data, "claim_next_edit_job")

    def patch_edit_job(
        self,
        edit_job_id: str,
        values: dict[str, Any],
        *,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        if worker_id is not None:
            try:
                response = self.supabase.rpc(
                    "patch_edit_job_owned",
                    {
                        "p_edit_job_id": edit_job_id,
                        "p_worker_id": worker_id,
                        "p_patch": values,
                    },
                ).execute()
            except Exception as exc:
                if failure := _lease_failure(exc):
                    raise failure from exc
                raise
            return _row(response.data, "patch_edit_job_owned")

        query = (
            self.supabase.table("edit_jobs")
            .update(values)
            .eq("id", edit_job_id)
        )
        response = query.select("*").execute()
        if not response.data:
            raise RuntimeError(f'Edit job "{edit_job_id}" could not be updated.')
        if not isinstance(response.data, list) or len(response.data) != 1:
            raise RuntimeError(
                f'Edit job "{edit_job_id}" update returned an unexpected result.'
            )
        return dict(response.data[0])

    def append_history(
        self,
        edit_job_id: str,
        event: dict[str, Any],
        *,
        worker_id: str,
    ) -> dict[str, Any]:
        try:
            response = self.supabase.rpc(
                "append_edit_job_history_owned",
                {
                    "p_edit_job_id": edit_job_id,
                    "p_worker_id": worker_id,
                    "p_event": event,
                },
            ).execute()
        except Exception as exc:
            if failure := _lease_failure(exc):
                raise failure from exc
            raise
        return _row(response.data, "append_edit_job_history_owned")

    def append_event(
        self,
        edit_job_id: str,
        *,
        worker_id: str,
        event_type: str,
        state: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self.supabase.rpc(
                "append_edit_job_event_owned",
                {
                    "p_edit_job_id": edit_job_id,
                    "p_worker_id": worker_id,
                    "p_event_type": event_type,
                    "p_state": state,
                    "p_message": message[:500],
                    "p_metadata": metadata or {},
                },
            ).execute()
        except Exception as exc:
            if failure := _lease_failure(exc):
                raise failure from exc
            raise
        return _row(response.data, "append_edit_job_event_owned")

    def heartbeat(
        self,
        edit_job_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        response = self.supabase.rpc(
            "heartbeat_edit_job",
            {
                "p_edit_job_id": edit_job_id,
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        ).execute()
        if response.data is not True:
            raise WorkflowFailure(
                "EDIT_LEASE_LOST",
                "The editor worker no longer owns this edit job.",
            )

    def getter(self, project_id: str):
        return self.projects.create_getter(project_id)

    def find_part_by_name(self, project_id: str, part_name: str) -> dict[str, Any] | None:
        response = self.supabase.rpc(
            "find_part_by_name",
            {"p_project_id": project_id, "p_part_name": part_name},
        ).execute()
        if not response.data:
            return None
        return _row(response.data, "find_part_by_name")

    def create_part(self, project_id: str, part_name: str, part_type: str) -> dict[str, Any]:
        try:
            response = (
                self.supabase.table("parts")
                .insert(
                    {
                        "project_id": project_id,
                        "part_name": part_name,
                        "part_type": part_type,
                    }
                )
                .execute()
            )
        except Exception as exc:
            code = _postgres_error_code(exc)
            if code == "23505":
                raise WorkflowFailure(
                    "PART_EXISTS",
                    f'A part named "{part_name}" already exists in this project.',
                ) from exc
            if code == "23503":
                raise WorkflowFailure(
                    "PROJECT_NOT_FOUND",
                    "The linked project no longer exists.",
                ) from exc
            raise
        return _row(response.data, "create_part")

    def delete_part(self, part_id: str) -> None:
        self.supabase.table("parts").delete().eq("id", part_id).execute()

    def read_text(self, storage_path: str) -> str:
        try:
            raw = self.supabase.storage.from_(BUCKET).download(storage_path)
        except Exception as exc:
            if _storage_object_missing(exc):
                raise WorkflowFailure(
                    "SOURCE_MISSING",
                    f"{storage_path} was not found in project storage.",
                ) from exc
            raise
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkflowFailure(
                "SOURCE_ENCODING_ERROR",
                f"{storage_path} is not valid UTF-8.",
            ) from exc

    def write_text(self, storage_path: str, content: str) -> None:
        self.supabase.storage.from_(BUCKET).upload(
            path=storage_path,
            file=content.encode("utf-8"),
            file_options={
                "content-type": "text/x-python",
                "upsert": "true",
            },
        )

    def queue_index(
        self,
        edit_job_id: str,
        state: str,
        *,
        worker_id: str,
    ) -> str:
        try:
            response = self.supabase.rpc(
                "queue_edit_index_build_owned",
                {
                    "p_edit_job_id": edit_job_id,
                    "p_worker_id": worker_id,
                    "p_state": state,
                },
            ).execute()
        except Exception as exc:
            if failure := _lease_failure(exc):
                raise failure from exc
            raise
        return _scalar(response.data)

    def index_job(self, index_job_id: str) -> dict[str, Any]:
        response = (
            self.supabase.table("index_jobs")
            .select("*")
            .eq("id", index_job_id)
            .single()
            .execute()
        )
        return dict(response.data)

    def generation_job(self, generation_job_id: str) -> dict[str, Any]:
        response = (
            self.supabase.table("generation_jobs")
            .select("*")
            .eq("id", generation_job_id)
            .single()
            .execute()
        )
        return dict(response.data)

    def queue_validation_run(
        self,
        edit_job_id: str,
        candidate_path: str,
        candidate_sha256: str,
        validation_run: int,
        *,
        worker_id: str,
    ) -> str:
        """Queue one candidate validation run and return its child job ID.

        Replaces :meth:`queue_validation` for the agent loop. The run counter
        it allocates against is uncapped, so a job may validate once per plan
        step rather than only at its commit attempts. Re-queuing the same
        ``(path, hash)`` pair returns the existing child rather than a
        duplicate -- including when that child already failed.
        """

        try:
            response = self.supabase.rpc(
                "queue_edit_candidate_validation_run_owned",
                {
                    "p_edit_job_id": edit_job_id,
                    "p_worker_id": worker_id,
                    "p_candidate_path": candidate_path,
                    "p_candidate_sha256": candidate_sha256,
                    "p_validation_run": validation_run,
                },
            ).execute()
        except Exception as exc:
            if failure := _lease_failure(exc):
                raise failure from exc
            raise
        return _scalar(response.data)

    def queue_geometry_check(self, edit_job_id: str, candidate_sha256: str) -> str:
        response = self.supabase.rpc(
            "queue_geometry_check",
            {
                "p_edit_job_id": edit_job_id,
                "p_candidate_sha256": candidate_sha256,
            },
        ).execute()
        return _scalar(response.data)

    def queue_export(
        self,
        edit_job_id: str,
        source_sha256: str,
        *,
        worker_id: str,
    ) -> str:
        try:
            response = self.supabase.rpc(
                "queue_edit_export_owned",
                {
                    "p_edit_job_id": edit_job_id,
                    "p_worker_id": worker_id,
                    "p_source_sha256": source_sha256,
                },
            ).execute()
        except Exception as exc:
            if failure := _lease_failure(exc):
                raise failure from exc
            raise
        return _scalar(response.data)

    def complete_edit_job(
        self,
        edit_job_id: str,
        result: dict[str, Any],
        *,
        worker_id: str,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_target = result.get("resolved_target")
        metadata = dict(resolved_target) if isinstance(resolved_target, dict) else {}
        for key in ("changed_symbols", "index_job_id", "export_job_id"):
            if key in result:
                metadata[key] = result[key]
        try:
            response = self.supabase.rpc(
                "finalize_edit_job_owned",
                {
                    "p_edit_job_id": edit_job_id,
                    "p_worker_id": worker_id,
                    "p_status": "completed",
                    "p_result": result,
                    "p_error_code": None,
                    "p_error_message": None,
                    "p_event_message": str(result.get("message") or "CAD edit completed.")[:500],
                    "p_event_metadata": metadata,
                    "p_metrics": metrics or {},
                },
            ).execute()
        except Exception as exc:
            if failure := _lease_failure(exc):
                raise failure from exc
            raise
        return _row(response.data, "finalize_edit_job_owned")

    def fail_edit_job(
        self,
        edit_job_id: str,
        *,
        code: str,
        message: str,
        result: dict[str, Any],
        worker_id: str,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self.supabase.rpc(
                "finalize_edit_job_owned",
                {
                    "p_edit_job_id": edit_job_id,
                    "p_worker_id": worker_id,
                    "p_status": "failed",
                    "p_result": result,
                    "p_error_code": code,
                    "p_error_message": message[:4000],
                    "p_event_message": message[:500],
                    "p_event_metadata": {"error_code": code},
                    "p_metrics": metrics or {},
                },
            ).execute()
        except Exception as exc:
            if failure := _lease_failure(exc):
                raise failure from exc
            raise
        return _row(response.data, "finalize_edit_job_owned")
