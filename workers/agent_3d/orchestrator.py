from __future__ import annotations

import ast
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar
from uuid import uuid4

from workers.commons.storage import cad_source_storage_path

from .failures import WorkflowFailure
from .planning.agent_contracts import CadGoal, CadPlan, PlanStep
from .planning.agent_trace import AgentTraceContext, AgentTraceWriter
from .planning.planning_log import PlanningLogWriter
from .planning.resolver import resolve_deterministic_edit_target
from .tools import ToolExecutionContext, ToolServices
from .tools.base import tool_failure
from .tools.hashing import source_hash
from .tools.index.index_tools import _index as _load_semantic_index
from .tools.index.index_tools import _parts as _semantic_index_parts
from .tools.index.index_tools import _text as _index_text


TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
RECENT_MESSAGE_LIMIT = 8
ACTIVE_STEP_STATUSES = ("pending", "in_progress")
STEP_COMPLETION_TOOL_ID = "request_step_completion"
MAX_AGENT_TURNS = 32
MAX_STEP_TURNS = 20
MAX_TOOL_CALLS_PER_BATCH = 5
T = TypeVar("T")
LOGGER = logging.getLogger(__name__)

# Read-only tools whose result depends only on their arguments: the semantic
# index and query semantics do not change mid-step, so an identical repeat
# call within the same step's observations can only return what is already
# known there.
READ_ONLY_DEDUPE_TOOL_IDS = frozenset({"index_search", "index_get_feature"})

# Tools that write to the current candidate's source. A check_geometry call
# with nothing else in this set having run since the last check_geometry
# observation cannot report anything different from that prior observation.
CANDIDATE_MUTATING_TOOL_IDS = frozenset(
    {
        "create_feature",
        "edit_feature",
        "delete_feature",
        "create_parameter",
        "edit_parameter",
        "delete_parameter",
        "edit_cad_build_model",
    }
)
GEOMETRY_CHECK_TOOL_ID = "check_geometry"


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _model_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    return _record(value)


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read one field from a model response item that may be a dict or object."""

    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _output(response: Any) -> list[Any]:
    """Return a model response's output items, or an empty list when absent."""

    value = _field(response, "output", [])
    return value if isinstance(value, list) else []


@dataclass(frozen=True)
class ToolCallRequest:
    """One tool call requested by a single model response.

    Arguments are parsed exactly once, here, at batch-construction time.
    ``parse_error`` is set (and ``arguments`` is empty) when the model's
    arguments string failed to parse as a JSON object; dispatch turns that
    into a TOOL_INPUT_INVALID result without ever calling the tool -- a
    per-call execution failure, not a batch-validation failure.
    """

    call_id: str
    tool_id: str
    raw_arguments: str
    arguments: dict[str, Any]
    parse_error: str | None = None


@dataclass(frozen=True)
class ToolCallBatch:
    """The full, ordered set of tool calls requested by one model response."""

    calls: tuple[ToolCallRequest, ...]


def _parse_call(item: Any) -> ToolCallRequest:
    """Build one ToolCallRequest from a raw function_call response item."""

    call_id = str(_field(item, "call_id", "") or "")
    tool_id = str(_field(item, "name", "") or "")
    raw_arguments = str(_field(item, "arguments", "{}"))
    try:
        parsed = json.loads(raw_arguments)
        if not isinstance(parsed, dict):
            raise ValueError("Tool arguments must be a JSON object.")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ToolCallRequest(call_id, tool_id, raw_arguments, {}, "Tool arguments are invalid.")
    return ToolCallRequest(call_id, tool_id, raw_arguments, parsed, None)


def _batch_rejection_outputs(batch: ToolCallBatch, failure: Any) -> list[dict[str, Any]]:
    """Build one identical rejection function_call_output per call in ``batch``."""

    payload = json.dumps(failure.model_dump(mode="json"), ensure_ascii=False)
    return [
        {"type": "function_call_output", "call_id": call.call_id, "output": payload}
        for call in batch.calls
    ]


def _with_step_status(plan: CadPlan, step_id: str, status: str) -> CadPlan:
    """Return a revalidated copy of ``plan`` with one step's status replaced.

    Only ``status`` changes. ``plan_id``, ``goal_id``, and ``version`` are
    preserved: a step transition is progress through the existing plan, not a
    plan revision. The goal is never touched.
    """

    payload = plan.model_dump(mode="json")
    for step in payload["steps"]:
        if step["step_id"] == step_id:
            step["status"] = status
    return CadPlan.model_validate(payload)


def _recent_messages(
    messages: Any, *, maximum: int = RECENT_MESSAGE_LIMIT
) -> list[dict[str, Any]]:
    """Return the last ``maximum`` conversation messages, chronologically ordered."""

    if not isinstance(messages, list):
        return []
    return [dict(message) for message in messages if isinstance(message, dict)][
        -maximum:
    ]


def _active_step(plan: CadPlan) -> PlanStep | None:
    """Return the step Agent3D should work on, or ``None`` when the plan is done.

    The active step is the earliest one still pending or in progress. ``None``
    means every step reached a terminal status, which ends the agent loop
    normally rather than being an error.
    """

    for step in sorted(plan.steps, key=lambda step: step.sequence):
        if step.status in ACTIVE_STEP_STATUSES:
            return step
    return None


