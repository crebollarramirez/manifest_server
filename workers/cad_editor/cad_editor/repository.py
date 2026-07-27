from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from workers.indexer.indexer.repository import (
    BUCKET,
    SupabaseProjectRepository,
    cad_source_storage_path,
)

from .contracts import WorkflowFailure


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def patch_edit_job(
        self,
        edit_job_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        response = (
            self.supabase.table("edit_jobs")
            .update(values)
            .eq("id", edit_job_id)
            .select("*")
            .execute()
        )
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
    ) -> dict[str, Any]:
        job = self.edit_job(edit_job_id)
        history = list(job.get("history") or [])
        history.append({"recorded_at": _now(), **event})
        return self.patch_edit_job(edit_job_id, {"history": history})

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

    def source(self, project_id: str, part_id: str):
        return self.projects.cad_source(project_id, part_id)

    def read_text(self, storage_path: str) -> str:
        raw = self.supabase.storage.from_(BUCKET).download(storage_path)
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

    def verify_text_hash(self, storage_path: str, expected_hash: str) -> str:
        content = self.read_text(storage_path)
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual_hash != expected_hash:
            raise WorkflowFailure(
                "STORAGE_WRITE_MISMATCH",
                f"Storage verification failed for {storage_path}.",
                details={
                    "expected_hash": expected_hash,
                    "actual_hash": actual_hash,
                },
            )
        return content

    @staticmethod
    def candidate_prefix(
        project_id: str,
        part_id: str,
        edit_job_id: str,
    ) -> str:
        return f"{project_id}/candidates/cad/{part_id}/{edit_job_id}"

    def candidate_path(
        self,
        project_id: str,
        part_id: str,
        edit_job_id: str,
        attempt: int,
    ) -> str:
        return (
            f"{self.candidate_prefix(project_id, part_id, edit_job_id)}"
            f"/attempt-{attempt}/model.py"
        )

    def original_path(
        self,
        project_id: str,
        part_id: str,
        edit_job_id: str,
    ) -> str:
        return (
            f"{self.candidate_prefix(project_id, part_id, edit_job_id)}"
            "/original/model.py"
        )

    def cleanup_candidates(
        self,
        project_id: str,
        part_id: str,
        edit_job_id: str,
        max_attempts: int = 3,
    ) -> str | None:
        paths = [
            self.original_path(project_id, part_id, edit_job_id),
            *(
                self.candidate_path(project_id, part_id, edit_job_id, attempt)
                for attempt in range(1, max_attempts + 1)
            ),
        ]
        try:
            self.supabase.storage.from_(BUCKET).remove(paths)
        except Exception as exc:
            return f"Candidate cleanup failed: {exc}"
        return None

    def queue_index(self, edit_job_id: str, state: str) -> str:
        response = self.supabase.rpc(
            "queue_edit_index_build",
            {
                "p_edit_job_id": edit_job_id,
                "p_state": state,
            },
        ).execute()
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

    def queue_candidate_validation(
        self,
        *,
        edit_job_id: str,
        candidate_path: str,
        candidate_hash: str,
        attempt: int,
    ) -> str:
        response = self.supabase.rpc(
            "queue_edit_candidate_validation",
            {
                "p_edit_job_id": edit_job_id,
                "p_candidate_path": candidate_path,
                "p_candidate_sha256": candidate_hash,
                "p_attempt_count": attempt,
            },
        ).execute()
        return _scalar(response.data)

    def generation_job(self, generation_job_id: str) -> dict[str, Any]:
        response = (
            self.supabase.table("generation_jobs")
            .select("*")
            .eq("id", generation_job_id)
            .single()
            .execute()
        )
        return dict(response.data)

    def queue_export(self, edit_job_id: str, source_hash: str) -> str:
        response = self.supabase.rpc(
            "queue_edit_export",
            {
                "p_edit_job_id": edit_job_id,
                "p_source_sha256": source_hash,
            },
        ).execute()
        return _scalar(response.data)

    def complete_edit_job(
        self,
        edit_job_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return self.patch_edit_job(
            edit_job_id,
            {
                "status": "completed",
                "state": "completed",
                "result": result,
                "error_code": None,
                "error_message": None,
                "lease_expires_at": None,
                "completed_at": _now(),
            },
        )

    def fail_edit_job(
        self,
        edit_job_id: str,
        *,
        code: str,
        message: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return self.patch_edit_job(
            edit_job_id,
            {
                "status": "failed",
                "state": "failed",
                "result": result,
                "error_code": code,
                "error_message": message[:4000],
                "lease_expires_at": None,
                "completed_at": _now(),
            },
        )

    def canonical_source_path(self, project_id: str, part_id: str) -> str:
        return cad_source_storage_path(project_id, part_id)
