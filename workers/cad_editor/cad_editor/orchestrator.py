from __future__ import annotations

import os
import time
import traceback
from dataclasses import replace
from typing import Any, Callable

from .applier import apply_edit_plan
from .context_builder import build_edit_context, build_repair_context
from .contracts import (
    CandidateSource,
    EditPlan,
    InitialCadDesignContext,
    InitialCadModel,
    InitialCadRepairContext,
    ResolvedEditTarget,
    WorkflowFailure,
)
from .error_classifier import classify_validation_error
from .resolver import resolve_edit_target
from .targets import semantic_ids_in_source, source_hash


TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
POST_COMMIT_STATES = {"committing", "reindexing", "queueing_export"}
CAD_RUNTIME_IMPORT = "from cadquery_runtime import cad_part, cq, dataclass"
BLANK_CAD_SOURCE = f"{CAD_RUNTIME_IMPORT}\n"


class EditWorkflowOrchestrator:
    def __init__(
        self,
        repository,
        agent,
        *,
        worker_id: str,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.repository = repository
        self.agent = agent
        self.worker_id = worker_id
        self.sleep = sleep
        self.monotonic = monotonic
        self.poll_interval = float(
            os.environ.get("CAD_EDITOR_POLL_INTERVAL_SECONDS", "0.5")
        )
        self.dependency_timeout = float(
            os.environ.get("CAD_EDITOR_DEPENDENCY_TIMEOUT_SECONDS", "300")
        )
        self.lease_seconds = int(os.environ.get("CAD_EDITOR_LEASE_SECONDS", "300"))

    def _heartbeat(self, edit_job_id: str) -> None:
        self.repository.heartbeat(
            edit_job_id,
            self.worker_id,
            self.lease_seconds,
        )

    def _set_state(
        self,
        edit_job_id: str,
        state: str,
        **values: Any,
    ) -> dict[str, Any]:
        job = self.repository.patch_edit_job(
            edit_job_id,
            {"state": state, **values},
        )
        self._heartbeat(edit_job_id)
        return job

    def _wait_for_job(
        self,
        edit_job_id: str,
        dependency_id: str,
        fetch: Callable[[str], dict[str, Any]],
        label: str,
    ) -> dict[str, Any]:
        deadline = self.monotonic() + self.dependency_timeout
        while True:
            dependency = fetch(dependency_id)
            status = str(dependency.get("status") or "")
            if status in TERMINAL_JOB_STATUSES:
                return dependency
            if self.monotonic() >= deadline:
                raise WorkflowFailure(
                    "DEPENDENCY_TIMEOUT",
                    f"{label} job {dependency_id} did not finish in time.",
                )
            self._heartbeat(edit_job_id)
            self.sleep(self.poll_interval)

    def _ensure_fresh_getter(
        self,
        job: dict[str, Any],
        *,
        state: str = "ensuring_index",
        force_build: bool = False,
    ):
        getter = None
        try:
            getter = self.repository.getter(str(job["project_id"]))
        except (ValueError, RuntimeError):
            pass
        if (
            not force_build
            and getter is not None
            and getter.freshness()["status"] == "fresh"
        ):
            return getter

        edit_job_id = str(job["id"])
        self._set_state(edit_job_id, state)
        index_job_id = self.repository.queue_index(edit_job_id, state)
        index_job = self._wait_for_job(
            edit_job_id,
            index_job_id,
            self.repository.index_job,
            "Index",
        )
        if index_job["status"] != "completed":
            raise WorkflowFailure(
                "INDEX_BUILD_FAILED",
                str(index_job.get("error_message") or "Project indexing failed."),
                details={"index_job_id": index_job_id},
            )
        job = self.repository.patch_edit_job(
            edit_job_id,
            {"index_job_id": None},
        )
        getter = self.repository.getter(str(job["project_id"]))
        freshness = getter.freshness()
        if freshness["status"] != "fresh":
            raise WorkflowFailure(
                "STALE_INDEX",
                "The project index is still stale after rebuilding.",
                details=freshness,
            )
        return getter

    @staticmethod
    def _target_from_job(job: dict[str, Any]) -> ResolvedEditTarget | None:
        records = job.get("resolved_targets") or []
        if not records:
            return None
        record = records[0]
        return ResolvedEditTarget(
            part_id=str(record["part_id"]),
            part_name=str(record["part_name"]),
            semantic_ids=[str(value) for value in record["semantic_ids"]],
            confidence=float(record.get("confidence") or 0.0),
            reason=str(record.get("reason") or "Persisted target selection."),
            candidates=list(record.get("candidates") or []),
        )

    def _resolve_target(
        self,
        job: dict[str, Any],
        getter,
    ) -> tuple[dict[str, Any], ResolvedEditTarget]:
        target = self._target_from_job(job)
        if target is not None:
            return job, target

        edit_job_id = str(job["id"])
        self._set_state(edit_job_id, "resolving_target")
        target = resolve_edit_target(
            getter,
            self.agent,
            str(job["request_text"]),
            requested_part_id=(
                str(job["requested_part_id"])
                if job.get("requested_part_id")
                else None
            ),
        )
        target_record = {
            "part_id": target.part_id,
            "part_name": target.part_name,
            "semantic_ids": target.semantic_ids,
            "confidence": target.confidence,
            "reason": target.reason,
            "candidates": target.candidates,
        }
        try:
            job = self.repository.patch_edit_job(
                edit_job_id,
                {
                    "resolved_part_id": target.part_id,
                    "resolved_targets": [target_record],
                },
            )
        except Exception as exc:
            raise WorkflowFailure(
                "PART_EDIT_IN_PROGRESS",
                "Another active edit already owns the resolved CAD part.",
            ) from exc
        self.repository.append_history(
            edit_job_id,
            {
                "event": "target_resolved",
                "part_id": target.part_id,
                "semantic_ids": target.semantic_ids,
                "confidence": target.confidence,
                "reason": target.reason,
            },
        )
        return job, target

    def _prepare_accepted_source(
        self,
        job: dict[str, Any],
        target: ResolvedEditTarget,
        *,
        indexed_hash: str,
    ):
        edit_job_id = str(job["id"])
        source = self.repository.source(str(job["project_id"]), target.part_id)
        if source.content_hash != indexed_hash:
            raise WorkflowFailure(
                "SOURCE_CHANGED",
                "Accepted source changed after the project index was checked.",
                details={
                    "indexed_hash": indexed_hash,
                    "actual_hash": source.content_hash,
                },
            )
        accepted_hash = job.get("accepted_source_sha256")
        if accepted_hash is not None:
            if source.content_hash != str(accepted_hash):
                raise WorkflowFailure(
                    "SOURCE_CHANGED",
                    "Accepted source changed while the edit workflow was running.",
                    details={
                        "expected_hash": accepted_hash,
                        "actual_hash": source.content_hash,
                    },
                )
            return job, source

        original_path = self.repository.original_path(
            str(job["project_id"]),
            target.part_id,
            edit_job_id,
        )
        self.repository.write_text(original_path, source.content)
        self.repository.verify_text_hash(original_path, source.content_hash)
        job = self.repository.patch_edit_job(
            edit_job_id,
            {
                "accepted_source_sha256": source.content_hash,
                "original_storage_path": original_path,
            },
        )
        return job, source

    @staticmethod
    def _history_event(
        job: dict[str, Any],
        event_name: str,
        attempt: int,
    ) -> dict[str, Any] | None:
        for event in reversed(job.get("history") or []):
            if event.get("event") == event_name and event.get("attempt") == attempt:
                return event
        return None

    def _candidate_from_event(
        self,
        event: dict[str, Any],
    ) -> tuple[CandidateSource, EditPlan | InitialCadModel, str]:
        path = str(event["candidate_path"])
        content = self.repository.read_text(path)
        expected_hash = str(event["candidate_hash"])
        actual_hash = source_hash(content)
        if actual_hash != expected_hash:
            raise WorkflowFailure(
                "CANDIDATE_SOURCE_HASH_MISMATCH",
                "Persisted candidate source no longer matches its edit attempt.",
            )
        candidate = CandidateSource(
            content=content,
            content_hash=actual_hash,
            base_hash=str(event["base_hash"]),
            changed_symbols=[str(value) for value in event["changed_symbols"]],
            applied_operations=list(event["plan"].get("operations") or [
                {"operation": "initial_design"}
            ]),
        )
        if event.get("candidate_kind") == "initial_design":
            return candidate, InitialCadModel.model_validate(event["plan"]), path
        return candidate, EditPlan.model_validate(event["plan"]), path

    @staticmethod
    def _initial_candidate_source(model: InitialCadModel) -> str:
        body = model.model_body.strip()
        if not body:
            raise WorkflowFailure(
                "INVALID_INITIAL_DESIGN",
                "Initial CAD model body cannot be blank.",
            )
        if body.startswith(CAD_RUNTIME_IMPORT):
            body = body[len(CAD_RUNTIME_IMPORT):].lstrip("\n")
        return f"{BLANK_CAD_SOURCE}\n{body}\n"

    def _prepare_initial_source(
        self,
        job: dict[str, Any],
        target: ResolvedEditTarget,
    ):
        source = self.repository.source(str(job["project_id"]), target.part_id)
        if source.content != BLANK_CAD_SOURCE:
            raise WorkflowFailure(
                "INITIAL_SOURCE_CHANGED",
                "The linked CAD part is no longer blank and cannot be initialised.",
            )
        return self._prepare_accepted_source(
            job,
            target,
            indexed_hash=source.content_hash,
        )

    def _validation_result(
        self,
        edit_job_id: str,
        validation_job_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        validation_job = self._wait_for_job(
            edit_job_id,
            validation_job_id,
            self.repository.generation_job,
            "Validation",
        )
        result = validation_job.get("result")
        if not isinstance(result, dict):
            result = {
                "schema_version": 2,
                "status": "failed",
                "stage": "worker",
                "repairable_hint": False,
                "diagnostics": [
                    {
                        "error_code": "VALIDATION_WORKER_ERROR",
                        "message": str(
                            validation_job.get("error_message")
                            or "Validation returned no structured result."
                        ),
                        "stage": "worker",
                        "related_symbols": [],
                    }
                ],
                "build_artifacts": None,
                "valid": False,
            }
        return validation_job, result

    def _next_plan(
        self,
        job: dict[str, Any],
        getter,
        target: ResolvedEditTarget,
        accepted_source,
    ) -> tuple[EditPlan, str, str, list[str], list[str]]:
        attempt_count = int(job.get("attempt_count") or 0)
        if attempt_count == 0:
            self._set_state(str(job["id"]), "retrieving_context")
            context = build_edit_context(
                getter,
                request=str(job["request_text"]),
                messages=list(job.get("messages") or []),
                target=target,
            )
            self._set_state(str(job["id"]), "planning_edit")
            plan = self.agent.create_edit_plan(context)
            return (
                plan,
                accepted_source.content,
                accepted_source.content_hash,
                [],
                target.semantic_ids,
            )

        validation_event = self._history_event(
            job,
            "validation_finished",
            attempt_count,
        )
        candidate_event = self._history_event(
            job,
            "candidate_created",
            attempt_count,
        )
        if validation_event is None or candidate_event is None:
            raise WorkflowFailure(
                "EDIT_STATE_CORRUPT",
                "The persisted edit attempt is incomplete.",
            )
        candidate, previous_plan, candidate_path = self._candidate_from_event(
            candidate_event
        )
        result = dict(validation_event["validation_result"])
        classification = classify_validation_error(
            result,
            candidate_path=candidate_path,
        )
        if not classification.repairable:
            raise WorkflowFailure(
                "VALIDATION_NOT_REPAIRABLE",
                classification.stop_reason or "Validation cannot be repaired.",
                details={"validation_result": result},
            )
        if attempt_count >= int(job.get("max_attempts") or 3):
            raise WorkflowFailure(
                "MAX_REPAIR_ATTEMPTS",
                "The CAD edit reached its three-attempt validation limit.",
                details={"validation_result": result},
            )

        self._set_state(str(job["id"]), "retrieving_repair_context")
        repair_semantic_ids = list(
            dict.fromkeys(
                [*target.semantic_ids, *semantic_ids_in_source(candidate.content)]
            )
        )
        repair_target = replace(target, semantic_ids=repair_semantic_ids)
        repair_context = build_repair_context(
            getter,
            request=str(job["request_text"]),
            messages=list(job.get("messages") or []),
            target=repair_target,
            candidate=candidate,
            previous_plan=previous_plan,
            validation_result=result,
            related_function_names=classification.related_function_names,
            related_parameter_queries=classification.related_parameter_queries,
        )
        self._set_state(str(job["id"]), "planning_repair")
        plan = self.agent.create_repair_plan(repair_context)
        return (
            plan,
            candidate.content,
            candidate.content_hash,
            classification.related_function_names,
            repair_semantic_ids,
        )

    def _create_or_resume_candidate(
        self,
        job: dict[str, Any],
        getter,
        target: ResolvedEditTarget,
        accepted_source,
    ) -> tuple[dict[str, Any], CandidateSource, EditPlan, str, int]:
        edit_job_id = str(job["id"])
        next_attempt = int(job.get("attempt_count") or 0) + 1
        pending = self._history_event(job, "candidate_created", next_attempt)
        if pending is not None:
            candidate, plan, path = self._candidate_from_event(pending)
            return job, candidate, plan, path, next_attempt

        (
            plan,
            base_source,
            base_hash,
            extra_functions,
            effective_semantic_ids,
        ) = self._next_plan(
            job,
            getter,
            target,
            accepted_source,
        )
        state = "applying_edit" if next_attempt == 1 else "applying_repair"
        self._set_state(edit_job_id, "validating_plan")
        self._set_state(edit_job_id, state)
        candidate = apply_edit_plan(
            base_source,
            expected_hash=base_hash,
            part_id=target.part_id,
            semantic_ids=effective_semantic_ids,
            plan=plan,
            extra_function_names=extra_functions,
        )
        candidate_path = self.repository.candidate_path(
            str(job["project_id"]),
            target.part_id,
            edit_job_id,
            next_attempt,
        )
        self.repository.write_text(candidate_path, candidate.content)
        self.repository.verify_text_hash(candidate_path, candidate.content_hash)
        job = self.repository.append_history(
            edit_job_id,
            {
                "event": "candidate_created",
                "attempt": next_attempt,
                "base_hash": candidate.base_hash,
                "candidate_hash": candidate.content_hash,
                "candidate_path": candidate_path,
                "changed_symbols": candidate.changed_symbols,
                "plan": plan.model_dump(mode="json"),
            },
        )
        return job, candidate, plan, candidate_path, next_attempt

    def _create_or_resume_initial_candidate(
        self,
        job: dict[str, Any],
        target: ResolvedEditTarget,
        accepted_source,
    ) -> tuple[dict[str, Any], CandidateSource, InitialCadModel, str, int]:
        edit_job_id = str(job["id"])
        next_attempt = int(job.get("attempt_count") or 0) + 1
        pending = self._history_event(job, "candidate_created", next_attempt)
        if pending is not None:
            candidate, model, path = self._candidate_from_event(pending)
            if not isinstance(model, InitialCadModel):
                raise WorkflowFailure(
                    "EDIT_STATE_CORRUPT",
                    "Initial design candidate has an edit-plan payload.",
                )
            return job, candidate, model, path, next_attempt

        if next_attempt == 1:
            self._set_state(edit_job_id, "planning_initial_design")
            model = self.agent.create_initial_design(
                InitialCadDesignContext(
                    request=str(job["request_text"]),
                    conversation=list(job.get("messages") or []),
                    part_id=target.part_id,
                    part_name=target.part_name,
                )
            )
        else:
            validation_event = self._history_event(
                job,
                "validation_finished",
                next_attempt - 1,
            )
            candidate_event = self._history_event(
                job,
                "candidate_created",
                next_attempt - 1,
            )
            if validation_event is None or candidate_event is None:
                raise WorkflowFailure(
                    "EDIT_STATE_CORRUPT",
                    "Initial design retry is missing its prior candidate or validation.",
                )
            previous_candidate, previous_model, _path = self._candidate_from_event(
                candidate_event
            )
            if not isinstance(previous_model, InitialCadModel):
                raise WorkflowFailure(
                    "EDIT_STATE_CORRUPT",
                    "Initial design retry has an edit-plan payload.",
                )
            self._set_state(edit_job_id, "planning_initial_repair")
            model = self.agent.create_initial_repair(
                InitialCadRepairContext(
                    original_request=str(job["request_text"]),
                    conversation=list(job.get("messages") or []),
                    previous_model_body=previous_candidate.content.removeprefix(
                        f"{BLANK_CAD_SOURCE}\n"
                    ),
                    validation_result=dict(validation_event["validation_result"]),
                )
            )

        self._set_state(edit_job_id, "applying_initial_design")
        content = self._initial_candidate_source(model)
        candidate = CandidateSource(
            content=content,
            content_hash=source_hash(content),
            base_hash=accepted_source.content_hash,
            changed_symbols=["initial_design"],
            applied_operations=[{"operation": "initial_design"}],
        )
        candidate_path = self.repository.candidate_path(
            str(job["project_id"]), target.part_id, edit_job_id, next_attempt
        )
        self.repository.write_text(candidate_path, candidate.content)
        self.repository.verify_text_hash(candidate_path, candidate.content_hash)
        job = self.repository.append_history(
            edit_job_id,
            {
                "event": "candidate_created",
                "candidate_kind": "initial_design",
                "attempt": next_attempt,
                "base_hash": candidate.base_hash,
                "candidate_hash": candidate.content_hash,
                "candidate_path": candidate_path,
                "changed_symbols": candidate.changed_symbols,
                "plan": model.model_dump(mode="json"),
            },
        )
        return job, candidate, model, candidate_path, next_attempt

    def _rollback(
        self,
        job: dict[str, Any],
        target: ResolvedEditTarget,
    ) -> bool:
        candidate_hash = str(job.get("current_candidate_sha256") or "")
        accepted_hash = str(job.get("accepted_source_sha256") or "")
        original_path = str(job.get("original_storage_path") or "")
        if not candidate_hash or not accepted_hash or not original_path:
            return False
        canonical_path = self.repository.canonical_source_path(
            str(job["project_id"]),
            target.part_id,
        )
        canonical = self.repository.read_text(canonical_path)
        canonical_hash = source_hash(canonical)
        if canonical_hash == accepted_hash:
            return True
        if canonical_hash != candidate_hash:
            raise WorkflowFailure(
                "COMMIT_RECOVERY_CONFLICT",
                "Canonical source changed independently after candidate commit.",
            )
        original = self.repository.read_text(original_path)
        if source_hash(original) != accepted_hash:
            raise WorkflowFailure(
                "COMMIT_RECOVERY_FAILED",
                "The original-source backup does not match the accepted hash.",
            )
        self.repository.write_text(canonical_path, original)
        self.repository.verify_text_hash(canonical_path, accepted_hash)
        return True

    def _commit(
        self,
        job: dict[str, Any],
        getter,
        target: ResolvedEditTarget,
        candidate: CandidateSource,
        plan: EditPlan | InitialCadModel,
        validation_job: dict[str, Any],
        validation_result: dict[str, Any],
    ) -> dict[str, Any]:
        edit_job_id = str(job["id"])
        if (
            validation_job.get("status") != "completed"
            or validation_job.get("type") != "validate_cad"
            or validation_job.get("source_kind") != "candidate"
            or str(validation_job.get("edit_job_id")) != edit_job_id
            or str(validation_job.get("source_storage_path"))
            != str(job.get("current_candidate_path"))
            or str(validation_job.get("source_sha256")) != candidate.content_hash
            or validation_result.get("status") != "passed"
            or validation_result.get("valid") is not True
        ):
            raise WorkflowFailure(
                "VALIDATION_PROOF_MISMATCH",
                "Candidate validation does not prove the current candidate.",
            )

        resume_state = str(job.get("state") or "")
        canonical_path = self.repository.canonical_source_path(
            str(job["project_id"]),
            target.part_id,
        )
        canonical = self.repository.read_text(canonical_path)
        canonical_hash = source_hash(canonical)
        accepted_hash = str(job["accepted_source_sha256"])
        if canonical_hash == accepted_hash:
            job = self._set_state(edit_job_id, "committing")
            self.repository.write_text(canonical_path, candidate.content)
            self.repository.verify_text_hash(canonical_path, candidate.content_hash)
        elif canonical_hash != candidate.content_hash:
            raise WorkflowFailure(
                "SOURCE_CHANGED",
                "Accepted source changed before the validated candidate could commit.",
            )

        if resume_state in {"reindexing", "queueing_export"} and job.get(
            "index_job_id"
        ):
            index_job_id = str(job["index_job_id"])
            index_job = self.repository.index_job(index_job_id)
            if index_job["status"] not in TERMINAL_JOB_STATUSES:
                index_job = self._wait_for_job(
                    edit_job_id,
                    index_job_id,
                    self.repository.index_job,
                    "Reindex",
                )
        else:
            job = self._set_state(
                edit_job_id,
                "reindexing",
                index_job_id=None,
            )
            index_job_id = self.repository.queue_index(edit_job_id, "reindexing")
            index_job = self._wait_for_job(
                edit_job_id,
                index_job_id,
                self.repository.index_job,
                "Reindex",
            )
        fresh_getter = None
        try:
            fresh_getter = self.repository.getter(str(job["project_id"]))
        except Exception:
            pass
        fresh = (
            fresh_getter is not None
            and fresh_getter.freshness()["status"] == "fresh"
            and all(
                fresh_getter.get_part(target.part_id, semantic_id) is not None
                for semantic_id in target.semantic_ids
            )
        )
        if index_job["status"] != "completed" or not fresh:
            latest_job = self.repository.edit_job(edit_job_id)
            self._rollback(latest_job, target)
            raise WorkflowFailure(
                "REINDEX_FAILED",
                str(
                    index_job.get("error_message")
                    or "Committed source could not produce a fresh project index."
                ),
                details={
                    "index_job_id": index_job_id,
                    "source_restored": True,
                },
            )

        warnings: list[str] = []
        export_job_id = str(job["export_job_id"]) if job.get("export_job_id") else None
        try:
            if export_job_id is None:
                self._set_state(edit_job_id, "queueing_export")
                export_job_id = self.repository.queue_export(
                    edit_job_id,
                    candidate.content_hash,
                )
        except Exception as exc:
            warnings.append(
                f"CAD export could not be queued; run /export manually: {exc}"
            )

        result: dict[str, Any] = {
            "schema_version": 1,
            "status": "completed",
            "message": plan.summary,
            "attempts": int(job.get("attempt_count") or 0),
            "resolved_target": {
                "part_id": target.part_id,
                "part_name": target.part_name,
                "semantic_ids": target.semantic_ids,
            },
            "changed_files": [canonical_path],
            "changed_symbols": candidate.changed_symbols,
            "source_sha256": candidate.content_hash,
            "validation_result": validation_result,
            "index_job_id": index_job_id,
            "export_job_id": export_job_id,
            "warnings": warnings,
        }
        self.repository.complete_edit_job(edit_job_id, result)
        cleanup_warning = self.repository.cleanup_candidates(
            str(job["project_id"]),
            target.part_id,
            edit_job_id,
        )
        if cleanup_warning:
            result["warnings"].append(cleanup_warning)
            self.repository.patch_edit_job(edit_job_id, {"result": result})
        return result

    def _run_initial_design(self, job: dict[str, Any]) -> dict[str, Any]:
        edit_job_id = str(job["id"])
        target = self._target_from_job(job)
        if target is None:
            raise WorkflowFailure(
                "EDIT_STATE_CORRUPT",
                "Initial design job has no linked CAD part.",
            )
        # The create_part index build is a barrier: wait for/reuse it before
        # generating source so the mandatory post-commit reindex cannot reuse
        # a build that started against the blank marker.
        self._ensure_fresh_getter(job, force_build=True)
        job = self.repository.edit_job(edit_job_id)
        job, accepted_source = self._prepare_initial_source(job, target)

        while True:
            validation_job_id = job.get("validation_job_id")
            attempt = int(job.get("attempt_count") or 0)
            if validation_job_id:
                candidate_event = self._history_event(job, "candidate_created", attempt)
                if candidate_event is None:
                    raise WorkflowFailure(
                        "EDIT_STATE_CORRUPT",
                        "Validation job has no persisted initial-design candidate.",
                    )
                candidate, plan, candidate_path = self._candidate_from_event(candidate_event)
                if not isinstance(plan, InitialCadModel):
                    raise WorkflowFailure(
                        "EDIT_STATE_CORRUPT",
                        "Initial design job has an edit-plan candidate.",
                    )
            else:
                job, candidate, plan, candidate_path, attempt = (
                    self._create_or_resume_initial_candidate(
                        job,
                        target,
                        accepted_source,
                    )
                )
                validation_job_id = self.repository.queue_candidate_validation(
                    edit_job_id=edit_job_id,
                    candidate_path=candidate_path,
                    candidate_hash=candidate.content_hash,
                    attempt=attempt,
                )
                job = self.repository.edit_job(edit_job_id)

            validation_event = self._history_event(job, "validation_finished", attempt)
            if validation_event is None:
                validation_job, validation_result = self._validation_result(
                    edit_job_id,
                    str(validation_job_id),
                )
                job = self.repository.append_history(
                    edit_job_id,
                    {
                        "event": "validation_finished",
                        "attempt": attempt,
                        "validation_job_id": validation_job_id,
                        "validation_status": validation_job["status"],
                        "validation_result": validation_result,
                    },
                )
            else:
                validation_job = self.repository.generation_job(str(validation_job_id))
                validation_result = dict(validation_event["validation_result"])

            if (
                validation_job["status"] == "completed"
                and validation_result.get("status") == "passed"
                and validation_result.get("valid") is True
            ):
                return self._commit(
                    job,
                    None,
                    target,
                    candidate,
                    plan,
                    validation_job,
                    validation_result,
                )

            job = self._set_state(
                edit_job_id,
                "classifying_error",
                validation_job_id=None,
            )
            classification = classify_validation_error(
                validation_result,
                candidate_path=candidate_path,
            )
            if not classification.repairable:
                raise WorkflowFailure(
                    "VALIDATION_NOT_REPAIRABLE",
                    classification.stop_reason or "Validation cannot be repaired.",
                    details={"validation_result": validation_result},
                )
            if attempt >= int(job.get("max_attempts") or 3):
                raise WorkflowFailure(
                    "MAX_REPAIR_ATTEMPTS",
                    "The initial CAD design reached its three-attempt validation limit.",
                    details={"validation_result": validation_result},
                )
            job = self.repository.edit_job(edit_job_id)

    def _run(self, job: dict[str, Any]) -> dict[str, Any]:
        edit_job_id = str(job["id"])
        self._heartbeat(edit_job_id)

        target = self._target_from_job(job)
        if target is not None and str(job.get("state")) in POST_COMMIT_STATES:
            candidate_event = self._history_event(
                job,
                "candidate_created",
                int(job.get("attempt_count") or 0),
            )
            if candidate_event is None:
                raise WorkflowFailure(
                    "EDIT_STATE_CORRUPT",
                    "Committed edit is missing its candidate event.",
                )
            candidate, plan, _path = self._candidate_from_event(candidate_event)
            validation_job = self.repository.generation_job(
                str(job["validation_job_id"])
            )
            validation_result = dict(validation_job.get("result") or {})
            getter = self.repository.getter(str(job["project_id"]))
            return self._commit(
                job,
                getter,
                target,
                candidate,
                plan,
                validation_job,
                validation_result,
            )

        if str(job.get("workflow_mode") or "edit") == "initial_design":
            return self._run_initial_design(job)

        getter = self._ensure_fresh_getter(job)
        job = self.repository.edit_job(edit_job_id)
        job, target = self._resolve_target(job, getter)
        indexed_source = getter.sources.get(target.part_id)
        if indexed_source is None:
            raise WorkflowFailure(
                "TARGET_NOT_FOUND",
                "The resolved CAD part is missing from the fresh Getter.",
            )
        job, accepted_source = self._prepare_accepted_source(
            job,
            target,
            indexed_hash=indexed_source.content_hash,
        )

        while True:
            validation_job_id = job.get("validation_job_id")
            attempt = int(job.get("attempt_count") or 0)
            if validation_job_id:
                candidate_event = self._history_event(
                    job,
                    "candidate_created",
                    attempt,
                )
                if candidate_event is None:
                    raise WorkflowFailure(
                        "EDIT_STATE_CORRUPT",
                        "Validation job has no persisted candidate event.",
                    )
                candidate, plan, candidate_path = self._candidate_from_event(
                    candidate_event
                )
            else:
                (
                    job,
                    candidate,
                    plan,
                    candidate_path,
                    attempt,
                ) = self._create_or_resume_candidate(
                    job,
                    getter,
                    target,
                    accepted_source,
                )
                validation_job_id = self.repository.queue_candidate_validation(
                    edit_job_id=edit_job_id,
                    candidate_path=candidate_path,
                    candidate_hash=candidate.content_hash,
                    attempt=attempt,
                )
                job = self.repository.edit_job(edit_job_id)

            validation_event = self._history_event(
                job,
                "validation_finished",
                attempt,
            )
            if validation_event is None:
                validation_job, validation_result = self._validation_result(
                    edit_job_id,
                    str(validation_job_id),
                )
                job = self.repository.append_history(
                    edit_job_id,
                    {
                        "event": "validation_finished",
                        "attempt": attempt,
                        "validation_job_id": validation_job_id,
                        "validation_status": validation_job["status"],
                        "validation_result": validation_result,
                    },
                )
            else:
                validation_job = self.repository.generation_job(str(validation_job_id))
                validation_result = dict(validation_event["validation_result"])

            if (
                validation_job["status"] == "completed"
                and validation_result.get("status") == "passed"
                and validation_result.get("valid") is True
            ):
                return self._commit(
                    job,
                    getter,
                    target,
                    candidate,
                    plan,
                    validation_job,
                    validation_result,
                )

            job = self._set_state(
                edit_job_id,
                "classifying_error",
                validation_job_id=None,
            )
            classification = classify_validation_error(
                validation_result,
                candidate_path=candidate_path,
            )
            if not classification.repairable:
                raise WorkflowFailure(
                    "VALIDATION_NOT_REPAIRABLE",
                    classification.stop_reason or "Validation cannot be repaired.",
                    details={"validation_result": validation_result},
                )
            if attempt >= int(job.get("max_attempts") or 3):
                raise WorkflowFailure(
                    "MAX_REPAIR_ATTEMPTS",
                    "The CAD edit reached its three-attempt validation limit.",
                    details={"validation_result": validation_result},
                )
            job = self.repository.edit_job(edit_job_id)

    def _handle_failure(
        self,
        job: dict[str, Any],
        failure: WorkflowFailure,
    ) -> dict[str, Any]:
        edit_job_id = str(job["id"])
        latest = self.repository.edit_job(edit_job_id)
        target = self._target_from_job(latest)
        source_restored = False
        recovery_error = None
        if target is not None and str(latest.get("state")) in {
            "committing",
            "reindexing",
        }:
            try:
                source_restored = self._rollback(latest, target)
            except WorkflowFailure as exc:
                recovery_error = {
                    "code": exc.code,
                    "message": str(exc),
                }

        result: dict[str, Any] = {
            "schema_version": 1,
            "status": "failed",
            "message": str(failure),
            "error_code": failure.code,
            "state": latest.get("state"),
            "attempts": int(latest.get("attempt_count") or 0),
            "resolved_targets": latest.get("resolved_targets") or [],
            "source_restored": source_restored,
            "details": failure.details,
            "recovery_error": recovery_error,
            "warnings": [],
        }
        self.repository.fail_edit_job(
            edit_job_id,
            code=failure.code,
            message=str(failure),
            result=result,
        )
        if target is not None:
            cleanup_warning = self.repository.cleanup_candidates(
                str(latest["project_id"]),
                target.part_id,
                edit_job_id,
            )
            if cleanup_warning:
                result["warnings"].append(cleanup_warning)
                self.repository.patch_edit_job(edit_job_id, {"result": result})
        return result

    def run(self, job: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run(job)
        except WorkflowFailure as failure:
            return self._handle_failure(job, failure)
        except Exception as exc:
            internal_failure = WorkflowFailure(
                "WORKFLOW_INTERNAL_ERROR",
                "The CAD editor failed unexpectedly.",
                details={"traceback": traceback.format_exc()[-16000:]},
            )
            result = self._handle_failure(job, internal_failure)
            result["internal_exception"] = type(exc).__name__
            return result