def _extract_cad_features(source: str) -> list[dict[str, str]]:
    """Return one record per top-level ``@cad_part(...)``-decorated function.

    Same AST walk and decorator-matching predicate ``_count_cad_features``
    uses, but collects ``{"semantic_id", "function_name", "role"}`` per match
    instead of just counting. A decorator keyword that's absent, or isn't a
    plain string literal, defaults to ``""`` rather than raising -- the same
    fail-open philosophy as the rest of this scan. Fails open to ``[]`` on a
    candidate source with a syntax error, since an empty roster is also the
    correct answer for "nothing to report yet."
    """

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    features: list[dict[str, str]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and (
                isinstance(decorator.func, ast.Name) and decorator.func.id == "cad_part"
            ):
                keywords = {kw.arg: kw.value for kw in decorator.keywords if kw.arg}

                def _literal(name: str) -> str:
                    value = keywords.get(name)
                    return (
                        value.value
                        if isinstance(value, ast.Constant) and isinstance(value.value, str)
                        else ""
                    )

                features.append(
                    {
                        "semantic_id": _literal("semantic_id"),
                        "function_name": node.name,
                        "role": _literal("role"),
                    }
                )
                break
    return features


def _extract_model_params(source: str) -> list[dict[str, str]]:
    """Return one record per field declared on the source's ``ModelParams`` class.

    Mirrors ``_extract_cad_features``'s fail-open philosophy: a syntax error
    or an absent ``ModelParams`` class yields ``[]`` rather than raising,
    since an empty roster is also the correct answer for "no parameters
    declared yet." A field's declared type and literal default are included
    so the roster doesn't just say a parameter exists, but what it already
    is -- the information that would otherwise cost a tool call to learn.
    """

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "ModelParams":
            continue
        params: list[dict[str, str]] = []
        for item in node.body:
            if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                continue
            value = item.value
            if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
                value = value.operand
                default = f"-{value.value}" if isinstance(value, ast.Constant) else ""
            elif isinstance(value, ast.Constant):
                default = str(value.value)
            else:
                default = ""
            params.append(
                {
                    "name": item.target.id,
                    "type_name": ast.unparse(item.annotation),
                    "default": default,
                }
            )
        return params
    return []


def _count_cad_features(source: str) -> int:
    """Count top-level functions decorated with ``@cad_part(...)`` in CAD source.

    A lightweight presence check, not full ``extractor.py``-style validation.
    Thin wrapper over ``_extract_cad_features`` -- keep the two in lockstep;
    this remains the ``STEP_REQUIRES_A_FEATURE`` completion gate's only use.
    """

    return len(_extract_cad_features(source))


def _step_needs_new_geometry(step: PlanStep, goal: CadGoal) -> bool:
    """Return whether ``step`` addresses a completion criterion that requires
    something to be added, per that criterion's own ``type`` field.

    ``preserve`` criteria describe existing behavior that must not change, so a
    step addressing only ``preserve`` criteria is never gated on feature count.
    """

    required_ids = {
        criterion.criterion_id
        for criterion in goal.completion_criteria
        if criterion.type == "required"
    }
    return any(
        criterion_id in required_ids for criterion_id in step.addresses_criteria
    )


class EditWorkflowOrchestrator:
    """Own one leased CAD edit from user goal through the agent loop.

    The public queue is ``edit_jobs``. Goal and plan are private append-only
    checkpoints in ``edit_jobs.history`` so a replacement worker can resume
    without relying on process memory.

    After the plan is created, this orchestrator runs the agent loop: it
    selects the active plan step, asks ``Agent3D`` for one decision, sends the
    requested tool calls to ``tool_executor``, records the results as
    current-step observations, and repeats. When the agent calls
    ``request_step_completion`` the orchestrator marks that step completed and
    advances; the loop ends once every step reached a terminal status.

    This orchestrator is the sole owner of plan state. ``Agent3D`` and the
    tools never mutate the plan, and **the goal is never mutated by anything**.
    Only step ``status`` changes -- adding, replacing, or reordering steps is
    plan revision, which is not implemented.

    Tool calls run against an edit-scoped candidate copy of the CAD source, so
    canonical source is never modified while the loop runs. Once every step
    completes, the candidate is validated by the CAD validator worker,
    committed to canonical source with a hash-guarded read-verify-write
    (rolling back if the mandatory post-commit reindex fails), and export is
    queued best-effort. A successful edit job completes with
    ``outcome: "committed"``.
    """

    def __init__(
        self,
        repository: Any,
        goal_creator: Any,
        planning_agent: Any,
        agent_3d: Any,
        tool_executor: Any,
        *,
        worker_id: str,
        planning_log_writer: Any | None = None,
        trace_writer: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.repository = repository
        self.goal_creator = goal_creator
        self.planning_agent = planning_agent
        self.agent_3d = agent_3d
        self.tool_executor = tool_executor
        self.worker_id = worker_id
        self.planning_log_writer = planning_log_writer or PlanningLogWriter()
        self.trace_writer = trace_writer or AgentTraceWriter()
        self.sleep = sleep
        self.monotonic = monotonic
        self.poll_interval = float(
            os.environ.get("CAD_EDITOR_DEPENDENCY_POLL_INTERVAL_SECONDS", "0.5")
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

    def _with_heartbeat(self, edit_job_id: str, task: Callable[[], T]) -> T:
        """Keep the edit lease alive while a blocking model call runs."""

        self._heartbeat(edit_job_id)
        stop = threading.Event()
        lease_errors: list[Exception] = []

        def maintain_lease() -> None:
            interval = max(1.0, self.lease_seconds / 3)
            while not stop.wait(interval):
                try:
                    self._heartbeat(edit_job_id)
                except Exception as exc:  # The caller checks before publishing.
                    lease_errors.append(exc)
                    stop.set()

        thread = threading.Thread(
            target=maintain_lease,
            name=f"cad-editor-heartbeat-{edit_job_id}",
            daemon=True,
        )
        thread.start()
        result: T | None = None
        task_error: Exception | None = None
        try:
            result = task()
        except Exception as exc:
            task_error = exc
        finally:
            stop.set()
            thread.join(timeout=1)
        if lease_errors:
            raise lease_errors[0]
        if task_error is not None:
            raise task_error
        self._heartbeat(edit_job_id)
        return result  # type: ignore[return-value]

    def _trace(self, event: str, edit_job_id: str, **fields: Any) -> None:
        """Best-effort structured trace write -- never fails the workflow.

        Only Agent3D's pre-call ``llm.request`` write is mandatory (it gates
        the model call, matching existing precedent). Every orchestrator-
        owned trace event -- tool execution, step transitions, loop outcome
        -- is diagnostic only: a write failure here is logged and swallowed
        rather than discarding real (possibly already-billed) progress.
        """

        try:
            self.trace_writer.log(event, edit_job_id=edit_job_id, **fields)
        except Exception:
            LOGGER.warning(
                "trace write failed edit_job_id=%s event=%s",
                edit_job_id,
                event,
                exc_info=True,
            )

    def _patch(self, edit_job_id: str, values: dict[str, Any]) -> dict[str, Any]:
        return self.repository.patch_edit_job(
            edit_job_id,
            values,
            worker_id=self.worker_id,
        )

    def _history(
        self,
        edit_job_id: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        return self.repository.append_history(
            edit_job_id,
            event,
            worker_id=self.worker_id,
        )

    def _event(
        self,
        edit_job_id: str,
        event_type: str,
        state: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.repository.append_event(
            edit_job_id,
            worker_id=self.worker_id,
            event_type=event_type,
            state=state,
            message=message,
            metadata=metadata or {},
        )

    def _transition(
        self,
        edit_job_id: str,
        state: str,
        event_type: str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
        values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = self._patch(edit_job_id, {"state": state, **(values or {})})
        self._event(edit_job_id, event_type, state, message, metadata)
        self._heartbeat(edit_job_id)
        return job

    @staticmethod
    def _history_event(
        job: dict[str, Any],
        event_name: str,
        attempt: int | None = None,
    ) -> dict[str, Any] | None:
        for event in reversed(list(job.get("history") or [])):
            if not isinstance(event, dict) or event.get("event") != event_name:
                continue
            if attempt is not None and int(event.get("attempt") or 0) != attempt:
                continue
            return event
        return None

    @classmethod
    def _plan_checkpoint(cls, job: dict[str, Any]) -> dict[str, Any] | None:
        """Return the newest persisted plan checkpoint, updated or original.

        History is append-only and ``_history_event`` returns its last match,
        so the latest ``plan_updated`` entry carries current step statuses. A
        job that has not advanced any step yet falls back to ``plan_created``.
        """

        return cls._history_event(job, "plan_updated") or cls._history_event(
            job, "plan_created"
        )

    @classmethod
    def _planning_result(cls, job: dict[str, Any]) -> dict[str, Any]:
        """Return the durable planning artifacts exposed by terminal results."""

        goal_event = cls._history_event(job, "goal_created")
        plan_event = cls._plan_checkpoint(job)
        return {
            "goal": _record((goal_event or {}).get("goal")),
            "high_level_plan": _record((plan_event or {}).get("plan")),
        }

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
            if str(dependency.get("status") or "") in TERMINAL_JOB_STATUSES:
                return dependency
            if self.monotonic() >= deadline:
                raise WorkflowFailure(
                    "DEPENDENCY_TIMEOUT",
                    f"{label} did not finish before its timeout.",
                )
            self._heartbeat(edit_job_id)
            self.sleep(self.poll_interval)

    def _goal(self, job: dict[str, Any]) -> CadGoal:
        checkpoint = self._history_event(job, "goal_created")
        if checkpoint is not None:
            return CadGoal.model_validate(checkpoint.get("goal"))

        edit_job_id = str(job["id"])
        self._transition(
            edit_job_id,
            "creating_goal",
            "goal.started",
            "Creating a structured CAD goal.",
        )
        goal = self._with_heartbeat(
            edit_job_id,
            lambda: self.goal_creator.create_goal(str(job["request_text"])),
        )
        if not isinstance(goal, CadGoal):
            goal = CadGoal.model_validate(_model_payload(goal))
        self._history(
            edit_job_id,
            {"event": "goal_created", "goal": goal.model_dump(mode="json")},
        )
        self._event(
            edit_job_id,
            "goal.completed",
            "creating_goal",
            "Structured CAD goal created.",
            {
                "goal_id": str(goal.goal_id),
                "criteria_count": len(goal.completion_criteria),
                "clarification_required": goal.clarification.required,
            },
        )
        return goal

    def _ensure_fresh_index(
        self,
        job: dict[str, Any],
        *,
        force_build: bool = False,
    ) -> Any:
        edit_job_id = str(job["id"])
        checkpoint = self._history_event(job, "initial_index_ready")
        getter = None
        try:
            getter = self.repository.getter(str(job["project_id"]))
        except (ValueError, RuntimeError, WorkflowFailure):
            getter = None
        if (
            getter is not None
            and getter.freshness().get("status") == "fresh"
            and (not force_build or checkpoint is not None)
        ):
            return getter

        self._transition(
            edit_job_id,
            "ensuring_index",
            "indexing.started",
            "Ensuring a fresh project index.",
        )
        index_id = self.repository.queue_index(
            edit_job_id,
            "ensuring_index",
            worker_id=self.worker_id,
        )
        child = self._wait_for_job(
            edit_job_id,
            index_id,
            self.repository.index_job,
            "Index build",
        )
        if child.get("status") != "completed":
            raise WorkflowFailure(
                "INDEX_BUILD_FAILED",
                str(child.get("error_message") or "The project index could not be built."),
                details={"index_job_id": index_id},
            )
        self._patch(edit_job_id, {"index_job_id": None})
        getter = self.repository.getter(str(job["project_id"]))
        freshness = getter.freshness()
        if freshness.get("status") != "fresh":
            raise WorkflowFailure(
                "STALE_INDEX",
                "The project index is still stale after rebuilding.",
                details=_record(freshness),
            )
        self._history(
            edit_job_id,
            {"event": "initial_index_ready", "index_job_id": index_id},
        )
        self._event(
            edit_job_id,
            "indexing.completed",
            "ensuring_index",
            "Project index is ready.",
            {"index_job_id": index_id},
        )
        return getter

    def _high_level_plan(self, job: dict[str, Any], goal: CadGoal) -> CadPlan:
        checkpoint = self._plan_checkpoint(job)
        if checkpoint is not None:
            plan = CadPlan.model_validate(checkpoint.get("plan"))
            if plan.goal_id != goal.goal_id:
                raise WorkflowFailure(
                    "EDIT_STATE_CORRUPT",
                    "Persisted CAD plan belongs to another goal.",
                )
            return plan

        edit_job_id = str(job["id"])
        part_id = str(job.get("resolved_part_id") or "")
        if not part_id:
            raise WorkflowFailure(
                "EDIT_STATE_CORRUPT",
                "High-level planning requires a resolved CAD part.",
            )
        plan_id = str(uuid4())
        version = 1
        self._transition(
            edit_job_id,
            "planning_goal",
            "plan.started",
            "Building the high-level CAD plan from semantic context.",
            metadata={"goal_id": str(goal.goal_id)},
        )
        model_plan = self._with_heartbeat(
            edit_job_id,
            lambda: self.planning_agent.create_plan(
                goal=goal,
                plan_id=plan_id,
                tool_context=ToolExecutionContext(
                    run_id=plan_id,
                    project_id=str(job["project_id"]),
                    part_id=part_id,
                    candidate_id=None,
                    services=ToolServices(repository=self.repository),
                ),
                version=version,
            ),
        )
        payload = _model_payload(model_plan)
        payload.update(
            plan_id=plan_id,
            goal_id=str(goal.goal_id),
            version=version,
        )
        plan = CadPlan.model_validate(payload)
        if any(step.status != "pending" for step in plan.steps):
            raise WorkflowFailure(
                "PLAN_RESPONSE_INVALID",
                "Every newly generated planning step must be pending.",
            )
        criterion_ids = {
            criterion.criterion_id for criterion in goal.completion_criteria
        }
        addressed = {
            criterion_id
            for step in plan.steps
            for criterion_id in step.addresses_criteria
        }
        if addressed != criterion_ids:
            raise WorkflowFailure(
                "PLAN_RESPONSE_INVALID",
                "The plan must collectively address exactly the supplied goal criteria.",
                details={
                    "missing": sorted(criterion_ids - addressed),
                    "unknown": sorted(addressed - criterion_ids),
                },
            )
        self._history(
            edit_job_id,
            {"event": "plan_created", "plan": plan.model_dump(mode="json")},
        )
        self._event(
            edit_job_id,
            "plan.completed",
            "planning_goal",
            "High-level CAD plan created.",
            {
                "goal_id": str(goal.goal_id),
                "plan_id": str(plan.plan_id),
                "version": plan.version,
                "steps": len(plan.steps),
            },
        )
        return plan

    def _resolve_planning_part(self, job: dict[str, Any]) -> dict[str, Any]:
        existing_targets = list(job.get("resolved_targets") or [])
        if job.get("resolved_part_id") and existing_targets:
            return job

        edit_job_id = str(job["id"])
        getter = self.repository.getter(str(job["project_id"]))
        if getter.freshness()["status"] != "fresh":
            raise WorkflowFailure(
                "STALE_INDEX",
                "Project index is not fresh enough to resolve planning scope.",
            )
        authoritative_part_id = job.get("requested_part_id") or job.get(
            "resolved_part_id"
        )
        target = resolve_deterministic_edit_target(
            getter,
            str(job["request_text"]),
            requested_part_id=(
                str(authoritative_part_id) if authoritative_part_id else None
            ),
        )
        resolved_targets = [
            {
                "part_id": target.part_id,
                "part_name": target.part_name,
                "semantic_ids": list(target.semantic_ids),
                "confidence": target.confidence,
                "reason": target.reason,
                "candidates": list(target.candidates),
            }
        ]
        try:
            self._patch(
                edit_job_id,
                {
                    "resolved_part_id": target.part_id,
                    "resolved_targets": resolved_targets,
                },
            )
        except WorkflowFailure:
            raise
        except Exception as exc:
            raise WorkflowFailure(
                "PART_EDIT_IN_PROGRESS",
                "Another active edit already owns the resolved CAD part.",
            ) from exc
        return self._history(
            edit_job_id,
            {
                "event": "planning_target_resolved",
                "part_id": target.part_id,
                "semantic_ids": list(target.semantic_ids),
                "confidence": target.confidence,
            },
        )

    def _set_step_status(
        self,
        edit_job_id: str,
        plan: CadPlan,
        step_id: str,
        status: str,
    ) -> CadPlan:
        """Advance one plan step and checkpoint the updated plan durably.

        Each transition appends its own ``plan_updated`` history entry, so a
        replacement worker resumes at the step the previous one reached.
        """

        updated = _with_step_status(plan, step_id, status)
        self._history(
            edit_job_id,
            {"event": "plan_updated", "plan": updated.model_dump(mode="json")},
        )
        return updated

    @staticmethod
    def _candidate_path(job: dict[str, Any]) -> str:
        """Return this edit job's candidate CAD source path.

        The candidate ID is the edit job ID because the CAD validator only
        accepts sources under that exact prefix.
        """

        return (
            f"{job['project_id']}/candidates/cad/"
            f"{job['resolved_part_id']}/{job['id']}/model.py"
        )

    @staticmethod
    def _original_path(job: dict[str, Any]) -> str:
        """Return the path this edit job's pre-edit canonical source is backed up to."""

        return (
            f"{job['project_id']}/candidates/cad/"
            f"{job['resolved_part_id']}/{job['id']}/original/model.py"
        )

    @staticmethod
    def _attempt_candidate_path(job: dict[str, Any], attempt_number: int) -> str:
        """Return the candidate path ``queue_edit_candidate_validation`` expects.

        The RPC recomputes and enforces this exact prefix server-side.
        """

        return (
            f"{job['project_id']}/candidates/cad/"
            f"{job['resolved_part_id']}/{job['id']}/attempt-{attempt_number}/model.py"
        )

    def _prepare_candidate(self, job: dict[str, Any]) -> tuple[str, str]:
        """Ensure an edit-scoped candidate exists and return its path and hash.

        Tools only ever read and write the candidate, never canonical source.
        An existing candidate is reused untouched so a resumed job keeps the
        work a previous worker already applied; storage writes upsert, so
        re-copying canonical here would silently discard it.
        """

        edit_job_id = str(job["id"])
        candidate_path = self._candidate_path(job)
        try:
            source = self.repository.read_text(candidate_path)
        except WorkflowFailure as exc:
            if exc.code != "SOURCE_MISSING":
                raise
            accepted_path = cad_source_storage_path(
                str(job["project_id"]),
                str(job["resolved_part_id"]),
            )
            try:
                source = self.repository.read_text(accepted_path)
            except WorkflowFailure as accepted_exc:
                raise WorkflowFailure(
                    "CANDIDATE_BOOTSTRAP_FAILED",
                    "Accepted CAD source could not be copied to an edit candidate.",
                    details={"code": accepted_exc.code, "path": accepted_path},
                ) from accepted_exc
            self.repository.write_text(candidate_path, source)
            accepted_sha256 = source_hash(source)
            original_path = self._original_path(job)
            self._patch(
                edit_job_id,
                {
                    "accepted_source_sha256": accepted_sha256,
                    "original_storage_path": original_path,
                },
            )
            self.repository.write_text(original_path, source)
            backup = self.repository.read_text(original_path)
            if source_hash(backup) != accepted_sha256:
                raise WorkflowFailure(
                    "ORIGINAL_BACKUP_MISMATCH",
                    "Original source backup could not be verified.",
                )
        candidate_sha256 = source_hash(source)
        # current_candidate_path/current_candidate_sha256 are only settable via
        # queue_edit_candidate_validation, which also queues a validation job --
        # applied later by _validate_candidate. The candidate path is cheap to
        # recompute deterministically on resume, so it is not persisted here.
        return candidate_path, candidate_sha256

    def _step_blocked_on_missing_geometry(
        self,
        job: dict[str, Any],
        active_step: PlanStep,
        goal: CadGoal,
    ) -> bool:
        """Return whether a completion request for ``active_step`` should be
        rejected because the candidate has no CAD features yet.

        Only applies when the step addresses a ``required`` criterion. Reads
        the candidate fresh rather than tracking feature creation incrementally,
        so it's correct regardless of how the candidate reached its current
        state (a tool call earlier this step, a resumed candidate, etc.). A
        candidate that can't be read fails open -- that's a bigger problem the
        next tool call will also hit, not something this gate should mask.
        """

        if not _step_needs_new_geometry(active_step, goal):
            return False
        try:
            source = self.repository.read_text(self._candidate_path(job))
        except WorkflowFailure:
            return False
        return _count_cad_features(source) == 0

    @staticmethod
    def _redundant_call_rejection(
        tool_id: str,
        arguments: dict[str, Any],
        observations: list[dict[str, Any]],
    ) -> Any | None:
        """Return a rejection for a read-only call that cannot add new information.

        Two distinct redundancy shapes are checked, since a flat "same tool,
        same step" rule would either miss or over-block real cases:

        - ``index_search``/``index_get_feature``: an exact match of both tool
          ID and arguments anywhere in this step's observations is always
          redundant -- the semantic index and query semantics do not change
          mid-step, so a repeat can only return what an existing observation
          already contains.
        - ``check_geometry``: its arguments are always empty, so "same
          arguments" cannot distinguish calls. Instead, scan backwards for the
          most recent observation that is either a prior check_geometry call
          or a candidate-mutating tool call. If the most recent one found is a
          check_geometry call, nothing has changed the candidate since that
          observation, so this repeat cannot report anything different. A
          mutating call found first means the candidate has moved on since
          the last check, so this call is left alone -- as is the very first
          check_geometry call of a step, since an earlier step may have
          mutated the candidate without ever confirming the result.
        """

        if tool_id in READ_ONLY_DEDUPE_TOOL_IDS:
            for observation in observations:
                if (
                    observation.get("tool_id") == tool_id
                    and observation.get("arguments") == arguments
                ):
                    return tool_failure(
                        "TOOL_CALL_REDUNDANT",
                        f"{tool_id} was already called this step with these "
                        "exact arguments; use that existing observation "
                        "instead of calling it again.",
                    )
            return None

        if tool_id == GEOMETRY_CHECK_TOOL_ID:
            for observation in reversed(observations):
                observed_tool_id = observation.get("tool_id")
                if observed_tool_id == GEOMETRY_CHECK_TOOL_ID:
                    return tool_failure(
                        "TOOL_CALL_REDUNDANT",
                        "check_geometry was already called since the last "
                        "candidate mutation; nothing has changed since that "
                        "observation. Mutate the candidate first, or use the "
                        "existing observation.",
                    )
                if observed_tool_id in CANDIDATE_MUTATING_TOOL_IDS:
                    return None
            return None

        return None

    def _validate_batch(
        self,
        batch: ToolCallBatch,
        allowed_tool_ids: set[str],
    ) -> list[dict[str, Any]] | None:
        """Validate one model response's full batch of requested tool calls.

        Checks run in order; the first violation rejects the WHOLE batch --
        nothing in it is dispatched. Returns None when the batch is valid
        (always true for a single call, unless it has an invalid call ID, an
        unavailable tool, or -- impossible at size 1 -- exceeds the batch
        size cap). Returns a ready-to-send function_call_output list, one
        per call, every one carrying the SAME structured rejection,
        otherwise.
        """

        calls = batch.calls

        seen_call_ids: set[str] = set()
        for call in calls:
            if not call.call_id or call.call_id in seen_call_ids:
                return _batch_rejection_outputs(
                    batch,
                    tool_failure(
                        "TOOL_BATCH_REJECTED",
                        "Every tool call in a batch must have a unique, "
                        "non-empty call ID.",
                        details={"reason": "invalid_call_id"},
                    ),
                )
            seen_call_ids.add(call.call_id)

        for call in calls:
            if allowed_tool_ids and call.tool_id not in allowed_tool_ids:
                return _batch_rejection_outputs(
                    batch,
                    tool_failure(
                        "TOOL_NOT_FOUND",
                        "The requested tool is unavailable.",
                    ),
                )

        if len(calls) > MAX_TOOL_CALLS_PER_BATCH:
            return _batch_rejection_outputs(
                batch,
                tool_failure(
                    "TOOL_BATCH_REJECTED",
                    f"A batch may request at most {MAX_TOOL_CALLS_PER_BATCH} "
                    "tool calls.",
                    details={
                        "reason": "batch_too_large",
                        "batch_size": len(calls),
                        "max_batch_size": MAX_TOOL_CALLS_PER_BATCH,
                    },
                ),
            )

        if len(calls) > 1:
            non_batchable = sorted(
                {
                    call.tool_id
                    for call in calls
                    if not self.tool_executor.is_batchable(call.tool_id)
                }
            )
            if non_batchable:
                return _batch_rejection_outputs(
                    batch,
                    tool_failure(
                        "TOOL_BATCH_REJECTED",
                        "Every call in a multi-call batch must be "
                        "individually batchable; call these tools one at a "
                        "time instead: " + ", ".join(non_batchable) + ".",
                        details={"reason": "not_batchable", "tool_ids": non_batchable},
                    ),
                )

        return None

    def _trace_rejected_batch(
        self,
        edit_job_id: str,
        goal: CadGoal,
        plan: CadPlan,
        active_step: PlanStep,
        agent_turn: int,
        step_attempt: int,
        reasoning_round: int,
        decision: Any,
        batch: ToolCallBatch,
        rejection_outputs: list[dict[str, Any]],
    ) -> None:
        """Trace every call in a wholesale-rejected batch as a failed tool call.

        Reuses the existing tool.started/tool.completed event types (rather
        than a new event type) so the trace stays a complete ledger of what
        the model tried to call, even though nothing was actually dispatched.
        """

        rejection_result = json.loads(rejection_outputs[0]["output"])
        for call in batch.calls:
            tool_call_correlation = dict(
                goal_id=str(goal.goal_id),
                plan_id=str(plan.plan_id),
                step_id=active_step.step_id,
                agent_turn=agent_turn,
                step_attempt=step_attempt,
                reasoning_round=reasoning_round,
                response_id=_field(decision, "id"),
                tool_call_id=call.call_id,
            )
            self._trace(
                "tool.started",
                edit_job_id,
                tool_id=call.tool_id,
                arguments=call.raw_arguments,
                **tool_call_correlation,
            )
            self._trace(
                "tool.completed",
                edit_job_id,
                tool_id=call.tool_id,
                ok=False,
                result=rejection_result,
                duration_seconds=0.0,
                **tool_call_correlation,
            )

    def _dispatch_call(
        self,
        call: ToolCallRequest,
        tool_context: ToolExecutionContext,
        observations: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any], Any]:
        """Run one already-batch-validated tool call and return its ID,
        arguments, and result.

        Batch validation (_validate_batch) already guarantees this call's
        tool ID is allowed and, for a multi-call batch, batchable -- this
        method does not re-check either. A call whose arguments failed to
        parse at batch-construction time becomes a TOOL_INPUT_INVALID
        result without dispatching. A read-only call that cannot produce
        information beyond what this step's observations already contain
        is rejected the same way, without dispatching to the tool.
        """

        if call.parse_error is not None:
            return call.tool_id, {}, tool_failure("TOOL_INPUT_INVALID", call.parse_error)
        redundant = self._redundant_call_rejection(call.tool_id, call.arguments, observations)
        if redundant is not None:
            return call.tool_id, call.arguments, redundant
        return call.tool_id, call.arguments, self.tool_executor.execute_sync(
            tool_id=call.tool_id,
            raw_arguments=call.arguments,
            context=tool_context,
        )

    @staticmethod
    def _other_parts_inventory(tool_context: ToolExecutionContext) -> list[dict[str, Any]]:
        """Other parts' known features and parameters from the project's
        semantic index.

        Call once per job -- the persisted index only changes at reindex
        time, well outside a running job. Excludes the part this job is
        editing: that part's live state comes from
        ``_current_part_inventory`` instead, never from this index, since the
        index can be stale relative to this job's own in-progress edits.
        Degrades to ``[]`` on any index problem (missing, malformed, or
        cross-project) rather than failing the job -- an inventory roster is
        an optimization, not a correctness requirement, the same stance
        ``IndexSearchTool``/``IndexGetFeatureTool`` already take on these same
        failure modes. Unlike the live candidate scan, the index has no
        default value for a parameter, only its declared type.
        """

        try:
            index = _load_semantic_index(tool_context)
        except WorkflowFailure:
            return []
        return [
            {
                "part_id": part["part_id"],
                "part_name": part["part_name"],
                "features": [
                    {
                        "semantic_id": _index_text(feature.get("semantic_id")),
                        "function_name": _index_text(feature.get("function_name")),
                        "role": _index_text(feature.get("role")),
                    }
                    for feature in part["cad_parts"]
                    if _index_text(feature.get("semantic_id"))
                ],
                "parameters": [
                    {
                        "name": _index_text(parameter.get("name")),
                        "type_name": _index_text(parameter.get("type_name")),
                    }
                    for parameter in part["model_params"]
                    if _index_text(parameter.get("name"))
                ],
            }
            for part in _semantic_index_parts(index)
            if part["part_id"] != tool_context.part_id
        ]

    def _current_part_inventory(self, job: dict[str, Any]) -> dict[str, Any]:
        """The part being edited's live features and parameters, scanned
        fresh from the candidate.

        Call at the start of every step's chain, never once per job: the
        candidate mutates as this job's own tool calls run
        (``create_feature``, ``create_parameter``, ``edit_cad_build_model``),
        so a later step in the same job must see what an earlier step in the
        same job just built -- something the persisted semantic index cannot
        supply, since it only reflects state as of the last reindex. A
        candidate that can't yet be read fails open to empty feature and
        parameter lists, mirroring ``_step_blocked_on_missing_geometry``'s
        convention.
        """

        resolved_targets = list(job.get("resolved_targets") or [])
        part_name = (
            _index_text(resolved_targets[0].get("part_name")) if resolved_targets else ""
        )
        try:
            source = self.repository.read_text(self._candidate_path(job))
        except WorkflowFailure:
            features: list[dict[str, str]] = []
            parameters: list[dict[str, str]] = []
        else:
            features = _extract_cad_features(source)
            parameters = _extract_model_params(source)
        return {
            "part_id": str(job["resolved_part_id"]),
            "part_name": part_name,
            "features": features,
            "parameters": parameters,
        }

    def _run_agent_loop(
        self,
        job: dict[str, Any],
        goal: CadGoal,
        plan: CadPlan,
    ) -> CadPlan:
        """Drive the plan to completion one continuous reasoning chain per
        active step.

        The plan is the loop's engine: each pass works the earliest unfinished
        step until the agent reports it complete, then advances. The loop ends
        when no step remains active. A step's reasoning chain -- and the
        orchestrator-local ``observations`` bookkeeping used for redundant-call
        rejection -- are scoped to that step and reset whenever it changes, so
        a later step never inherits an earlier step's chain or tool transcript.
        """

        edit_job_id = str(job["id"])
        tool_context = ToolExecutionContext(
            run_id=str(plan.plan_id),
            project_id=str(job["project_id"]),
            part_id=str(job["resolved_part_id"]),
            candidate_id=edit_job_id,
            services=ToolServices(repository=self.repository),
        )
        other_parts_inventory = self._other_parts_inventory(tool_context)
        recent_messages = _recent_messages(job.get("messages"))
        allowed_tool_ids = {
            str(definition["name"])
            for definition in getattr(self.agent_3d, "tool_catalog", ())
            if isinstance(definition, dict) and definition.get("name")
        }
        observations: list[dict[str, Any]] = []
        current_step_id: str | None = None
        previous_response_id: str | None = None
        pending_tool_outputs: list[dict[str, Any]] = []
        agent_turn = 0
        step_attempt = 0
        reasoning_round = 0

        while True:
            active_step = _active_step(plan)
            if active_step is None:
                LOGGER.info(
                    "agent loop finished edit_job_id=%s plan_id=%s turns=%s",
                    edit_job_id,
                    plan.plan_id,
                    agent_turn,
                )
                self._trace(
                    "agent_loop.completed",
                    edit_job_id,
                    goal_id=str(goal.goal_id),
                    plan_id=str(plan.plan_id),
                    agent_turn=agent_turn,
                )
                return plan

            if active_step.step_id != current_step_id:
                previous_step_id = current_step_id
                current_step_id = active_step.step_id
                previous_response_id = None
                pending_tool_outputs = []
                step_attempt = 0
                reasoning_round = 0
                observations = []
                if active_step.status == "pending":
                    plan = self._set_step_status(
                        edit_job_id,
                        plan,
                        active_step.step_id,
                        "in_progress",
                    )
                    active_step = _active_step(plan) or active_step
                self._transition(
                    edit_job_id,
                    "applying_edit",
                    "tools.started",
                    f"Working on plan step {active_step.step_id}.",
                    metadata={
                        "step_id": active_step.step_id,
                        "objective": active_step.objective,
                    },
                )
                self._trace(
                    "step.started" if previous_step_id is None else "step.changed",
                    edit_job_id,
                    goal_id=str(goal.goal_id),
                    plan_id=str(plan.plan_id),
                    step_id=active_step.step_id,
                    previous_step_id=previous_step_id,
                    objective=active_step.objective,
                    observation_count_before=len(observations),
                )

            starting_new_chain = previous_response_id is None
            agent_turn += 1
            if starting_new_chain:
                step_attempt += 1
                reasoning_round = 1
            else:
                reasoning_round += 1

            if agent_turn > MAX_AGENT_TURNS:
                self._trace(
                    "agent_loop.failed",
                    edit_job_id,
                    goal_id=str(goal.goal_id),
                    plan_id=str(plan.plan_id),
                    step_id=active_step.step_id,
                    agent_turn=agent_turn,
                    step_attempt=step_attempt,
                    reasoning_round=reasoning_round,
                    error_code="AGENT_TURN_LIMIT",
                )
                raise WorkflowFailure(
                    "AGENT_TURN_LIMIT",
                    "The agent loop exceeded its total decision limit.",
                    details={
                        "step_id": active_step.step_id,
                        "max_turns": MAX_AGENT_TURNS,
                    },
                )
            if reasoning_round > MAX_STEP_TURNS:
                self._trace(
                    "agent_loop.failed",
                    edit_job_id,
                    goal_id=str(goal.goal_id),
                    plan_id=str(plan.plan_id),
                    step_id=active_step.step_id,
                    agent_turn=agent_turn,
                    step_attempt=step_attempt,
                    reasoning_round=reasoning_round,
                    error_code="AGENT_STEP_TURN_LIMIT",
                )
                raise WorkflowFailure(
                    "AGENT_STEP_TURN_LIMIT",
                    "The agent loop exceeded its decision limit for one plan step.",
                    details={
                        "step_id": active_step.step_id,
                        "max_step_turns": MAX_STEP_TURNS,
                    },
                )

            decision_step = active_step
            trace_context = AgentTraceContext(
                edit_job_id=edit_job_id,
                agent_turn=agent_turn,
                step_attempt=step_attempt,
                reasoning_round=reasoning_round,
            )
            if starting_new_chain:
                decision_observations = list(observations)
                decision_project_inventory = {
                    "current_part": self._current_part_inventory(job),
                    "other_parts": other_parts_inventory,
                }
                decision = self._with_heartbeat(
                    edit_job_id,
                    lambda: self.agent_3d.start_step(
                        goal=goal,
                        plan=plan,
                        active_step=decision_step,
                        trace_context=trace_context,
                        recent_messages=recent_messages,
                        observations=decision_observations,
                        project_inventory=decision_project_inventory,
                    ),
                )
            else:
                decision_previous_response_id = previous_response_id
                decision_tool_outputs = list(pending_tool_outputs)
                decision = self._with_heartbeat(
                    edit_job_id,
                    lambda: self.agent_3d.continue_step(
                        goal=goal,
                        plan=plan,
                        active_step=decision_step,
                        trace_context=trace_context,
                        previous_response_id=decision_previous_response_id,
                        tool_outputs=decision_tool_outputs,
                    ),
                )
            previous_response_id = str(_field(decision, "id", "") or "") or None

            calls = [
                item
                for item in _output(decision)
                if _field(item, "type") == "function_call"
            ]
            if not calls:
                self._trace(
                    "agent_loop.failed",
                    edit_job_id,
                    goal_id=str(goal.goal_id),
                    plan_id=str(plan.plan_id),
                    step_id=active_step.step_id,
                    agent_turn=agent_turn,
                    step_attempt=step_attempt,
                    reasoning_round=reasoning_round,
                    response_id=_field(decision, "id"),
                    error_code="AGENT_NO_ACTION",
                )
                raise WorkflowFailure(
                    "AGENT_NO_ACTION",
                    "Agent3D requested no tool call for the active plan step.",
                    details={"step_id": active_step.step_id},
                )

            batch = ToolCallBatch(calls=tuple(_parse_call(item) for item in calls))
            rejection_outputs = self._validate_batch(batch, allowed_tool_ids)
            if rejection_outputs is not None:
                self._trace_rejected_batch(
                    edit_job_id,
                    goal,
                    plan,
                    active_step,
                    agent_turn,
                    step_attempt,
                    reasoning_round,
                    decision,
                    batch,
                    rejection_outputs,
                )
                pending_tool_outputs = rejection_outputs
                continue

            step_completed = False
            completion_summary = ""
            pending_tool_outputs = []
            for call in batch.calls:
                tool_call_id = call.call_id
                tool_call_correlation = dict(
                    goal_id=str(goal.goal_id),
                    plan_id=str(plan.plan_id),
                    step_id=active_step.step_id,
                    agent_turn=agent_turn,
                    step_attempt=step_attempt,
                    reasoning_round=reasoning_round,
                    response_id=_field(decision, "id"),
                    tool_call_id=tool_call_id,
                )
                self._trace(
                    "tool.started",
                    edit_job_id,
                    tool_id=call.tool_id,
                    arguments=call.raw_arguments,
                    **tool_call_correlation,
                )
                started_at = self.monotonic()
                tool_id, arguments, result = self._dispatch_call(
                    call,
                    tool_context,
                    observations,
                )
                duration_seconds = self.monotonic() - started_at
                LOGGER.info(
                    "agent tool call edit_job_id=%s step_id=%s turn=%s "
                    "tool_id=%s ok=%s",
                    edit_job_id,
                    active_step.step_id,
                    agent_turn,
                    tool_id,
                    result.ok,
                )
                observations.append(
                    {
                        "tool_id": tool_id,
                        "arguments": arguments,
                        "result": result.model_dump(mode="json"),
                    }
                )
                if tool_id == STEP_COMPLETION_TOOL_ID and result.ok:
                    if self._step_blocked_on_missing_geometry(job, active_step, goal):
                        result = tool_failure(
                            "STEP_REQUIRES_A_FEATURE",
                            "This part has no CAD features yet, and this step "
                            "addresses a required criterion. Call create_feature "
                            "(and create_parameter first if a new dimension is "
                            "needed) before requesting completion again.",
                        )
                        observations[-1]["result"] = result.model_dump(mode="json")
                        LOGGER.info(
                            "agent step completion blocked edit_job_id=%s "
                            "step_id=%s turn=%s reason=no_cad_features",
                            edit_job_id,
                            active_step.step_id,
                            agent_turn,
                        )
                    else:
                        step_completed = True
                        completion_summary = str(arguments.get("summary") or "")
                # Traced after the completion gate above so a blocked
                # request_step_completion call is recorded with the result
                # observations actually ended up with, not the pre-gate one.
                self._trace(
                    "tool.completed",
                    edit_job_id,
                    tool_id=tool_id,
                    ok=result.ok,
                    result=result.model_dump(mode="json"),
                    duration_seconds=duration_seconds,
                    **tool_call_correlation,
                )
                # Feeds the next continue_step call, if the chain keeps going
                # (i.e. this call didn't complete the step) -- built from the
                # same (possibly gate-rewritten) result just traced above, so
                # the model's next round sees exactly what was recorded.
                pending_tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call_id,
                        "output": json.dumps(
                            result.model_dump(mode="json"), ensure_ascii=False
                        ),
                    }
                )

            if step_completed:
                plan = self._set_step_status(
                    edit_job_id,
                    plan,
                    active_step.step_id,
                    "completed",
                )
                self._event(
                    edit_job_id,
                    "tools.completed",
                    "applying_edit",
                    f"Plan step {active_step.step_id} completed.",
                    {
                        "step_id": active_step.step_id,
                        "summary": completion_summary,
                        "turns": reasoning_round,
                    },
                )
                self._trace(
                    "step.completed",
                    edit_job_id,
                    goal_id=str(goal.goal_id),
                    plan_id=str(plan.plan_id),
                    step_id=active_step.step_id,
                    agent_turn=agent_turn,
                    step_attempt=step_attempt,
                    reasoning_round=reasoning_round,
                    summary=completion_summary,
                    observation_count=len(observations),
                )

    def _validate_candidate(
        self,
        job: dict[str, Any],
        candidate_path: str,
    ) -> tuple[dict[str, Any], str, str]:
        """Validate the loop's final candidate and return proof of that validation.

        ``queue_edit_candidate_validation`` enforces an ``attempt-{n}`` path
        server-side that the agent loop's stable candidate path doesn't use,
        so this copies the candidate's current content there before queuing
        -- content is byte-identical, so the hash validated is the hash the
        loop produced.
        """

        edit_job_id = str(job["id"])
        candidate_content = self.repository.read_text(candidate_path)
        candidate_sha256 = source_hash(candidate_content)
        attempt_number = int(job.get("attempt_count") or 0)
        if job.get("current_candidate_sha256") != candidate_sha256:
            # A resume validating the exact content a prior attempt already
            # queued reuses that attempt number/path so the RPC's own
            # idempotent-reuse check (matched on path *and* hash) returns the
            # existing validation job instead of queuing a duplicate.
            attempt_number += 1
        attempt_path = self._attempt_candidate_path(job, attempt_number)
        self.repository.write_text(attempt_path, candidate_content)

        self._transition(
            edit_job_id,
            "validating_candidate",
            "validation.started",
            f"Validating candidate attempt {attempt_number}.",
            metadata={"attempt": attempt_number},
        )
        validation_job_id = self.repository.queue_validation(
            edit_job_id,
            attempt_path,
            candidate_sha256,
            attempt_number,
            worker_id=self.worker_id,
        )
        validation_job = self._wait_for_job(
            edit_job_id,
            validation_job_id,
            self.repository.generation_job,
            "CAD validation",
        )
        report = _record(validation_job.get("result"))
        passed = (
            validation_job.get("status") == "completed"
            and report.get("status") == "passed"
            and report.get("valid") is True
            and validation_job.get("source_storage_path") == attempt_path
            and validation_job.get("source_sha256") == candidate_sha256
            and str(validation_job.get("edit_job_id")) == edit_job_id
        )
        self._event(
            edit_job_id,
            "validation.passed" if passed else "validation.failed",
            "validating_candidate",
            f"Candidate attempt {attempt_number} "
            f"{'passed' if passed else 'failed'} validation.",
            {
                "attempt": attempt_number,
                "validation_job_id": validation_job_id,
                "candidate_sha256": candidate_sha256,
            },
        )
        if not passed:
            raise WorkflowFailure(
                "VALIDATION_FAILED",
                str(
                    validation_job.get("error_message")
                    or "The committed candidate failed validation."
                ),
                details={
                    "validation_job_id": validation_job_id,
                    "attempt": attempt_number,
                    "report": report,
                },
            )
        return validation_job, attempt_path, candidate_sha256

    def _commit_candidate(
        self,
        job: dict[str, Any],
        validation_job: dict[str, Any],
        attempt_path: str,
        candidate_sha256: str,
    ) -> str:
        """Write the validated candidate to canonical source, hash-guarded.

        Ports the legacy TS orchestrator's ``commit()``: re-verifies the
        validation actually proves *this* candidate, then only writes
        canonical if it still matches the source this job started from --
        reading, writing, and re-reading are all hash-checked so a
        concurrent change to either side is caught rather than silently
        overwritten.
        """

        edit_job_id = str(job["id"])
        report = _record(validation_job.get("result"))
        if (
            validation_job.get("status") != "completed"
            or report.get("status") != "passed"
            or report.get("valid") is not True
            or validation_job.get("source_storage_path") != attempt_path
            or validation_job.get("source_sha256") != candidate_sha256
            or str(validation_job.get("edit_job_id")) != edit_job_id
        ):
            raise WorkflowFailure(
                "VALIDATION_PROOF_MISMATCH",
                "Validation does not prove the current candidate.",
            )

        self._transition(
            edit_job_id,
            "committing",
            "commit.started",
            "Committing the validated candidate.",
        )
        canonical_path = cad_source_storage_path(
            str(job["project_id"]),
            str(job["resolved_part_id"]),
        )
        canonical_sha256 = source_hash(self.repository.read_text(canonical_path))
        if canonical_sha256 == job.get("accepted_source_sha256"):
            candidate_content = self.repository.read_text(attempt_path)
            if source_hash(candidate_content) != candidate_sha256:
                raise WorkflowFailure(
                    "CANDIDATE_HASH_MISMATCH",
                    "Candidate content changed after validation.",
                )
            self.repository.write_text(canonical_path, candidate_content)
            written_sha256 = source_hash(self.repository.read_text(canonical_path))
            if written_sha256 != candidate_sha256:
                raise WorkflowFailure(
                    "STORAGE_WRITE_MISMATCH",
                    "Committed source could not be hash-verified.",
                )
        elif canonical_sha256 != candidate_sha256:
            raise WorkflowFailure(
                "SOURCE_CHANGED",
                "Accepted source changed before commit.",
            )
        self._event(
            edit_job_id,
            "commit.completed",
            "committing",
            "Validated CAD source committed.",
        )
        return canonical_path

    def _rollback_commit(
        self,
        job: dict[str, Any],
        canonical_path: str,
        candidate_sha256: str,
    ) -> bool:
        """Restore canonical source from its pre-edit backup, if it's safe to.

        Only rolls back when canonical still matches the candidate this job
        just committed -- if something else already changed it, overwriting
        could destroy that other change. Ports the legacy TS ``rollback()``.
        """

        canonical_sha256 = source_hash(self.repository.read_text(canonical_path))
        if canonical_sha256 != candidate_sha256:
            return False
        original_path = self._original_path(job)
        original = self.repository.read_text(original_path)
        if source_hash(original) != job.get("accepted_source_sha256"):
            raise WorkflowFailure(
                "ROLLBACK_SOURCE_MISMATCH",
                "Original source backup no longer matches.",
            )
        self.repository.write_text(canonical_path, original)
        return True

    def _reindex_after_commit(
        self,
        job: dict[str, Any],
        canonical_path: str,
        candidate_sha256: str,
    ) -> str:
        edit_job_id = str(job["id"])
        self._transition(
            edit_job_id,
            "reindexing",
            "reindex.started",
            "Reindexing committed CAD source.",
        )
        index_job_id = self.repository.queue_index(
            edit_job_id,
            "reindexing",
            worker_id=self.worker_id,
        )
        index_job = self._wait_for_job(
            edit_job_id,
            index_job_id,
            self.repository.index_job,
            "Post-commit index build",
        )
        if index_job.get("status") != "completed":
            rolled_back = self._rollback_commit(job, canonical_path, candidate_sha256)
            raise WorkflowFailure(
                "REINDEX_FAILED",
                str(index_job.get("error_message") or "Post-commit reindex failed."),
                details={"index_job_id": index_job_id, "rolled_back": rolled_back},
            )
        self._event(
            edit_job_id,
            "reindex.completed",
            "reindexing",
            "Committed source indexed.",
            {"index_job_id": index_job_id},
        )
        return index_job_id

    def _queue_export_after_commit(
        self,
        job: dict[str, Any],
        candidate_sha256: str,
    ) -> tuple[str | None, list[str]]:
        """Queue CAD export, best-effort -- a failure here warns, not fails, the job."""

        edit_job_id = str(job["id"])
        existing = job.get("export_job_id")
        if existing:
            return str(existing), []
        try:
            export_job_id = self.repository.queue_export(
                edit_job_id,
                candidate_sha256,
                worker_id=self.worker_id,
            )
        except Exception as exc:
            if isinstance(exc, WorkflowFailure) and exc.code == "EDIT_LEASE_LOST":
                raise
            warning = f"CAD export could not be queued; run /export manually: {exc}"
            self._event(
                edit_job_id,
                "export.warning",
                "queueing_export",
                warning,
                {"warning": warning},
            )
            return None, [warning]
        self._event(
            edit_job_id,
            "export.queued",
            "queueing_export",
            "CAD export queued.",
            {"export_job_id": export_job_id},
        )
        return export_job_id, []

    def _complete_agent_run(
        self,
        job: dict[str, Any],
        goal: CadGoal,
        high_level_plan: CadPlan,
        planning_log_path: str,
        canonical_path: str,
        candidate_sha256: str,
        index_job_id: str,
        export_job_id: str | None,
        export_warnings: list[str],
        validation_job: dict[str, Any],
    ) -> dict[str, Any]:
        """Finalize after the agent loop's candidate has been committed."""

        semantic_ids = [
            binding.semantic_id for binding in high_level_plan.target_bindings
        ]
        result = {
            "schema_version": 1,
            "status": "completed",
            "outcome": "committed",
            "message": high_level_plan.summary,
            "goal": goal.model_dump(mode="json"),
            "high_level_plan": high_level_plan.model_dump(mode="json"),
            "planning_log_path": planning_log_path,
            "attempts": int(job.get("attempt_count") or 0),
            "resolved_target": {
                "part_id": job.get("resolved_part_id")
                or job.get("requested_part_id"),
                "semantic_ids": semantic_ids,
            },
            "changed_files": [canonical_path],
            "changed_symbols": [],
            "source_sha256": candidate_sha256,
            "validation_result": _record(validation_job.get("result")),
            "index_job_id": index_job_id,
            "export_job_id": export_job_id,
            "warnings": export_warnings,
        }
        self.repository.complete_edit_job(
            str(job["id"]),
            result,
            worker_id=self.worker_id,
        )
        return result

    def _run(self, claimed_job: dict[str, Any]) -> dict[str, Any]:
        edit_job_id = str(claimed_job["id"])
        self._heartbeat(edit_job_id)
        job = self.repository.edit_job(edit_job_id)

        if self._history_event(job, "job_started") is None:
            self._event(
                edit_job_id,
                "job.started",
                str(job.get("state") or "received"),
                "CAD edit processing started.",
                {
                    "workflow_mode": str(job.get("workflow_mode") or "edit"),
                    "part_id": job.get("requested_part_id"),
                },
            )
            job = self._history(edit_job_id, {"event": "job_started"})

        goal = self._goal(job)
        if goal.clarification.required:
            raise WorkflowFailure(
                "CLARIFICATION_REQUIRED",
                goal.clarification.question,
                details={
                    "question": goal.clarification.question,
                    "reason": goal.clarification.reason,
                    "goal_id": str(goal.goal_id),
                },
            )
        job = self.repository.edit_job(edit_job_id)
        force_index = str(job.get("workflow_mode") or "edit") == "initial_design"
        self._ensure_fresh_index(job, force_build=force_index)
        job = self.repository.edit_job(edit_job_id)
        job = self._resolve_planning_part(job)
        high_level_plan = self._high_level_plan(job, goal)
        job = self.repository.edit_job(edit_job_id)
        try:
            planning_log_path = self.planning_log_writer.write(
                job=job,
                goal=goal,
                plan=high_level_plan,
            )
        except Exception as exc:
            raise WorkflowFailure(
                "PLANNING_LOG_WRITE_FAILED",
                "The CAD planning log could not be written.",
                details={"exception_type": type(exc).__name__},
            ) from exc

        candidate_path, _base_sha256 = self._prepare_candidate(job)
        job = self.repository.edit_job(edit_job_id)
        high_level_plan = self._run_agent_loop(job, goal, high_level_plan)
        job = self.repository.edit_job(edit_job_id)

        validation_job, attempt_path, candidate_sha256 = self._validate_candidate(
            job, candidate_path
        )
        job = self.repository.edit_job(edit_job_id)
        canonical_path = self._commit_candidate(
            job, validation_job, attempt_path, candidate_sha256
        )
        job = self.repository.edit_job(edit_job_id)
        index_job_id = self._reindex_after_commit(
            job, canonical_path, candidate_sha256
        )
        job = self.repository.edit_job(edit_job_id)
        export_job_id, export_warnings = self._queue_export_after_commit(
            job, candidate_sha256
        )

        return self._complete_agent_run(
            job,
            goal,
            high_level_plan,
            planning_log_path,
            canonical_path,
            candidate_sha256,
            index_job_id,
            export_job_id,
            export_warnings,
            validation_job,
        )

    def _handle_failure(
        self,
        claimed_job: dict[str, Any],
        failure: WorkflowFailure,
    ) -> dict[str, Any]:
        if failure.code == "EDIT_LEASE_LOST":
            raise failure
        edit_job_id = str(claimed_job["id"])
        latest = self.repository.edit_job(edit_job_id)
        failed_state = str(latest.get("state") or "unknown")
        result = {
            "schema_version": 1,
            "status": "failed",
            "message": str(failure),
            **self._planning_result(latest),
            "error_code": failure.code,
            "state": failed_state,
            "attempts": 0,
            "resolved_targets": list(latest.get("resolved_targets") or []),
            "details": failure.details,
            "warnings": [],
        }
        self.repository.fail_edit_job(
            edit_job_id,
            code=failure.code,
            message=str(failure),
            result=result,
            worker_id=self.worker_id,
        )
        return result

    def run(self, claimed_job: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run(claimed_job)
        except WorkflowFailure as failure:
            return self._handle_failure(claimed_job, failure)
        except Exception as exc:
            LOGGER.exception(
                "Unhandled CAD editor workflow exception for edit job %s.",
                claimed_job.get("id"),
            )
            failure = WorkflowFailure(
                "WORKFLOW_INTERNAL_ERROR",
                "The CAD editor failed unexpectedly.",
                details={"exception_type": type(exc).__name__},
            )
            return self._handle_failure(claimed_job, failure)
