from __future__ import annotations

import copy
import json
import os
import re
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.orchestrator import (
    MAX_AGENT_TURNS,
    MAX_REPAIR_ATTEMPTS,
    MAX_REPAIR_TURNS,
    MAX_STEP_TURNS,
    MAX_STEP_VALIDATION_REJECTIONS,
    MAX_TOOL_CALLS_PER_BATCH,
    EditWorkflowOrchestrator,
    ToolCallBatch,
    ToolCallRequest,
    _active_step,
    _count_cad_features,
    _extract_cad_features,
    _extract_model_params,
    _parse_call,
    _recent_messages,
    _step_needs_new_geometry,
    _with_step_status,
)
from workers.agent_3d.planning.agent_contracts import (
    CadGoal,
    CadPlan,
    CadPlanModelOutput,
)
from workers.agent_3d.tools.hashing import source_hash
from workers.agent_3d.tools import (
    CreateFeatureTool,
    CreateParameterTool,
    EditCadBuildModelTool,
    IndexGetFeatureTool,
    RequestStepCompletionTool,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
    ToolServices,
)


def _constraint_values(anchor: str) -> frozenset[str]:
    """Read an `in (...)` CHECK list out of the migrations.

    Parsed from the SQL rather than duplicated here on purpose: a hardcoded
    copy drifts silently, and the whole point of checking these in the fake
    is to fail when code emits a value the database would reject. The last
    migration that defines the constraint wins, matching how they apply.
    """

    migrations = sorted(
        (Path(__file__).resolve().parents[1] / "supabase" / "migrations").glob("*.sql")
    )
    for path in reversed(migrations):
        text = path.read_text(encoding="utf-8")
        index = text.rfind(anchor)
        if index == -1:
            continue
        body = text[index + len(anchor):]
        body = body[: body.index(")")]
        values = frozenset(re.findall(r"'([a-z_]+(?:\.[a-z_]+)?)'", body))
        if values:
            return values
    raise AssertionError(f"No migration defines a CHECK list for {anchor!r}.")


ALLOWED_EVENT_TYPES = _constraint_values("event_type in (")
ALLOWED_JOB_STATES = _constraint_values("state in (")

PROJECT_ID = "22222222-2222-4222-8222-222222222222"
PART_ID = "11111111-1111-4111-8111-111111111111"
EDIT_JOB_ID = "33333333-3333-4333-8333-333333333333"
GOAL_ID = "44444444-4444-4444-8444-444444444444"
ACCEPTED_SOURCE_PATH = f"{PROJECT_ID}/parts/cad/{PART_ID}/model.py"
CANDIDATE_PATH = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/model.py"
ACCEPTED_SOURCE = (
    "from cadquery_runtime import cad_part, cq, dataclass\n"
    "\n"
    "@dataclass(frozen=True)\n"
    "class ModelParams:\n"
    "    bracket_length_mm: float = 100.0\n"
    "\n"
    "\n"
    "@cad_part(\n"
    '    semantic_id="bracket_body",\n'
    '    role="primary_body",\n'
    '    library="cadquery",\n'
    '    parameters=("bracket_length_mm",),\n'
    "    depends_on=(),\n"
    '    search_keys=("bracket", "body"),\n'
    ")\n"
    "def build_bracket_body(params: ModelParams):\n"
    '    return cq.Workplane("XY").box(params.bracket_length_mm, 20, 5)\n'
    "\n"
    "\n"
    "def build_model(params: ModelParams):\n"
    "    body = build_bracket_body(params)\n"
    "    return body\n"
)

# The genuinely empty skeleton a brand-new part starts with -- used by the
# empty-part completion-gate tests, which need zero @cad_part features.
EMPTY_SKELETON_SOURCE = (
    "from cadquery_runtime import cad_part, cq, dataclass\n"
    "\n"
    "@dataclass(frozen=True)\n"
    "class ModelParams:\n"
    "    pass\n"
    "\n"
    "\n"
    "def build_model(params: ModelParams):\n"
    '    return cq.Workplane("XY")\n'
)


def goal() -> CadGoal:
    return CadGoal.model_validate(
        {
            "goal_id": GOAL_ID,
            "raw_request": "Make the bracket taller",
            "description": "Increase bracket height without changing its holes.",
            "completion_criteria": [
                {
                    "criterion_id": "GC-1",
                    "description": "The bracket is taller.",
                    "type": "required",
                }
            ],
            "constraints": ["Preserve the mounting holes."],
            "assumptions": [],
            "clarification": {
                "required": False,
                "question": None,
                "reason": None,
            },
        }
    )


def clarification_goal() -> CadGoal:
    payload = goal().model_dump(mode="json")
    payload.update(
        completion_criteria=[],
        clarification={
            "required": True,
            "question": "Which bracket face should become taller?",
            "reason": "Two faces match the request.",
        },
    )
    return CadGoal.model_validate(payload)


def plan_output() -> CadPlanModelOutput:
    return CadPlanModelOutput.model_validate(
        {
            "plan_id": "model-echo-is-not-authoritative",
            "goal_id": "model-echo-is-not-authoritative",
            "version": 99,
            "summary": "Update the linked bracket and verify the result.",
            "target_bindings": [],
            "steps": [
                {
                    "step_id": "PS-1",
                    "sequence": 1,
                    "objective": "Change the bracket height.",
                    "depends_on": [],
                    "addresses_criteria": ["GC-1"],
                    "status": "pending",
                }
            ],
        }
    )


# Validation report builders. These mirror the real schema-2 shape the CAD
# validator emits (workers/cad_validator/cad_ast_validator.py), because the
# orchestrator's classification reads schema_version, repairable_hint, and
# diagnostics -- a looser stub would silently exercise a different branch
# than the one a test means to cover.
def passed_report(solid_count: int = 1, **geometry: object) -> dict:
    return {
        "schema_version": 2,
        "status": "passed",
        "stage": "completed",
        "repairable_hint": False,
        "diagnostics": [],
        "valid": True,
        "safe_to_execute": True,
        "build_artifacts": {
            "solid_count": solid_count,
            "result_type": "cadquery.Workplane",
            **geometry,
        },
    }


def repairable_report(
    error_code: str = "CADQUERY_RUNTIME_ERROR",
    message: str = "Shape is not valid.",
    **diagnostic: object,
) -> dict:
    """A failure the agent can act on: the candidate's own source is at fault."""

    return {
        "schema_version": 2,
        "status": "failed",
        "stage": "cadquery_runtime",
        "repairable_hint": True,
        "diagnostics": [
            {
                "error_code": error_code,
                "message": message,
                "stage": "cadquery_runtime",
                "file_path": "model.py",
                "related_symbols": ["make_bracket"],
                **diagnostic,
            }
        ],
        "valid": False,
        "safe_to_execute": True,
    }


def timeout_report() -> dict:
    """Infrastructure failure: nothing here describes a fixable source defect."""

    return {
        "schema_version": 2,
        "status": "failed",
        "stage": "cadquery_runtime",
        "repairable_hint": False,
        "diagnostics": [
            {
                "error_code": "VALIDATION_TIMEOUT",
                "message": "Validation exceeded its time budget.",
                "stage": "cadquery_runtime",
                "file_path": "model.py",
                "related_symbols": [],
            }
        ],
        "valid": False,
        "safe_to_execute": True,
    }


def worker_error_report() -> dict:
    return {
        "schema_version": 2,
        "status": "failed",
        "stage": "worker",
        "repairable_hint": False,
        "diagnostics": [
            {
                "error_code": "VALIDATION_WORKER_ERROR",
                "message": "The validation worker crashed.",
                "stage": "worker",
                "file_path": "model.py",
                "related_symbols": [],
            }
        ],
        "valid": False,
    }


class FakeRepository:
    def __init__(self):
        self.jobs = {
            EDIT_JOB_ID: {
                "id": EDIT_JOB_ID,
                "project_id": PROJECT_ID,
                "request_text": "Make the bracket taller",
                "messages": [],
                "requested_part_id": PART_ID,
                "resolved_part_id": PART_ID,
                "resolved_targets": [
                    {
                        "part_id": PART_ID,
                        "part_name": "Bracket",
                        "semantic_ids": ["bracket_body"],
                        "confidence": 1,
                        "reason": "The linked CAD part is authoritative.",
                        "candidates": [],
                    }
                ],
                "workflow_mode": "edit",
                "status": "running",
                "state": "received",
                "attempt_count": 0,
                "max_attempts": 3,
                "index_job_id": None,
                "history": [],
            }
        }
        self.events: list[dict] = []
        self.mutations: list[str] = []
        self.index_jobs: dict[str, dict] = {}
        self.heartbeat_count = 0
        self.index_count = 0
        self.files: dict[str, str] = {ACCEPTED_SOURCE_PATH: ACCEPTED_SOURCE}
        self.writes: list[str] = []
        # Post-loop commit pipeline test knobs -- override before calling
        # orchestrator.run() to script validation/reindex/export outcomes.
        self.generation_jobs: dict[str, dict] = {}
        self.generation_count = 0
        self.export_count = 0
        self.validation_status = "completed"
        self.validation_result: dict = passed_report()
        # Scripts consecutive validation runs. Each entry may carry any of
        # status / result / source_storage_path / source_sha256 /
        # error_message; anything omitted falls back to the flat knobs above.
        # Empty means "every run behaves the same".
        self.validation_outcomes: list[dict] = []
        self.index_should_fail = False
        self.export_should_fail = False
        self.validation_calls: list[dict] = []
        self.export_calls: list[dict] = []
        # Fires once queue_validation has recorded a completed validation job,
        # simulating a window where something else changes canonical source
        # before this job's commit runs.
        self.mutate_canonical_after_validation = None

    def read_text(self, path: str) -> str:
        try:
            return self.files[path]
        except KeyError as exc:
            raise WorkflowFailure(
                "SOURCE_MISSING",
                f"{path} was not found in project storage.",
            ) from exc

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content
        self.writes.append(path)

    @staticmethod
    def _owned(worker_id: str) -> None:
        if worker_id != "test-worker":
            raise AssertionError("edit mutation was not guarded by worker ownership")

    def edit_job(self, edit_job_id: str) -> dict:
        return copy.deepcopy(self.jobs[edit_job_id])

    def patch_edit_job(
        self,
        edit_job_id: str,
        values: dict,
        *,
        worker_id: str,
    ) -> dict:
        self._owned(worker_id)
        self.jobs[edit_job_id].update(copy.deepcopy(values))
        return self.edit_job(edit_job_id)

    def append_history(
        self,
        edit_job_id: str,
        event: dict,
        *,
        worker_id: str,
    ) -> dict:
        self._owned(worker_id)
        self.mutations.append(f"history:{event['event']}")
        self.jobs[edit_job_id]["history"].append(copy.deepcopy(event))
        return self.edit_job(edit_job_id)

    def append_event(
        self,
        edit_job_id: str,
        *,
        worker_id: str,
        event_type: str,
        state: str,
        message: str,
        metadata: dict,
    ) -> dict:
        self._owned(worker_id)
        # edit_job_events.event_type and edit_jobs.state are closed CHECK
        # lists in the database. Enforcing them here is what stops a new
        # event type or state from passing every test and then failing on
        # its first real run -- exactly how validation.completed shipped
        # broken once.
        if event_type not in ALLOWED_EVENT_TYPES:
            raise AssertionError(
                f"event_type {event_type!r} is not in the "
                "edit_job_events_event_type_check constraint; add it in a "
                "migration before emitting it."
            )
        if state not in ALLOWED_JOB_STATES:
            raise AssertionError(
                f"state {state!r} is not in the edit_jobs state check "
                "constraint; add it in a migration before setting it."
            )
        event = {
            "edit_job_id": edit_job_id,
            "event_type": event_type,
            "state": state,
            "message": message,
            "metadata": copy.deepcopy(metadata),
        }
        self.events.append(event)
        return copy.deepcopy(event)

    def heartbeat(self, edit_job_id: str, worker_id: str, _lease: int) -> None:
        self._owned(worker_id)
        if self.jobs[edit_job_id]["status"] != "running":
            raise AssertionError("heartbeat attempted after terminal mutation")
        self.heartbeat_count += 1

    def getter(self, _project_id: str):
        class Getter:
            parts = {
                PART_ID: {
                    "part_name": "Bracket",
                    "cad_parts": [{"semantic_id": "bracket_body"}],
                }
            }

            @staticmethod
            def freshness():
                return {"status": "fresh"}

            @staticmethod
            def search_parts(_request: str, _limit: int):
                return [
                    {
                        "part_id": PART_ID,
                        "part_name": "Bracket",
                        "semantic_id": "bracket_body",
                        "score": 0.95,
                    }
                ]

            @staticmethod
            def dependency_graph(_part_id: str):
                return {
                    "nodes": [
                        {
                            "semantic_id": "bracket_body",
                            "transitive_dependencies": [],
                            "transitive_dependents": [],
                        }
                    ]
                }

        return Getter()

    def queue_index(
        self,
        edit_job_id: str,
        state: str,
        *,
        worker_id: str,
    ) -> str:
        self._owned(worker_id)
        self.index_count += 1
        index_id = f"index-{self.index_count}"
        should_fail = self.index_should_fail and state == "reindexing"
        self.index_jobs[index_id] = {
            "id": index_id,
            "status": "failed" if should_fail else "completed",
            "error_message": "Reindex failed." if should_fail else None,
        }
        self.jobs[edit_job_id].update(state=state, index_job_id=index_id)
        return index_id

    def index_job(self, index_job_id: str) -> dict:
        return copy.deepcopy(self.index_jobs[index_job_id])

    def generation_job(self, generation_job_id: str) -> dict:
        return copy.deepcopy(self.generation_jobs[generation_job_id])

    def queue_validation_run(
        self,
        edit_job_id: str,
        candidate_path: str,
        candidate_sha256: str,
        validation_run: int,
        *,
        worker_id: str,
    ) -> str:
        self._owned(worker_id)
        self.validation_calls.append(
            {
                "edit_job_id": edit_job_id,
                "candidate_path": candidate_path,
                "candidate_sha256": candidate_sha256,
                "validation_run": validation_run,
            }
        )
        # Mirrors the RPC's idempotent-reuse branch: the same (path, hash)
        # returns the child that already exists rather than a duplicate,
        # regardless of its status.
        existing_id = self.jobs[edit_job_id].get("validation_job_id")
        existing = self.generation_jobs.get(str(existing_id)) if existing_id else None
        if (
            existing is not None
            and existing["source_storage_path"] == candidate_path
            and existing["source_sha256"] == candidate_sha256
        ):
            return str(existing_id)

        self.generation_count += 1
        generation_id = f"validation-{self.generation_count}"
        outcome = (
            self.validation_outcomes.pop(0)
            if self.validation_outcomes
            else {"status": self.validation_status, "result": self.validation_result}
        )
        report = copy.deepcopy(outcome.get("result", self.validation_result))
        self.generation_jobs[generation_id] = {
            "id": generation_id,
            "edit_job_id": edit_job_id,
            "type": "validate_cad",
            "source_kind": "candidate",
            "source_storage_path": outcome.get("source_storage_path", candidate_path),
            "source_sha256": outcome.get("source_sha256", candidate_sha256),
            "status": outcome.get("status", self.validation_status),
            "result": report,
            "error_message": outcome.get("error_message"),
        }
        self.jobs[edit_job_id].update(
            state="validating_candidate",
            validation_run_count=validation_run,
            current_candidate_path=candidate_path,
            current_candidate_sha256=candidate_sha256,
            validation_job_id=generation_id,
        )
        if self.mutate_canonical_after_validation is not None:
            self.mutate_canonical_after_validation()
        return generation_id

    def queue_export(
        self,
        edit_job_id: str,
        source_sha256: str,
        *,
        worker_id: str,
    ) -> str:
        self._owned(worker_id)
        self.export_calls.append(
            {"edit_job_id": edit_job_id, "source_sha256": source_sha256}
        )
        if self.export_should_fail:
            raise RuntimeError("export queue unavailable")
        self.export_count += 1
        export_id = f"export-{self.export_count}"
        self.jobs[edit_job_id].update(state="queueing_export", export_job_id=export_id)
        return export_id

    def complete_edit_job(
        self,
        edit_job_id: str,
        result: dict,
        *,
        worker_id: str,
        metrics: dict | None = None,
    ) -> dict:
        self._owned(worker_id)
        self.jobs[edit_job_id].update(
            status="completed",
            state="completed",
            result=copy.deepcopy(result),
            metrics=copy.deepcopy(metrics or {}),
        )
        return self.edit_job(edit_job_id)

    def fail_edit_job(
        self,
        edit_job_id: str,
        *,
        code: str,
        message: str,
        result: dict,
        worker_id: str,
        metrics: dict | None = None,
    ) -> dict:
        self._owned(worker_id)
        self.jobs[edit_job_id].update(
            status="failed",
            state="failed",
            error_code=code,
            error_message=message,
            result=copy.deepcopy(result),
            metrics=copy.deepcopy(metrics or {}),
        )
        return self.edit_job(edit_job_id)


class FakeGoalCreator:
    def __init__(self, trace: list[str]):
        self.trace = trace
        self.calls = 0

    def create_goal(self, _request: str) -> CadGoal:
        self.calls += 1
        self.trace.append("goal")
        return goal()


class FakePlanningAgent:
    def __init__(self, trace: list[str]):
        self.trace = trace
        self.calls = 0
        self.requests: list[dict] = []

    def create_plan(self, **kwargs) -> CadPlanModelOutput:
        self.calls += 1
        self.requests.append(kwargs)
        self.trace.append("high-level-plan")
        return plan_output()


class FakePlanningLogWriter:
    def __init__(self):
        self.calls: list[dict] = []

    def write(self, *, job: dict, goal: CadGoal, plan: CadPlan) -> str:
        self.calls.append({"job": job, "goal": goal, "plan": plan})
        return f"/debug-logs/{job['id']}.txt"


class FakeTraceWriter:
    """Records every structured trace event instead of writing to disk."""

    def __init__(self):
        self.calls: list[dict] = []

    def log(self, event: str, *, edit_job_id: str, **fields):
        self.calls.append({"event": event, "edit_job_id": edit_job_id, **fields})
        return f"/debug-logs/{edit_job_id}.trace.jsonl"


def tool_call(tool_id: str, call_id: str = "call-1", **arguments):
    """Build one model-shaped function_call output item."""

    return SimpleNamespace(
        type="function_call",
        name=tool_id,
        call_id=call_id,
        arguments=json.dumps(arguments),
    )


def completion_turn(summary: str = "Step done.", call_id: str = "call-1"):
    """Build a decision that completes the active step in a single turn."""

    return [tool_call("request_step_completion", call_id, summary=summary)]


def create_feature_turn(call_id: str = "call-1", name: str = "base_plate"):
    """Build a decision that adds one valid CAD feature to the candidate.

    ``name`` distinguishes successive features so a test can mutate the
    candidate more than once -- creating the same semantic ID twice fails.
    """

    return [
        tool_call(
            "create_feature",
            call_id,
            semantic_id=name,
            function_name=f"build_{name}",
            role="primary_body",
            parameters=[],
            dependencies=[],
            search_keys=[name, "plate"],
            docstring=f"Build the {name}.",
            function_body='return cq.Workplane("XY").box(100, 100, 5)',
        )
    ]


class FakeAgent3D:
    """Agent stand-in returning scripted decisions, one per reasoning round.

    Without a script it completes whichever step is active on that step's
    first round, so the plan always runs to completion.
    """

    def __init__(self, trace: list[str] | None = None, script: list[list] | None = None):
        self.trace = trace
        self.invoked = False
        self.calls = 0
        self.requests: list[dict] = []
        self.start_step_requests: list[dict] = []
        self.continue_step_requests: list[dict] = []
        self.script = list(script) if script is not None else None
        self.tool_catalog = (
            {"type": "function", "name": "request_step_completion"},
            {"type": "function", "name": "index_search"},
            {"type": "function", "name": "index_get_feature"},
            {"type": "function", "name": "create_feature"},
            {"type": "function", "name": "create_parameter"},
            {"type": "function", "name": "edit_cad_build_model"},
        )

    def _respond(self, kind: str, kwargs: dict) -> SimpleNamespace:
        self.invoked = True
        self.calls += 1
        self.requests.append({"kind": kind, **kwargs})
        (self.start_step_requests if kind == "start_step" else self.continue_step_requests).append(
            kwargs
        )
        if self.trace is not None:
            self.trace.append("agent-decision")
        if self.script is None:
            output = completion_turn()
        elif self.script:
            output = self.script.pop(0)
        else:
            output = []
        return SimpleNamespace(id=f"decision-{self.calls}", output=list(output))

    def start_step(self, **kwargs):
        return self._respond("start_step", kwargs)

    def continue_step(self, **kwargs):
        return self._respond("continue_step", kwargs)


class RecordingToolExecutor:
    """Real tool dispatch through a registry, with per-call recording."""

    def __init__(self):
        registry = ToolRegistry()
        registry.register(RequestStepCompletionTool())
        registry.register(CreateFeatureTool())
        registry.register(EditCadBuildModelTool())
        registry.register(IndexGetFeatureTool())
        registry.register(CreateParameterTool())
        self._executor = ToolExecutor(registry)
        self.invoked = False
        self.calls: list[dict] = []

    def execute_sync(self, **kwargs):
        self.invoked = True
        self.calls.append(kwargs)
        return self._executor.execute_sync(**kwargs)

    def batch_group(self, tool_id: str) -> str | None:
        return self._executor.batch_group(tool_id)


def runtime(repository: FakeRepository, *, agent_script: list[list] | None = None):
    trace: list[str] = []
    creator = FakeGoalCreator(trace)
    planner = FakePlanningAgent(trace)
    planning_log_writer = FakePlanningLogWriter()
    agent_3d = FakeAgent3D(trace, script=agent_script)
    tool_executor = RecordingToolExecutor()
    orchestrator = EditWorkflowOrchestrator(
        repository,
        creator,
        planner,
        agent_3d,
        tool_executor,
        worker_id="test-worker",
        planning_log_writer=planning_log_writer,
        trace_writer=FakeTraceWriter(),
        sleep=lambda _seconds: None,
    )
    return orchestrator, creator, planner, trace, agent_3d, tool_executor


class RecentMessagesAndActiveStepTests(unittest.TestCase):
    def test_bounds_to_the_last_eight_messages_in_order(self):
        messages = [{"role": "user", "content": str(index)} for index in range(10)]

        bounded = _recent_messages(messages)

        self.assertEqual([m["content"] for m in bounded], [str(i) for i in range(2, 10)])

    def test_fewer_than_four_available_messages_are_passed_through_unpadded(self):
        messages = [{"role": "user", "content": "hello"}]

        self.assertEqual(_recent_messages(messages), messages)

    def test_non_list_or_non_dict_entries_are_dropped(self):
        self.assertEqual(_recent_messages(None), [])
        self.assertEqual(_recent_messages("not a list"), [])
        self.assertEqual(_recent_messages([{"role": "user"}, "junk"]), [{"role": "user"}])

    def test_active_step_picks_the_first_pending_step_in_sequence_order(self):
        selected = _active_step(plan_output_as_cad_plan())

        self.assertEqual(selected.step_id, "PS-1")

    def test_active_step_includes_an_in_progress_step(self):
        plan = _with_step_status(plan_output_as_cad_plan(), "PS-1", "in_progress")

        selected = _active_step(plan)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.step_id, "PS-1")

    def test_active_step_is_none_when_no_step_remains_active(self):
        for terminal_status in ("completed", "blocked", "superseded"):
            with self.subTest(status=terminal_status):
                plan = _with_step_status(
                    plan_output_as_cad_plan(), "PS-1", terminal_status
                )

                self.assertIsNone(_active_step(plan))


def plan_output_as_cad_plan() -> CadPlan:
    payload = plan_output().model_dump(mode="json")
    payload.update(plan_id=str(uuid4()), goal_id=GOAL_ID, version=1)
    return CadPlan.model_validate(payload)


class CountCadFeaturesTests(unittest.TestCase):
    def test_zero_features_on_an_empty_skeleton(self):
        self.assertEqual(_count_cad_features(EMPTY_SKELETON_SOURCE), 0)

    def test_counts_one_decorated_feature(self):
        self.assertEqual(_count_cad_features(ACCEPTED_SOURCE), 1)

    def test_fails_open_to_zero_on_a_syntax_error(self):
        self.assertEqual(_count_cad_features("def build_model(:\n"), 0)

    def test_stays_in_lockstep_with_extract_cad_features(self):
        for source in (EMPTY_SKELETON_SOURCE, ACCEPTED_SOURCE, "def build_model(:\n"):
            with self.subTest(source=source):
                self.assertEqual(
                    _count_cad_features(source), len(_extract_cad_features(source))
                )


class ExtractCadFeaturesTests(unittest.TestCase):
    def test_empty_skeleton_extracts_nothing(self):
        self.assertEqual(_extract_cad_features(EMPTY_SKELETON_SOURCE), [])

    def test_extracts_semantic_id_function_name_and_role(self):
        self.assertEqual(
            _extract_cad_features(ACCEPTED_SOURCE),
            [
                {
                    "semantic_id": "bracket_body",
                    "function_name": "build_bracket_body",
                    "role": "primary_body",
                }
            ],
        )

    def test_extracts_multiple_features_in_order(self):
        source = (
            "from cadquery_runtime import cad_part, cq, dataclass\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class ModelParams:\n"
            "    pass\n"
            "\n"
            "\n"
            '@cad_part(semantic_id="a", role="first", library="cadquery", '
            "parameters=(), depends_on=(), search_keys=(\"a\",))\n"
            "def build_a(params: ModelParams):\n"
            '    return cq.Workplane("XY")\n'
            "\n"
            "\n"
            '@cad_part(semantic_id="b", role="second", library="cadquery", '
            "parameters=(), depends_on=(), search_keys=(\"b\",))\n"
            "def build_b(params: ModelParams):\n"
            '    return cq.Workplane("XY")\n'
            "\n"
            "\n"
            "def build_model(params: ModelParams):\n"
            "    return build_a(params)\n"
        )

        self.assertEqual(
            _extract_cad_features(source),
            [
                {"semantic_id": "a", "function_name": "build_a", "role": "first"},
                {"semantic_id": "b", "function_name": "build_b", "role": "second"},
            ],
        )

    def test_fails_open_to_an_empty_list_on_a_syntax_error(self):
        self.assertEqual(_extract_cad_features("def build_model(:\n"), [])

    def test_a_non_literal_decorator_keyword_defaults_to_an_empty_string(self):
        source = (
            "from cadquery_runtime import cad_part, cq, dataclass\n"
            "\n"
            "ROLE = \"computed_role\"\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class ModelParams:\n"
            "    pass\n"
            "\n"
            "\n"
            '@cad_part(semantic_id="a", role=ROLE, library="cadquery", '
            "parameters=(), depends_on=(), search_keys=(\"a\",))\n"
            "def build_a(params: ModelParams):\n"
            '    return cq.Workplane("XY")\n'
            "\n"
            "\n"
            "def build_model(params: ModelParams):\n"
            "    return build_a(params)\n"
        )

        self.assertEqual(
            _extract_cad_features(source),
            [{"semantic_id": "a", "function_name": "build_a", "role": ""}],
        )


class ExtractModelParamsTests(unittest.TestCase):
    def test_empty_skeleton_has_no_parameters(self):
        self.assertEqual(_extract_model_params(EMPTY_SKELETON_SOURCE), [])

    def test_extracts_name_type_and_default(self):
        self.assertEqual(
            _extract_model_params(ACCEPTED_SOURCE),
            [{"name": "bracket_length_mm", "type_name": "float", "default": "100.0"}],
        )

    def test_extracts_multiple_parameters_in_order(self):
        source = (
            "from cadquery_runtime import cad_part, cq, dataclass\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class ModelParams:\n"
            "    plate_size_mm: float = 80\n"
            "    plate_thickness_mm: float = 6\n"
            "\n"
            "\n"
            "def build_model(params: ModelParams):\n"
            '    return cq.Workplane("XY")\n'
        )

        self.assertEqual(
            _extract_model_params(source),
            [
                {"name": "plate_size_mm", "type_name": "float", "default": "80"},
                {"name": "plate_thickness_mm", "type_name": "float", "default": "6"},
            ],
        )

    def test_extracts_a_negative_default(self):
        source = (
            "from cadquery_runtime import cad_part, cq, dataclass\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class ModelParams:\n"
            "    offset_mm: float = -5.0\n"
            "\n"
            "\n"
            "def build_model(params: ModelParams):\n"
            '    return cq.Workplane("XY")\n'
        )

        self.assertEqual(
            _extract_model_params(source),
            [{"name": "offset_mm", "type_name": "float", "default": "-5.0"}],
        )

    def test_fails_open_to_an_empty_list_on_a_syntax_error(self):
        self.assertEqual(_extract_model_params("def build_model(:\n"), [])

    def test_a_source_without_a_model_params_class_has_no_parameters(self):
        source = (
            "from cadquery_runtime import cq\n"
            "\n"
            "def build_model(params):\n"
            '    return cq.Workplane("XY")\n'
        )

        self.assertEqual(_extract_model_params(source), [])


class StepNeedsNewGeometryTests(unittest.TestCase):
    def test_true_when_the_step_addresses_a_required_criterion(self):
        step = plan_output_as_cad_plan().steps[0]

        self.assertTrue(_step_needs_new_geometry(step, goal()))

    def test_false_when_the_step_only_addresses_preserve_criteria(self):
        preserve_goal = CadGoal.model_validate(
            {
                **goal().model_dump(mode="json"),
                "completion_criteria": [
                    {
                        "criterion_id": "GC-1",
                        "description": "The mounting holes are preserved.",
                        "type": "preserve",
                    }
                ],
            }
        )
        step = plan_output_as_cad_plan().steps[0]

        self.assertFalse(_step_needs_new_geometry(step, preserve_goal))


class RedundantCallRejectionTests(unittest.TestCase):
    def test_index_search_repeated_with_identical_arguments_is_rejected(self):
        observations = [
            {
                "tool_id": "index_search",
                "arguments": {"query": "bracket", "limit": 5},
                "result": {"ok": True, "data": {}},
            }
        ]

        rejection = EditWorkflowOrchestrator._redundant_call_rejection(
            "index_search", {"query": "bracket", "limit": 5}, observations
        )

        self.assertIsNotNone(rejection)
        self.assertFalse(rejection.ok)
        self.assertEqual(rejection.error.code, "TOOL_CALL_REDUNDANT")

    def test_index_search_with_different_arguments_is_not_rejected(self):
        observations = [
            {
                "tool_id": "index_search",
                "arguments": {"query": "bracket", "limit": 5},
                "result": {"ok": True, "data": {}},
            }
        ]

        rejection = EditWorkflowOrchestrator._redundant_call_rejection(
            "index_search", {"query": "pole", "limit": 5}, observations
        )

        self.assertIsNone(rejection)

    def test_index_get_feature_repeated_with_identical_semantic_id_is_rejected(self):
        observations = [
            {
                "tool_id": "index_get_feature",
                "arguments": {"semantic_id": "hanger_rod"},
                "result": {"ok": True, "data": {}},
            }
        ]

        rejection = EditWorkflowOrchestrator._redundant_call_rejection(
            "index_get_feature", {"semantic_id": "hanger_rod"}, observations
        )

        self.assertIsNotNone(rejection)
        self.assertEqual(rejection.error.code, "TOOL_CALL_REDUNDANT")

    def test_index_get_feature_with_a_different_semantic_id_is_not_rejected(self):
        observations = [
            {
                "tool_id": "index_get_feature",
                "arguments": {"semantic_id": "hanger_rod"},
                "result": {"ok": True, "data": {}},
            }
        ]

        rejection = EditWorkflowOrchestrator._redundant_call_rejection(
            "index_get_feature", {"semantic_id": "wall_bracket_plate"}, observations
        )

        self.assertIsNone(rejection)

    def test_check_geometry_repeated_with_no_intervening_mutation_is_rejected(self):
        observations = [
            {"tool_id": "index_search", "arguments": {}, "result": {}},
            {"tool_id": "check_geometry", "arguments": {}, "result": {}},
        ]

        rejection = EditWorkflowOrchestrator._redundant_call_rejection(
            "check_geometry", {}, observations
        )

        self.assertIsNotNone(rejection)
        self.assertEqual(rejection.error.code, "TOOL_CALL_REDUNDANT")

    def test_check_geometry_after_a_mutation_is_not_rejected(self):
        observations = [
            {"tool_id": "check_geometry", "arguments": {}, "result": {}},
            {"tool_id": "edit_feature", "arguments": {}, "result": {}},
        ]

        rejection = EditWorkflowOrchestrator._redundant_call_rejection(
            "check_geometry", {}, observations
        )

        self.assertIsNone(rejection)

    def test_first_check_geometry_call_in_a_step_is_not_rejected(self):
        # No prior check_geometry observation exists yet this step, even
        # though nothing has mutated the candidate in this step either -- an
        # earlier step may have mutated the candidate without ever
        # confirming the result, so this call is left alone.
        observations = [
            {"tool_id": "index_search", "arguments": {}, "result": {}},
            {"tool_id": "index_get_feature", "arguments": {}, "result": {}},
        ]

        rejection = EditWorkflowOrchestrator._redundant_call_rejection(
            "check_geometry", {}, observations
        )

        self.assertIsNone(rejection)

    def test_check_geometry_with_no_prior_observations_is_not_rejected(self):
        rejection = EditWorkflowOrchestrator._redundant_call_rejection(
            "check_geometry", {}, []
        )

        self.assertIsNone(rejection)

    def test_mutating_tools_are_never_deduped_even_with_identical_arguments(self):
        arguments = {"semantic_id": "hanger_rod", "value": 12}
        observations = [
            {"tool_id": "edit_parameter", "arguments": arguments, "result": {}},
        ]

        rejection = EditWorkflowOrchestrator._redundant_call_rejection(
            "edit_parameter", arguments, observations
        )

        self.assertIsNone(rejection)

    def test_request_step_completion_is_never_deduped(self):
        arguments = {"summary": "Done."}
        observations = [
            {
                "tool_id": "request_step_completion",
                "arguments": arguments,
                "result": {},
            },
        ]

        rejection = EditWorkflowOrchestrator._redundant_call_rejection(
            "request_step_completion", arguments, observations
        )

        self.assertIsNone(rejection)


class ExecuteCallRedundancyIntegrationTests(unittest.TestCase):
    def test_execute_call_short_circuits_before_dispatching_to_the_tool_executor(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, _agent, tool_executor = runtime(
            repository
        )
        tool_context = ToolExecutionContext(
            run_id="run-1",
            project_id=PROJECT_ID,
            part_id=PART_ID,
            candidate_id=EDIT_JOB_ID,
            services=ToolServices(repository=repository),
        )
        observations = [
            {
                "tool_id": "index_search",
                "arguments": {"query": "bracket", "limit": 5},
                "result": {"ok": True, "data": {}},
            }
        ]
        call = _parse_call(tool_call("index_search", "call-1", query="bracket", limit=5))

        tool_id, _arguments, result = orchestrator._dispatch_call(
            call, tool_context, observations
        )

        self.assertEqual(tool_id, "index_search")
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "TOOL_CALL_REDUNDANT")
        # RecordingToolExecutor never registers index_search, so had the
        # redundancy check not short-circuited, execute_sync would have run
        # and failed with TOOL_NOT_FOUND instead -- proving this rejection
        # came from the pre-dispatch check, not the tool itself.
        self.assertFalse(tool_executor.invoked)


class BatchValidationTests(unittest.TestCase):
    ALLOWED = {
        "index_search",
        "index_get_feature",
        "create_feature",
        "create_parameter",
        "request_step_completion",
    }

    @staticmethod
    def _batch(*items) -> ToolCallBatch:
        return ToolCallBatch(calls=tuple(_parse_call(item) for item in items))

    @staticmethod
    def _rejection(outputs: list[dict]) -> dict:
        return json.loads(outputs[0]["output"])["error"]

    def test_several_create_parameter_calls_share_one_round(self):
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(
            tool_call("create_parameter", "call-1", parameter_name="a_mm", value=1.0),
            tool_call("create_parameter", "call-2", parameter_name="b_mm", value=2.0),
            tool_call("create_parameter", "call-3", parameter_name="c_mm", value=3.0),
        )

        self.assertIsNone(orchestrator._validate_batch(batch, self.ALLOWED))

    def test_a_read_mixed_with_a_parameter_create_is_rejected_as_mixed_groups(self):
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(
            tool_call("index_get_feature", "call-1", semantic_id="a"),
            tool_call("create_parameter", "call-2", parameter_name="a_mm", value=1.0),
        )

        outputs = orchestrator._validate_batch(batch, self.ALLOWED)

        self.assertEqual(len(outputs), 2)
        error = self._rejection(outputs)
        self.assertEqual(error["code"], "TOOL_BATCH_REJECTED")
        self.assertEqual(error["details"]["reason"], "mixed_batch_groups")
        self.assertEqual(
            error["details"]["batch_groups"], ["parameter_create", "read"]
        )

    def test_a_parameter_create_with_a_solo_tool_reports_not_batchable(self):
        # Pins the sub-rule ordering: the None check must run before the
        # homogeneity check, or sorting a set holding None and str raises.
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(
            tool_call("create_parameter", "call-1", parameter_name="a_mm", value=1.0),
            tool_call("create_feature", "call-2"),
        )

        outputs = orchestrator._validate_batch(batch, self.ALLOWED)

        error = self._rejection(outputs)
        self.assertEqual(error["details"]["reason"], "not_batchable")
        self.assertEqual(error["details"]["tool_ids"], ["create_feature"])

    def test_request_step_completion_can_never_share_a_round(self):
        # The atomicity invariant: the completion gate reads the candidate
        # hash assuming nothing else in this round can have touched it.
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(
            tool_call("request_step_completion", "call-1", summary="done"),
            tool_call("create_parameter", "call-2", parameter_name="a_mm", value=1.0),
        )

        outputs = orchestrator._validate_batch(batch, self.ALLOWED)

        error = self._rejection(outputs)
        self.assertEqual(error["details"]["reason"], "not_batchable")
        self.assertEqual(error["details"]["tool_ids"], ["request_step_completion"])

    def test_two_batchable_tools_together_are_accepted(self):
        # Two calls to the same batchable tool (RecordingToolExecutor only
        # registers index_get_feature among the read-only tools) still
        # exercises "every call in a multi-call batch is batchable" --
        # batchability is a per-tool-ID property, not per-call-argument.
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(
            tool_call("index_get_feature", "call-1", semantic_id="a"),
            tool_call("index_get_feature", "call-2", semantic_id="b"),
        )

        self.assertIsNone(orchestrator._validate_batch(batch, self.ALLOWED))

    def test_a_solo_non_batchable_tool_is_accepted(self):
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(tool_call("create_feature", "call-1"))

        self.assertIsNone(orchestrator._validate_batch(batch, self.ALLOWED))

    def test_a_non_batchable_tool_mixed_with_a_batchable_one_is_rejected(self):
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(
            tool_call("index_get_feature", "call-1", semantic_id="a"),
            tool_call("create_feature", "call-2"),
        )

        rejection = orchestrator._validate_batch(batch, self.ALLOWED)

        self.assertIsNotNone(rejection)
        self.assertEqual(len(rejection), 2)
        for output in rejection:
            failure = json.loads(output["output"])
            self.assertFalse(failure["ok"])
            self.assertEqual(failure["error"]["code"], "TOOL_BATCH_REJECTED")
            self.assertEqual(failure["error"]["details"]["reason"], "not_batchable")
            self.assertEqual(failure["error"]["details"]["tool_ids"], ["create_feature"])
        self.assertEqual({o["call_id"] for o in rejection}, {"call-1", "call-2"})

    def test_an_unknown_tool_is_rejected_via_tool_not_found_even_alone(self):
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(tool_call("not_a_real_tool", "call-1"))

        rejection = orchestrator._validate_batch(batch, self.ALLOWED)

        self.assertIsNotNone(rejection)
        self.assertEqual(len(rejection), 1)
        failure = json.loads(rejection[0]["output"])
        self.assertEqual(failure["error"]["code"], "TOOL_NOT_FOUND")

    def test_an_unknown_tool_rejects_the_whole_multi_call_batch(self):
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(
            tool_call("index_search", "call-1", query="a", limit=3),
            tool_call("not_a_real_tool", "call-2"),
        )

        rejection = orchestrator._validate_batch(batch, self.ALLOWED)

        self.assertIsNotNone(rejection)
        self.assertEqual(len(rejection), 2)
        for output in rejection:
            self.assertEqual(json.loads(output["output"])["error"]["code"], "TOOL_NOT_FOUND")

    def test_a_duplicate_call_id_is_rejected(self):
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(
            tool_call("index_search", "call-1", query="a", limit=3),
            tool_call("index_get_feature", "call-1", semantic_id="a"),
        )

        rejection = orchestrator._validate_batch(batch, self.ALLOWED)

        self.assertIsNotNone(rejection)
        failure = json.loads(rejection[0]["output"])
        self.assertEqual(failure["error"]["code"], "TOOL_BATCH_REJECTED")
        self.assertEqual(failure["error"]["details"]["reason"], "invalid_call_id")

    def test_an_empty_call_id_is_rejected(self):
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(tool_call("index_search", "", query="a", limit=3))

        rejection = orchestrator._validate_batch(batch, self.ALLOWED)

        self.assertIsNotNone(rejection)
        failure = json.loads(rejection[0]["output"])
        self.assertEqual(failure["error"]["details"]["reason"], "invalid_call_id")

    def test_a_batch_over_the_size_limit_is_rejected(self):
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(
            *[
                tool_call("index_search", f"call-{i}", query="a", limit=3)
                for i in range(MAX_TOOL_CALLS_PER_BATCH + 1)
            ]
        )

        rejection = orchestrator._validate_batch(batch, self.ALLOWED)

        self.assertIsNotNone(rejection)
        self.assertEqual(len(rejection), MAX_TOOL_CALLS_PER_BATCH + 1)
        failure = json.loads(rejection[0]["output"])
        self.assertEqual(failure["error"]["details"]["reason"], "batch_too_large")

    def test_a_batch_at_exactly_the_size_limit_is_accepted(self):
        orchestrator, *_rest = runtime(FakeRepository())
        batch = self._batch(
            *[
                tool_call("index_get_feature", f"call-{i}", semantic_id=f"id-{i}")
                for i in range(MAX_TOOL_CALLS_PER_BATCH)
            ]
        )

        self.assertIsNone(orchestrator._validate_batch(batch, self.ALLOWED))


class ProjectInventoryTests(unittest.TestCase):
    @staticmethod
    def _tool_context(repository: FakeRepository) -> ToolExecutionContext:
        return ToolExecutionContext(
            run_id="run-1",
            project_id=PROJECT_ID,
            part_id=PART_ID,
            candidate_id=EDIT_JOB_ID,
            services=ToolServices(repository=repository),
        )

    def test_other_parts_inventory_excludes_the_current_part(self):
        repository = FakeRepository()
        repository.files[f"{PROJECT_ID}/index/semantic_index.json"] = json.dumps(
            {
                "schema_version": 1,
                "project_id": PROJECT_ID,
                "parts": [
                    {
                        "part_id": PART_ID,
                        "part_name": "Bracket",
                        "cad_parts": [
                            {
                                "semantic_id": "bracket_body",
                                "function_name": "build_bracket_body",
                                "role": "primary_body",
                            }
                        ],
                        "model_params": [],
                    },
                    {
                        "part_id": "part-2",
                        "part_name": "Soap Holder",
                        "cad_parts": [
                            {
                                "semantic_id": "holder_floor",
                                "function_name": "build_holder_floor",
                                "role": "supporting floor",
                            }
                        ],
                        "model_params": [
                            {"name": "holder_diameter_mm", "type_name": "float"}
                        ],
                    },
                ],
            }
        )
        orchestrator, *_rest = runtime(repository)

        inventory = orchestrator._other_parts_inventory(self._tool_context(repository))

        self.assertEqual(
            inventory,
            [
                {
                    "part_id": "part-2",
                    "part_name": "Soap Holder",
                    "features": [
                        {
                            "semantic_id": "holder_floor",
                            "function_name": "build_holder_floor",
                            "role": "supporting floor",
                        }
                    ],
                    "parameters": [
                        {"name": "holder_diameter_mm", "type_name": "float"}
                    ],
                }
            ],
        )

    def test_other_parts_inventory_degrades_to_empty_when_the_index_is_missing(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        self.assertEqual(
            orchestrator._other_parts_inventory(self._tool_context(repository)), []
        )

    def test_other_parts_inventory_degrades_to_empty_on_invalid_json(self):
        repository = FakeRepository()
        repository.files[f"{PROJECT_ID}/index/semantic_index.json"] = "{not json"
        orchestrator, *_rest = runtime(repository)

        self.assertEqual(
            orchestrator._other_parts_inventory(self._tool_context(repository)), []
        )

    def test_current_part_inventory_reflects_the_live_candidate(self):
        repository = FakeRepository()
        repository.files[CANDIDATE_PATH] = ACCEPTED_SOURCE
        orchestrator, *_rest = runtime(repository)

        inventory = orchestrator._current_part_inventory(repository.jobs[EDIT_JOB_ID])

        self.assertEqual(
            inventory,
            {
                "part_id": PART_ID,
                "part_name": "Bracket",
                "features": [
                    {
                        "semantic_id": "bracket_body",
                        "function_name": "build_bracket_body",
                        "role": "primary_body",
                    }
                ],
                "parameters": [
                    {
                        "name": "bracket_length_mm",
                        "type_name": "float",
                        "default": "100.0",
                    }
                ],
                "geometry": {},
            },
        )

    def test_current_part_inventory_carries_the_last_measured_geometry(self):
        repository = FakeRepository()
        repository.files[CANDIDATE_PATH] = ACCEPTED_SOURCE
        orchestrator, *_rest = runtime(repository)

        inventory = orchestrator._current_part_inventory(
            repository.jobs[EDIT_JOB_ID], {"solid_count": 1, "volume_mm3": 70200.0}
        )

        # Supplied so a step opens knowing what the model measures instead of
        # spending its first round on check_geometry to ask.
        self.assertEqual(
            inventory["geometry"], {"solid_count": 1, "volume_mm3": 70200.0}
        )

    def test_current_part_inventory_fails_open_when_the_candidate_is_not_bootstrapped_yet(
        self,
    ):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        inventory = orchestrator._current_part_inventory(repository.jobs[EDIT_JOB_ID])

        self.assertEqual(
            inventory,
            {
                "part_id": PART_ID,
                "part_name": "Bracket",
                "features": [],
                "parameters": [],
                "geometry": {},
            },
        )


class ProjectInventoryAcrossStepsTests(unittest.TestCase):
    def test_a_later_step_sees_what_an_earlier_step_in_the_same_job_just_created(self):
        repository = FakeRepository()
        repository.files[ACCEPTED_SOURCE_PATH] = EMPTY_SKELETON_SOURCE
        AgentLoopTests._seed_two_step_plan(repository)
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository,
            agent_script=[create_feature_turn(), completion_turn(), completion_turn()],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(agent_3d.start_step_requests), 2)
        # PS-1 starts with nothing built yet.
        self.assertEqual(
            agent_3d.start_step_requests[0]["project_inventory"]["current_part"]["features"],
            [],
        )
        # PS-2's chain starts fresh, but its roster reflects what PS-1 just
        # built in this same job -- read live from the candidate, not from
        # the (unreindexed) semantic index.
        self.assertEqual(
            agent_3d.start_step_requests[1]["project_inventory"]["current_part"]["features"],
            [
                {
                    "semantic_id": "base_plate",
                    "function_name": "build_base_plate",
                    "role": "primary_body",
                }
            ],
        )


class CadEditorOrchestrationTests(unittest.TestCase):
    def test_planning_resolves_server_owned_part_before_the_agent(self):
        repository = FakeRepository()
        repository.jobs[EDIT_JOB_ID].update(
            resolved_part_id=None,
            resolved_targets=[],
        )
        orchestrator, _creator, planner, _trace, _agent_3d, _tool_executor = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(repository.jobs[EDIT_JOB_ID]["resolved_part_id"], PART_ID)
        self.assertEqual(
            repository.jobs[EDIT_JOB_ID]["resolved_targets"][0]["part_id"],
            PART_ID,
        )
        self.assertEqual(planner.calls, 1)
        self.assertEqual(planner.requests[0]["tool_context"].part_id, PART_ID)
        self.assertTrue(_agent_3d.invoked)
        self.assertEqual(_agent_3d.calls, 1)
        self.assertTrue(_tool_executor.invoked)

    def test_run_completes_the_full_commit_pipeline(self):
        repository = FakeRepository()
        orchestrator, creator, planner, trace, _agent_3d, _tool_executor = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outcome"], "committed")
        self.assertEqual(result["goal"]["goal_id"], GOAL_ID)
        self.assertEqual(result["high_level_plan"]["goal_id"], GOAL_ID)
        self.assertEqual(result["validation_result"], passed_report())
        self.assertEqual(result["index_job_id"], "index-1")
        self.assertEqual(result["export_job_id"], "export-1")
        self.assertEqual(result["changed_files"], [ACCEPTED_SOURCE_PATH])
        self.assertEqual(len(result["source_sha256"]), 64)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(
            result["planning_log_path"],
            f"/debug-logs/{EDIT_JOB_ID}.txt",
        )
        self.assertEqual(len(orchestrator.planning_log_writer.calls), 1)
        self.assertEqual((creator.calls, planner.calls), (1, 1))
        self.assertEqual(trace, ["goal", "high-level-plan", "agent-decision"])
        self.assertEqual(repository.jobs[EDIT_JOB_ID]["status"], "completed")

    def test_canonical_source_is_untouched_while_the_loop_runs(self):
        repository = FakeRepository()
        writes_during_loop: list[str] = []
        original_write = repository.write_text

        def spy_write_text(path: str, content: str) -> None:
            if repository.jobs[EDIT_JOB_ID]["state"] == "applying_edit":
                writes_during_loop.append(path)
            original_write(path, content)

        repository.write_text = spy_write_text  # type: ignore[method-assign]
        orchestrator, *_rest = runtime(
            repository, agent_script=[create_feature_turn(), completion_turn()]
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        # The loop really did write somewhere (the candidate) -- proving the
        # absence of ACCEPTED_SOURCE_PATH below is a meaningful assertion.
        self.assertIn(CANDIDATE_PATH, writes_during_loop)
        self.assertNotIn(ACCEPTED_SOURCE_PATH, writes_during_loop)

    def test_canonical_source_is_updated_after_a_successful_commit(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(
            repository, agent_script=[create_feature_turn(), completion_turn()]
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        candidate_content = repository.files[CANDIDATE_PATH]
        self.assertIn("build_base_plate", candidate_content)
        self.assertEqual(repository.files[ACCEPTED_SOURCE_PATH], candidate_content)
        self.assertEqual(result["changed_files"], [ACCEPTED_SOURCE_PATH])

    def test_unexpected_failure_keeps_traceback_in_private_logs(self):
        repository = FakeRepository()

        def infrastructure_bug(
            _edit_job_id: str,
            _worker_id: str,
            _lease: int,
        ) -> None:
            raise RuntimeError("private infrastructure diagnostic")

        repository.heartbeat = infrastructure_bug  # type: ignore[method-assign]
        orchestrator, *_rest = runtime(repository)

        with self.assertLogs(
            "workers.agent_3d.orchestrator",
            level="ERROR",
        ) as captured:
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["error_code"], "WORKFLOW_INTERNAL_ERROR")
        self.assertTrue(
            any("RuntimeError: private infrastructure diagnostic" in line for line in captured.output)
        )

    def test_required_clarification_stops_before_planning(self):
        repository = FakeRepository()
        orchestrator, creator, planner, _trace, _agent_3d, _tool_executor = runtime(repository)
        creator.create_goal = lambda _request: clarification_goal()  # type: ignore[method-assign]

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "CLARIFICATION_REQUIRED")
        self.assertEqual(
            result["details"]["question"],
            "Which bracket face should become taller?",
        )
        self.assertTrue(result["goal"]["clarification"]["required"])
        self.assertEqual(result["high_level_plan"], {})
        self.assertEqual(planner.calls, 0)

    def test_lease_loss_is_not_converted_into_an_unguarded_terminal_write(self):
        repository = FakeRepository()

        def lose_lease(_edit_job_id: str, _worker_id: str, _lease: int) -> None:
            raise WorkflowFailure(
                "EDIT_LEASE_LOST",
                "The editor worker no longer owns this edit job.",
            )

        repository.heartbeat = lose_lease  # type: ignore[method-assign]
        orchestrator, *_rest = runtime(repository)

        with self.assertRaisesRegex(WorkflowFailure, "no longer owns"):
            orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(repository.jobs[EDIT_JOB_ID]["status"], "running")
        self.assertFalse(
            any(event["event_type"] == "job.failed" for event in repository.events)
        )

    def test_goal_precedes_the_high_level_plan(self):
        repository = FakeRepository()
        orchestrator, creator, planner, trace, _agent_3d, _tool_executor = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["goal"]["goal_id"], GOAL_ID)
        self.assertEqual(result["high_level_plan"]["goal_id"], GOAL_ID)
        self.assertEqual((creator.calls, planner.calls), (1, 1))
        self.assertLess(trace.index("goal"), trace.index("high-level-plan"))

    def test_checkpointed_goal_and_plan_are_not_recreated_on_resume(self):
        repository = FakeRepository()
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "job_started"},
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
        ]
        orchestrator, creator, planner, _trace, _agent_3d, _tool_executor = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(creator.calls, 0)
        self.assertEqual(planner.calls, 1)

    def test_agent_3d_receives_the_goal_plan_active_step_and_empty_observations(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, _tool_executor = runtime(
            repository
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(agent_3d.calls, 1)
        request = agent_3d.requests[0]
        self.assertEqual(request["goal"].goal_id, goal().goal_id)
        self.assertEqual(request["plan"].goal_id, goal().goal_id)
        self.assertEqual(request["active_step"].step_id, "PS-1")
        self.assertEqual(request["observations"], [])

    def _seed_checkpoints(
        self,
        repository: FakeRepository,
        *,
        statuses: list[str],
        event: str = "plan_created",
    ) -> None:
        """Persist a goal plus a plan whose steps carry the supplied statuses."""

        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "job_started"},
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
            {
                "event": event,
                "plan": {
                    "plan_id": "55555555-5555-4555-8555-555555555555",
                    "goal_id": GOAL_ID,
                    "version": 1,
                    "summary": "Update the linked bracket.",
                    "target_bindings": [],
                    "steps": [
                        {
                            "step_id": f"PS-{index}",
                            "sequence": index,
                            "objective": f"Bracket work {index}.",
                            "depends_on": [],
                            "addresses_criteria": ["GC-1"],
                            "status": status,
                        }
                        for index, status in enumerate(statuses, start=1)
                    ],
                },
            },
        ]

    def test_plan_with_no_active_step_completes_without_calling_the_agent(self):
        repository = FakeRepository()
        self._seed_checkpoints(repository, statuses=["completed"])
        orchestrator, creator, planner, _trace, agent_3d, tool_executor = runtime(
            repository
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outcome"], "committed")
        self.assertEqual((creator.calls, planner.calls), (0, 0))
        self.assertEqual(agent_3d.calls, 0)
        self.assertFalse(tool_executor.invoked)


class AgentLoopTests(unittest.TestCase):
    @staticmethod
    def _final_plan(repository: FakeRepository) -> dict:
        history = repository.jobs[EDIT_JOB_ID]["history"]
        updates = [event for event in history if event.get("event") == "plan_updated"]
        return updates[-1]["plan"]

    def test_loop_completes_every_step_and_records_each_transition(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        # The scripted plan has one step, completed on its first turn.
        self.assertEqual(agent_3d.calls, 1)
        self.assertEqual(
            [step["status"] for step in result["high_level_plan"]["steps"]],
            ["completed"],
        )
        transitions = [
            step["status"]
            for event in repository.jobs[EDIT_JOB_ID]["history"]
            if event.get("event") == "plan_updated"
            for step in event["plan"]["steps"]
        ]
        self.assertEqual(transitions, ["in_progress", "completed"])

    def test_step_lifecycle_is_reported_through_progress_events(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        loop_events = [
            event
            for event in repository.events
            if event["event_type"] in {"tools.started", "tools.completed"}
        ]
        self.assertEqual(
            [event["event_type"] for event in loop_events],
            ["tools.started", "tools.completed"],
        )
        self.assertEqual(loop_events[0]["metadata"]["step_id"], "PS-1")
        self.assertEqual(loop_events[1]["metadata"]["summary"], "Step done.")
        self.assertEqual(loop_events[0]["state"], "applying_edit")

    def test_observations_accumulate_within_a_step(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository,
            agent_script=[
                [tool_call("index_search", "call-1", query="bracket", limit=3)],
                completion_turn("Bracket enlarged.", "call-2"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(agent_3d.calls, 2)
        self.assertEqual(len(agent_3d.start_step_requests), 1)
        self.assertEqual(agent_3d.start_step_requests[0]["observations"], [])
        self.assertEqual(len(agent_3d.continue_step_requests), 1)
        carried = agent_3d.continue_step_requests[0]["tool_outputs"]
        self.assertEqual(len(carried), 1)
        self.assertEqual(carried[0]["call_id"], "call-1")
        carried_result = json.loads(carried[0]["output"])
        # index_search is outside this agent's catalog stand-in registry.
        self.assertFalse(carried_result["ok"])

    def test_observations_reset_when_the_active_step_changes(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository,
            agent_script=[
                [tool_call("index_search", "call-1", query="bracket", limit=3)],
                completion_turn("First step done.", "call-2"),
                completion_turn("Second step done.", "call-3"),
            ],
        )
        self._seed_two_step_plan(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(agent_3d.calls, 3)
        self.assertEqual(len(agent_3d.start_step_requests), 2)
        self.assertEqual(len(agent_3d.continue_step_requests), 1)
        self.assertEqual(agent_3d.start_step_requests[0]["active_step"].step_id, "PS-1")
        self.assertEqual(len(agent_3d.continue_step_requests[0]["tool_outputs"]), 1)
        # PS-2 becomes active and must not inherit PS-1's tool transcript or chain.
        self.assertEqual(agent_3d.start_step_requests[1]["active_step"].step_id, "PS-2")
        self.assertEqual(agent_3d.start_step_requests[1]["observations"], [])

    @staticmethod
    def _seed_two_step_plan(repository: FakeRepository) -> None:
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "job_started"},
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
            {
                "event": "plan_created",
                "plan": {
                    "plan_id": "55555555-5555-4555-8555-555555555555",
                    "goal_id": GOAL_ID,
                    "version": 1,
                    "summary": "Update the linked bracket.",
                    "target_bindings": [],
                    "steps": [
                        {
                            "step_id": "PS-1",
                            "sequence": 1,
                            "objective": "Measure the bracket.",
                            "depends_on": [],
                            "addresses_criteria": ["GC-1"],
                            "status": "pending",
                        },
                        {
                            "step_id": "PS-2",
                            "sequence": 2,
                            "objective": "Raise the bracket.",
                            "depends_on": ["PS-1"],
                            "addresses_criteria": ["GC-1"],
                            "status": "pending",
                        },
                    ],
                },
            },
        ]

    def test_a_decision_with_no_tool_call_fails_fast(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository, agent_script=[[]])

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "AGENT_NO_ACTION")
        self.assertEqual(result["details"]["step_id"], "PS-1")

    def test_a_step_that_never_completes_hits_the_per_step_turn_limit(self):
        repository = FakeRepository()
        stalling = [
            [tool_call("index_search", f"call-{turn}", query="bracket", limit=1)]
            for turn in range(MAX_STEP_TURNS + 2)
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository, agent_script=stalling
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "AGENT_STEP_TURN_LIMIT")
        self.assertEqual(result["details"]["step_id"], "PS-1")
        self.assertEqual(agent_3d.calls, MAX_STEP_TURNS)

    def test_the_global_turn_limit_bounds_a_long_plan(self):
        repository = FakeRepository()
        # One step per turn would need more turns than the global cap allows.
        statuses = ["pending"] * (MAX_AGENT_TURNS + 2)
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "job_started"},
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
            {
                "event": "plan_created",
                "plan": {
                    "plan_id": "55555555-5555-4555-8555-555555555555",
                    "goal_id": GOAL_ID,
                    "version": 1,
                    "summary": "Update the linked bracket.",
                    "target_bindings": [],
                    "steps": [
                        {
                            "step_id": f"PS-{index}",
                            "sequence": index,
                            "objective": f"Bracket work {index}.",
                            "depends_on": [],
                            "addresses_criteria": ["GC-1"],
                            "status": status,
                        }
                        for index, status in enumerate(statuses, start=1)
                    ],
                },
            },
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "AGENT_TURN_LIMIT")
        self.assertEqual(agent_3d.calls, MAX_AGENT_TURNS)

    def test_the_goal_is_never_mutated_by_the_loop(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["goal"], goal().model_dump(mode="json"))
        goal_events = [
            event
            for event in repository.jobs[EDIT_JOB_ID]["history"]
            if event.get("event") == "goal_created"
        ]
        self.assertEqual(len(goal_events), 1)
        self.assertEqual(goal_events[0]["goal"], goal().model_dump(mode="json"))


class AgentBatchExecutionTests(unittest.TestCase):
    """A validated multi-call batch dispatches strictly in the model-returned
    order, one call at a time -- never concurrently -- and every call's
    result, success or failure, is returned to the same reasoning chain.
    """

    @staticmethod
    def _parameter_turn(*names: str):
        """One round creating several ModelParams fields together."""

        return [
            tool_call(
                "create_parameter",
                f"p-{index}",
                parameter_name=name,
                value=float(index + 1),
            )
            for index, name in enumerate(names)
        ]

    def test_a_create_parameter_batch_applies_every_field(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, _agent, tool_executor = runtime(
            repository,
            agent_script=[
                self._parameter_turn("slot_mm", "rib_mm", "boss_mm"),
                completion_turn(call_id="done"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        source = repository.files[CANDIDATE_PATH]
        for name in ("slot_mm", "rib_mm", "boss_mm"):
            self.assertIn(f"{name}: float", source)
        # Three fields created in one round rather than three.
        self.assertEqual(
            [call["tool_id"] for call in tool_executor.calls[:3]],
            ["create_parameter"] * 3,
        )

    def test_one_failed_call_in_a_batch_does_not_stop_the_others(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository,
            agent_script=[
                # The middle call reuses the first call's name.
                self._parameter_turn("slot_mm", "slot_mm", "boss_mm"),
                completion_turn(call_id="done"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        outputs = agent_3d.continue_step_requests[0]["tool_outputs"]
        decoded = [json.loads(output["output"]) for output in outputs]
        self.assertEqual([item["ok"] for item in decoded], [True, False, True])
        self.assertEqual(decoded[1]["error"]["code"], "TOOL_VALIDATION_FAILED")
        self.assertEqual(decoded[1]["error"]["details"]["reason"], "duplicate")
        # The two that succeeded stay applied: a partial failure is not a
        # rollback, and the agent is expected to fix only the failed call.
        source = repository.files[CANDIDATE_PATH]
        self.assertIn("slot_mm: float", source)
        self.assertIn("boss_mm: float", source)

    def test_calls_dispatch_in_model_returned_order(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, tool_executor = runtime(
            repository,
            agent_script=[
                [
                    tool_call("index_get_feature", "call-1", semantic_id="a"),
                    tool_call("index_get_feature", "call-2", semantic_id="b"),
                ],
                completion_turn("Done.", "call-3"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        # The first two dispatches are the batch, strictly in the order the
        # model returned them; sequential execute_sync calls (no
        # asyncio.gather/threads) is what makes this order guaranteed.
        semantic_ids = [call["raw_arguments"]["semantic_id"] for call in tool_executor.calls[:2]]
        self.assertEqual(semantic_ids, ["a", "b"])

    def test_a_partial_failure_within_a_valid_batch_returns_all_results_and_continues(self):
        # Two calls to the same tool with identical arguments in one batch:
        # the first dispatches and succeeds; because observations accumulate
        # within the batch before the next call is checked, the second is
        # caught by the existing intra-step redundancy dedup -- an ordinary
        # per-call failure, not a batch-validation rejection.
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, tool_executor = runtime(
            repository,
            agent_script=[
                [
                    tool_call("index_get_feature", "call-1", semantic_id="a"),
                    tool_call("index_get_feature", "call-2", semantic_id="a"),
                ],
                completion_turn("Done.", "call-3"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        # The first call reaches the tool executor; the second is caught by
        # _redundant_call_rejection inside _dispatch_call before ever
        # reaching execute_sync -- only the first batch call plus the
        # completion call from round 2 actually dispatch.
        self.assertEqual(len(tool_executor.calls), 2)
        tool_outputs = agent_3d.continue_step_requests[0]["tool_outputs"]
        self.assertEqual([o["call_id"] for o in tool_outputs], ["call-1", "call-2"])
        first, second = (json.loads(o["output"]) for o in tool_outputs)
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"]["code"], "TOOL_CALL_REDUNDANT")


class AgentChainContinuationTests(unittest.TestCase):
    """One active plan step drives one continuous reasoning chain: a single
    start_step call opens it, and every further round of that same step
    continues it via continue_step -- never a fresh start_step -- until the
    step completes or a new step begins.
    """

    def test_a_step_completed_on_its_first_round_calls_start_step_only(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(agent_3d.start_step_requests), 1)
        self.assertEqual(len(agent_3d.continue_step_requests), 0)

    def test_a_multi_round_step_uses_one_start_step_then_continue_step_per_further_round(
        self,
    ):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository,
            agent_script=[
                [tool_call("index_search", "call-1", query="bracket", limit=3)],
                [tool_call("index_search", "call-2", query="drainage", limit=3)],
                create_feature_turn("call-3"),
                completion_turn("Done.", "call-4"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(agent_3d.calls, 4)
        self.assertEqual(len(agent_3d.start_step_requests), 1)
        self.assertEqual(len(agent_3d.continue_step_requests), 3)
        # Each round continues from the immediately preceding response --
        # one strictly linked chain, not independent decisions.
        self.assertEqual(agent_3d.continue_step_requests[0]["previous_response_id"], "decision-1")
        self.assertEqual(agent_3d.continue_step_requests[1]["previous_response_id"], "decision-2")
        self.assertEqual(agent_3d.continue_step_requests[2]["previous_response_id"], "decision-3")

    def test_continue_step_receives_the_prior_rounds_tool_output_as_a_function_call_output_item(
        self,
    ):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository,
            agent_script=[create_feature_turn("call-1"), completion_turn("Done.", "call-2")],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(len(agent_3d.continue_step_requests), 1)
        tool_outputs = agent_3d.continue_step_requests[0]["tool_outputs"]
        self.assertEqual(len(tool_outputs), 1)
        self.assertEqual(tool_outputs[0]["type"], "function_call_output")
        self.assertEqual(tool_outputs[0]["call_id"], "call-1")
        output = json.loads(tool_outputs[0]["output"])
        self.assertTrue(output["ok"])
        self.assertEqual(output["data"]["status"], "created")

    def test_a_failed_tool_call_keeps_the_same_chain_alive_for_the_next_round(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository,
            agent_script=[
                [tool_call("not_a_real_tool", "call-1")],
                completion_turn("Done.", "call-2"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(agent_3d.start_step_requests), 1)
        self.assertEqual(len(agent_3d.continue_step_requests), 1)
        continued = agent_3d.continue_step_requests[0]
        self.assertEqual(continued["previous_response_id"], "decision-1")
        failure = json.loads(continued["tool_outputs"][0]["output"])
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["error"]["code"], "TOOL_NOT_FOUND")

    def test_a_new_step_always_starts_a_new_chain(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository,
            agent_script=[
                completion_turn("First step done.", "call-1"),
                completion_turn("Second step done.", "call-2"),
            ],
        )
        AgentLoopTests._seed_two_step_plan(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(agent_3d.start_step_requests), 2)
        self.assertEqual(len(agent_3d.continue_step_requests), 0)
        self.assertEqual(agent_3d.start_step_requests[0]["active_step"].step_id, "PS-1")
        self.assertEqual(agent_3d.start_step_requests[1]["active_step"].step_id, "PS-2")

    def test_a_batched_round_returns_all_results_together_in_one_continuation(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, tool_executor = runtime(
            repository,
            agent_script=[
                [
                    tool_call("index_get_feature", "call-1", semantic_id="a"),
                    tool_call("index_get_feature", "call-2", semantic_id="b"),
                ],
                completion_turn("Done.", "call-3"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(agent_3d.calls, 2)
        self.assertEqual(len(agent_3d.start_step_requests), 1)
        self.assertEqual(len(agent_3d.continue_step_requests), 1)
        # 2 dispatched batch calls + the completion call from round 2.
        self.assertEqual(len(tool_executor.calls), 3)
        continued = agent_3d.continue_step_requests[0]
        self.assertEqual(continued["previous_response_id"], "decision-1")
        tool_outputs = continued["tool_outputs"]
        self.assertEqual([o["call_id"] for o in tool_outputs], ["call-1", "call-2"])
        for output in tool_outputs:
            self.assertEqual(output["type"], "function_call_output")

    def test_a_rejected_batch_dispatches_nothing_but_keeps_the_chain_alive(self):
        repository = FakeRepository()
        orchestrator, _creator, _planner, _trace, agent_3d, tool_executor = runtime(
            repository,
            agent_script=[
                [
                    tool_call("index_get_feature", "call-1", semantic_id="a"),
                    tool_call("create_feature", "call-2"),
                ],
                completion_turn("Done.", "call-3"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(agent_3d.start_step_requests), 1)
        self.assertEqual(len(agent_3d.continue_step_requests), 1)
        # Nothing from the rejected batch reached the tool executor -- only
        # the completion call from round 2 (which receives the rejection) did.
        self.assertEqual(len(tool_executor.calls), 1)
        self.assertEqual(tool_executor.calls[0]["tool_id"], "request_step_completion")
        rejected = agent_3d.continue_step_requests[0]["tool_outputs"]
        self.assertEqual([o["call_id"] for o in rejected], ["call-1", "call-2"])
        for output in rejected:
            failure = json.loads(output["output"])
            self.assertFalse(failure["ok"])
            self.assertEqual(failure["error"]["code"], "TOOL_BATCH_REJECTED")


class CandidateBootstrapTests(unittest.TestCase):
    def test_accepted_source_is_copied_to_an_edit_scoped_candidate(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(repository.files[CANDIDATE_PATH], ACCEPTED_SOURCE)
        # current_candidate_path/sha256 are set on the job row by the
        # queue_edit_candidate_validation_run RPC itself when a candidate is
        # validated, not by _prepare_candidate directly.
        self.assertEqual(
            repository.jobs[EDIT_JOB_ID]["current_candidate_path"],
            f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/validation-1/model.py",
        )
        self.assertEqual(len(result["source_sha256"]), 64)

    def test_an_existing_candidate_is_reused_rather_than_overwritten(self):
        repository = FakeRepository()
        resumed_source = ACCEPTED_SOURCE + "\n# work from a previous worker\n"
        repository.files[CANDIDATE_PATH] = resumed_source
        orchestrator, *_rest = runtime(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(repository.files[CANDIDATE_PATH], resumed_source)

    def test_missing_accepted_source_fails_clearly(self):
        repository = FakeRepository()
        repository.files.pop(ACCEPTED_SOURCE_PATH)
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "CANDIDATE_BOOTSTRAP_FAILED")


class PlanResumeTests(unittest.TestCase):
    def test_resume_continues_from_the_latest_plan_update(self):
        repository = FakeRepository()
        AgentLoopTests._seed_two_step_plan(repository)
        history = repository.jobs[EDIT_JOB_ID]["history"]
        resumed = copy.deepcopy(history[-1]["plan"])
        resumed["steps"][0]["status"] = "completed"
        history.append({"event": "plan_updated", "plan": resumed})
        orchestrator, _creator, planner, _trace, agent_3d, _executor = runtime(
            repository
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(planner.calls, 0)
        # PS-1 was already completed, so only PS-2 is worked -- and, since
        # the resumed process holds no prior chain state, it starts a new
        # reasoning chain (start_step) for PS-2 rather than continuing one.
        self.assertEqual(agent_3d.calls, 1)
        self.assertEqual(len(agent_3d.start_step_requests), 1)
        self.assertEqual(agent_3d.start_step_requests[0]["active_step"].step_id, "PS-2")
        self.assertEqual(
            [step["status"] for step in result["high_level_plan"]["steps"]],
            ["completed", "completed"],
        )


class EmptyPartCompletionGateTests(unittest.TestCase):
    @staticmethod
    def _seed_single_step_plan(
        repository: FakeRepository, *, criterion_type: str
    ) -> None:
        repository.files[ACCEPTED_SOURCE_PATH] = EMPTY_SKELETON_SOURCE
        goal_payload = goal().model_dump(mode="json")
        goal_payload["completion_criteria"][0]["type"] = criterion_type
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "job_started"},
            {"event": "goal_created", "goal": goal_payload},
            {
                "event": "plan_created",
                "plan": {
                    "plan_id": "55555555-5555-4555-8555-555555555555",
                    "goal_id": GOAL_ID,
                    "version": 1,
                    "summary": "Build the soap holder.",
                    "target_bindings": [],
                    "steps": [
                        {
                            "step_id": "PS-1",
                            "sequence": 1,
                            "objective": "Create the base plate.",
                            "depends_on": [],
                            "addresses_criteria": ["GC-1"],
                            "status": "pending",
                        },
                    ],
                },
            },
        ]

    def test_completion_is_rejected_when_a_required_step_has_no_features(self):
        repository = FakeRepository()
        self._seed_single_step_plan(repository, criterion_type="required")
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository, agent_script=[completion_turn()]
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        # The scripted turn was rejected; the next round had nothing left in
        # the script, so the loop fails on AGENT_NO_ACTION -- proof the step
        # was never marked completed off the rejected call. The rejection
        # flows to the next round of the SAME reasoning chain (continue_step),
        # not a fresh one.
        self.assertEqual(agent_3d.calls, 2)
        self.assertEqual(len(agent_3d.continue_step_requests), 1)
        continued = agent_3d.continue_step_requests[0]
        self.assertEqual(continued["previous_response_id"], "decision-1")
        rejected_output = json.loads(continued["tool_outputs"][0]["output"])
        self.assertEqual(continued["tool_outputs"][0]["call_id"], "call-1")
        self.assertFalse(rejected_output["ok"])
        self.assertEqual(rejected_output["error"]["code"], "STEP_REQUIRES_A_FEATURE")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "AGENT_NO_ACTION")
        self.assertEqual(
            result["high_level_plan"]["steps"][0]["status"], "in_progress"
        )

    def test_completion_succeeds_after_creating_a_feature(self):
        repository = FakeRepository()
        self._seed_single_step_plan(repository, criterion_type="required")
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository,
            agent_script=[create_feature_turn(), completion_turn()],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(agent_3d.calls, 2)
        self.assertEqual(
            result["high_level_plan"]["steps"][0]["status"], "completed"
        )
        candidate = repository.files[CANDIDATE_PATH]
        self.assertEqual(_count_cad_features(candidate), 1)
        self.assertIn("build_base_plate", candidate)

    def test_gate_does_not_block_a_step_that_only_preserves(self):
        repository = FakeRepository()
        self._seed_single_step_plan(repository, criterion_type="preserve")
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository, agent_script=[completion_turn()]
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(agent_3d.calls, 1)
        self.assertEqual(
            result["high_level_plan"]["steps"][0]["status"], "completed"
        )

    def test_gate_stops_firing_once_an_earlier_step_created_a_feature(self):
        repository = FakeRepository()
        repository.files[ACCEPTED_SOURCE_PATH] = EMPTY_SKELETON_SOURCE
        AgentLoopTests._seed_two_step_plan(repository)
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository,
            agent_script=[
                create_feature_turn(),
                completion_turn("First step done.", "call-2"),
                completion_turn("Second step done.", "call-3"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(agent_3d.calls, 3)
        self.assertEqual(
            [step["status"] for step in result["high_level_plan"]["steps"]],
            ["completed", "completed"],
        )

    def test_wiring_an_undefined_function_is_rejected_before_completion(self):
        repository = FakeRepository()
        self._seed_single_step_plan(repository, criterion_type="required")
        orchestrator, _creator, _planner, _trace, agent_3d, _executor = runtime(
            repository,
            agent_script=[
                [
                    tool_call(
                        "edit_cad_build_model",
                        "call-1",
                        function_body=(
                            "body = build_soap_holder_body(params)\nreturn body"
                        ),
                    )
                ],
                completion_turn(),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(len(agent_3d.continue_step_requests), 2)

        # Round 1 (start_step): the wiring attempt is rejected at the tool
        # itself, and round 2's continuation of the same chain receives it.
        wiring_rejected_output = agent_3d.continue_step_requests[0]["tool_outputs"][0]
        self.assertEqual(wiring_rejected_output["call_id"], "call-1")
        wiring_rejected = json.loads(wiring_rejected_output["output"])
        self.assertFalse(wiring_rejected["ok"])
        self.assertEqual(wiring_rejected["error"]["code"], "TOOL_VALIDATION_FAILED")
        self.assertEqual(
            wiring_rejected["error"]["details"]["reason"], "undefined_function_call"
        )
        # build_model was never actually rewritten to call the missing name.
        self.assertNotIn(
            "build_soap_holder_body", repository.files[CANDIDATE_PATH]
        )

        # Round 2 (still the same chain): completion is still correctly
        # blocked -- zero features exist either way, so the completion gate
        # also fires, and round 3's continuation receives that rejection.
        completion_rejected_output = agent_3d.continue_step_requests[1]["tool_outputs"][0]
        self.assertEqual(completion_rejected_output["call_id"], "call-1")
        completion_rejected = json.loads(completion_rejected_output["output"])
        self.assertEqual(completion_rejected["error"]["code"], "STEP_REQUIRES_A_FEATURE")

        # The script ran out after that -- the job fails on the next empty
        # decision rather than ever completing on nothing.
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "AGENT_NO_ACTION")


class StepValidationTests(unittest.TestCase):
    """The step-level validation gate on request_step_completion."""

    @staticmethod
    def _completion_outputs(agent_3d) -> list[dict]:
        """Every function_call_output the agent was fed back, in order."""

        return [
            output
            for request in agent_3d.continue_step_requests
            for output in request["tool_outputs"]
        ]

    def test_passing_step_validation_completes_the_step(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(repository.validation_calls), 1)
        call = repository.validation_calls[0]
        self.assertEqual(
            call["candidate_path"],
            f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/validation-1/model.py",
        )
        self.assertEqual(call["validation_run"], 1)

    def test_repairable_failure_keeps_the_step_in_progress_and_feeds_diagnostics(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report(line=21)},
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="call-2"),
                completion_turn(call_id="call-3"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        # The chain continued rather than restarting: a failed gate is not a
        # step change, so the agent keeps its own context.
        self.assertGreaterEqual(len(agent_3d.continue_step_requests), 1)
        self.assertEqual(len(agent_3d.start_step_requests), 1)
        rejection = json.loads(self._completion_outputs(agent_3d)[0]["output"])
        self.assertFalse(rejection["ok"])
        self.assertEqual(rejection["error"]["code"], "CANDIDATE_VALIDATION_FAILED")
        self.assertEqual(
            rejection["error"]["details"]["diagnostics"][0]["line"], 21
        )

    def test_a_step_failure_never_appends_a_plan_step(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},
        ]
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="call-2"),
                completion_turn(call_id="call-3"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(len(result["high_level_plan"]["steps"]), 1)

    def test_a_validation_rejected_completion_traces_as_failed(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},
        ]
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="call-2"),
                completion_turn(call_id="call-3"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        completions = [
            event
            for event in orchestrator.trace_writer.calls
            if event["event"] == "tool.completed"
            and event["tool_id"] == "request_step_completion"
        ]
        self.assertFalse(completions[0]["ok"])
        self.assertEqual(
            completions[0]["result"]["error"]["code"], "CANDIDATE_VALIDATION_FAILED"
        )

    def test_the_deterministic_gate_runs_before_any_validation(self):
        repository = FakeRepository()
        repository.files[ACCEPTED_SOURCE_PATH] = EMPTY_SKELETON_SOURCE
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="call-2"),
                completion_turn(call_id="call-3"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        # STEP_REQUIRES_A_FEATURE short-circuits before the validator is paid
        # for: the first completion never queued a run.
        first = json.loads(self._completion_outputs(agent_3d)[0]["output"])
        self.assertEqual(first["error"]["code"], "STEP_REQUIRES_A_FEATURE")

    def test_an_unchanged_candidate_is_rejected_without_revalidating(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                completion_turn(call_id="call-2"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        # Only the first completion paid for a validation run; the second saw
        # a byte-identical candidate and was rejected deterministically.
        self.assertEqual(len(repository.validation_calls), 1)
        second = json.loads(self._completion_outputs(agent_3d)[1]["output"])
        self.assertEqual(second["error"]["code"], "STEP_VALIDATION_NO_CHANGE")

    def test_a_changed_candidate_is_validated_again(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},
            {"status": "completed", "result": passed_report()},
        ]
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="call-2"),
                completion_turn(call_id="call-3"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(repository.validation_calls), 2)
        self.assertEqual(repository.validation_calls[1]["validation_run"], 2)

    def test_a_passing_step_gate_is_reused_as_the_final_proof(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        # The last step's own gate already proved these exact bytes, so _run
        # must not queue a second, identical final validation.
        self.assertEqual(len(repository.validation_calls), 1)

    def _seed_passing_validation(self, repository, *, source_sha256: str) -> None:
        """Record a completed passing validation on the job, as a prior worker would."""

        proof_path = (
            f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/validation-1/model.py"
        )
        repository.generation_jobs["validation-earlier"] = {
            "id": "validation-earlier",
            "edit_job_id": EDIT_JOB_ID,
            "type": "validate_cad",
            "source_kind": "candidate",
            "source_storage_path": proof_path,
            "source_sha256": source_sha256,
            "status": "completed",
            "result": passed_report(),
            "error_message": None,
        }
        repository.files[proof_path] = ACCEPTED_SOURCE
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
            {
                "event": "candidate_validation",
                "purpose": "step",
                "step_id": "PS-1",
                "outcome": "passed",
                "validation_run": 1,
                "validation_job_id": "validation-earlier",
                "source_sha256": source_sha256,
                "storage_path": proof_path,
                "stage": "completed",
                "diagnostics": [],
            },
        ]

    def test_a_seeded_proof_for_the_current_bytes_skips_revalidation(self):
        repository = FakeRepository()
        self._seed_passing_validation(
            repository, source_sha256=source_hash(ACCEPTED_SOURCE)
        )
        orchestrator, *_rest = runtime(repository)

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        # A previous worker already proved these exact bytes, so the resumed
        # job commits on that proof without paying for validation again.
        self.assertEqual(repository.validation_calls, [])

    def test_a_seeded_proof_for_other_bytes_is_never_reused(self):
        repository = FakeRepository()
        self._seed_passing_validation(repository, source_sha256="a" * 64)
        orchestrator, *_rest = runtime(repository)

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        # The seeded proof describes source this candidate no longer has, so
        # it is discarded and the real candidate is validated before commit.
        self.assertEqual(len(repository.validation_calls), 1)

    def test_an_infrastructure_failure_at_a_step_gate_never_reaches_the_agent(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": timeout_report()},
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                completion_turn(call_id="call-2"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "VALIDATION_FAILED")
        self.assertEqual(agent_3d.continue_step_requests, [])

    def test_a_report_describing_other_bytes_is_infrastructure(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {
                "status": "failed",
                "result": repairable_report(),
                # Repairable-looking, but the verdict is about a different
                # candidate, so its diagnostics say nothing about ours.
                "source_sha256": "f" * 64,
            },
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository, agent_script=[completion_turn(call_id="call-1")]
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["error_code"], "VALIDATION_FAILED")
        self.assertEqual(agent_3d.continue_step_requests, [])

    def test_a_schema_one_report_is_infrastructure(self):
        repository = FakeRepository()
        legacy = {"status": "failed", "valid": False, "repairable_hint": True}
        repository.validation_outcomes = [{"status": "failed", "result": legacy}]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository, agent_script=[completion_turn(call_id="call-1")]
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["error_code"], "VALIDATION_FAILED")
        self.assertEqual(agent_3d.continue_step_requests, [])

    def test_a_repairable_report_with_no_diagnostics_is_infrastructure(self):
        repository = FakeRepository()
        empty = {**repairable_report(), "diagnostics": []}
        repository.validation_outcomes = [{"status": "failed", "result": empty}]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository, agent_script=[completion_turn(call_id="call-1")]
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["error_code"], "VALIDATION_FAILED")
        self.assertEqual(agent_3d.continue_step_requests, [])

    def test_the_gate_stops_validating_after_its_rejection_budget(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()}
            for _ in range(MAX_STEP_VALIDATION_REJECTIONS)
        ]
        # Distinct feature names, because the tool rejects a duplicate
        # semantic ID and requires a snake_case verb_noun function name.
        names = ["base_plate", "top_flange", "side_rib", "corner_gusset"]
        script: list[list] = [completion_turn(call_id="call-0")]
        for index in range(MAX_STEP_VALIDATION_REJECTIONS):
            script.append(create_feature_turn(call_id=f"m-{index}", name=names[index]))
            script.append(completion_turn(call_id=f"c-{index}"))
        orchestrator, *_rest = runtime(repository, agent_script=script)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        # Two rejections, then the gate stands down and the completion is
        # honored -- the defect goes on to the final boundary rather than
        # burning the step's turn budget and failing the job mid-plan.
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            len([c for c in repository.validation_calls]),
            MAX_STEP_VALIDATION_REJECTIONS + 1,  # + the final gate
        )

    def test_state_returns_to_applying_edit_after_an_in_loop_validation(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},
        ]
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="call-2"),
                completion_turn(call_id="call-3"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        states = [event["state"] for event in repository.events]
        first_validation = states.index("validating_candidate")
        # The public progress stream must not keep claiming the job is
        # validating while the agent is back to editing.
        self.assertIn("applying_edit", states[first_validation:])

    def test_a_seeded_failure_is_replayed_after_a_restart(self):
        repository = FakeRepository()
        candidate_sha256 = source_hash(ACCEPTED_SOURCE)
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
            {
                "event": "candidate_validation",
                "purpose": "step",
                "step_id": "PS-1",
                "outcome": "repairable",
                "validation_run": 1,
                "validation_job_id": "validation-earlier",
                "source_sha256": candidate_sha256,
                "storage_path": "ignored",
                "stage": "cadquery_runtime",
                "diagnostics": [
                    {"error_code": "CADQUERY_RUNTIME_ERROR", "message": "Bad shape."}
                ],
            },
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                completion_turn(call_id="call-2"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        # The replacement worker knows these exact bytes already failed, so
        # it rejects the completion without paying for the run again.
        first = json.loads(self._completion_outputs(agent_3d)[0]["output"])
        self.assertEqual(first["error"]["code"], "STEP_VALIDATION_NO_CHANGE")
        self.assertEqual(repository.validation_calls, [])

    def test_step_validation_can_be_disabled_without_disabling_the_final_gate(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)
        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        # The step gate stood down, but the whole-plan candidate was still
        # validated exactly once before commit.
        self.assertEqual(len(repository.validation_calls), 1)
        self.assertEqual(repository.validation_calls[0]["validation_run"], 1)


class DisconnectedSolidsGateTests(unittest.TestCase):
    """A part is one connected printable solid, enforced from the validation
    report the gate already reads.

    This is the failure that motivated the gate: a real run split the model
    into two solids at PS-4, validated clean four times because the validator
    checks executability rather than topology, and burned the next step's
    whole turn budget trying to fillet two disjoint bodies together.
    """

    @staticmethod
    def _outputs(agent_3d) -> list[dict]:
        return [
            json.loads(output["output"])
            for request in agent_3d.continue_step_requests
            for output in request["tool_outputs"]
        ]

    def test_a_two_solid_candidate_cannot_complete_a_step(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "completed", "result": passed_report(solid_count=2)},
            {"status": "completed", "result": passed_report(solid_count=1)},
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="call-2"),
                completion_turn(call_id="call-3"),
            ],
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        rejection = self._outputs(agent_3d)[0]
        self.assertFalse(rejection["ok"])
        self.assertEqual(rejection["error"]["code"], "CANDIDATE_VALIDATION_FAILED")
        codes = [d["error_code"] for d in rejection["error"]["details"]["diagnostics"]]
        self.assertIn("DISCONNECTED_SOLIDS", codes)

    def test_the_rejection_tells_the_agent_the_solid_count(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "completed", "result": passed_report(solid_count=3)},
            {"status": "completed", "result": passed_report(solid_count=1)},
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="call-2"),
                completion_turn(call_id="call-3"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        diagnostic = self._outputs(agent_3d)[0]["error"]["details"]["diagnostics"][0]
        self.assertEqual(diagnostic["solid_count"], 3)
        self.assertIn("3 disconnected solids", diagnostic["message"])
        self.assertEqual(diagnostic["function_name"], "build_model")

    def test_a_one_solid_candidate_completes_normally(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(repository.validation_calls), 1)

    def test_a_report_without_a_measurement_is_not_failed(self):
        # Reports predating the measurement must not fail a candidate that
        # otherwise validated cleanly.
        repository = FakeRepository()
        legacy = passed_report()
        legacy["build_artifacts"] = None
        repository.validation_result = legacy
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")

    def test_a_multi_solid_final_candidate_drives_the_repair_loop(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "completed", "result": passed_report(solid_count=2)},
            {"status": "completed", "result": passed_report(solid_count=1)},
        ]
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="r-1", name="top_flange"),
                completion_turn(call_id="r-2"),
            ],
        )

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        appended = [
            event
            for event in repository.jobs[EDIT_JOB_ID]["history"]
            if event.get("event") == "repair_step_appended"
        ]
        self.assertEqual(len(appended), 1)
        self.assertIn("DISCONNECTED_SOLIDS", appended[0]["trigger"]["error_codes"])

    def test_a_seeded_measurement_survives_a_restart(self):
        repository = FakeRepository()
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
            {
                "event": "candidate_validation",
                "purpose": "step",
                "step_id": "PS-1",
                "outcome": "repairable",
                "source_sha256": source_hash(ACCEPTED_SOURCE),
                "diagnostics": [
                    {"error_code": "DISCONNECTED_SOLIDS", "message": "2 solids."}
                ],
                "geometry": {"solid_count": 2, "volume_mm3": 200301.7},
            },
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository, agent_script=[completion_turn(call_id="call-1")]
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        # The most recent measurement of any outcome, not the last passing
        # one: after a failure the failing geometry is what the part is.
        geometry = agent_3d.start_step_requests[0]["project_inventory"][
            "current_part"
        ]["geometry"]
        self.assertEqual(geometry["solid_count"], 2)

    def test_a_measured_candidate_reaches_the_next_step_as_inventory(self):
        repository = FakeRepository()
        repository.validation_result = passed_report(
            solid_count=1, volume_mm3=70200.0, face_count=6, edge_count=12
        )
        plan = CadPlan.model_validate(
            {
                **plan_output().model_dump(mode="json"),
                "plan_id": str(uuid4()),
                "goal_id": GOAL_ID,
                "version": 1,
                "steps": [
                    {
                        "step_id": "PS-1",
                        "sequence": 1,
                        "objective": "First.",
                        "depends_on": [],
                        "addresses_criteria": ["GC-1"],
                        "status": "pending",
                    },
                    {
                        "step_id": "PS-2",
                        "sequence": 2,
                        "objective": "Second.",
                        "depends_on": ["PS-1"],
                        "addresses_criteria": [],
                        "status": "pending",
                    },
                ],
            }
        )
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
            {"event": "plan_created", "plan": plan.model_dump(mode="json")},
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        # PS-2 opens already knowing what PS-1 built, so it need not spend a
        # round on check_geometry to find out.
        second = agent_3d.start_step_requests[1]
        geometry = second["project_inventory"]["current_part"]["geometry"]
        self.assertEqual(geometry["solid_count"], 1)
        self.assertEqual(geometry["volume_mm3"], 70200.0)


class RunMetricsTests(unittest.TestCase):
    """edit_jobs.metrics: the per-run facts edit_job_events cannot carry.

    Deliberately excludes anything already durable as an event -- per-step
    turns, validation outcomes, timings -- so the two do not disagree.
    """

    def test_a_completed_run_records_its_tool_mix_and_turns(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                create_feature_turn(call_id="call-1"),
                completion_turn(call_id="call-2"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        metrics = repository.jobs[EDIT_JOB_ID]["metrics"]
        self.assertEqual(metrics["agent_turns"], 2)
        self.assertEqual(metrics["tool_calls"], 2)
        self.assertEqual(
            metrics["tool_mix"],
            {"create_feature": 1, "request_step_completion": 1},
        )
        self.assertEqual(metrics["validation_runs"], 1)
        self.assertEqual(metrics["tool_failures"], {})

    def test_a_failed_run_records_metrics_too(self):
        # Failed runs are the ones you go looking for, so they must carry
        # their counters as well.
        repository = FakeRepository()
        repository.validation_result = timeout_report()
        repository.validation_status = "failed"
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        metrics = repository.jobs[EDIT_JOB_ID]["metrics"]
        self.assertGreater(metrics["agent_turns"], 0)
        self.assertGreater(metrics["validation_runs"], 0)

    def test_batched_rounds_are_counted(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                [
                    tool_call("create_parameter", "p-1", parameter_name="a_mm", value=1.0),
                    tool_call("create_parameter", "p-2", parameter_name="b_mm", value=2.0),
                ],
                completion_turn(call_id="done"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        metrics = repository.jobs[EDIT_JOB_ID]["metrics"]
        self.assertEqual(metrics["batched_rounds"], 1)
        self.assertEqual(metrics["tool_calls"], 3)
        self.assertEqual(metrics["agent_turns"], 2)

    def test_tool_failures_are_counted_by_code(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                create_feature_turn(call_id="call-1", name="base_plate"),
                create_feature_turn(call_id="call-2", name="base_plate"),
                completion_turn(call_id="call-3"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        metrics = repository.jobs[EDIT_JOB_ID]["metrics"]
        self.assertEqual(metrics["tool_failures"], {"TOOL_VALIDATION_FAILED": 1})

    def test_counters_do_not_leak_between_runs(self):
        # One worker reuses a single orchestrator across claimed jobs.
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))
        first = copy.deepcopy(repository.jobs[EDIT_JOB_ID]["metrics"])
        repository.jobs[EDIT_JOB_ID].update(
            status="running", state="applying_edit", result=None, history=[]
        )
        orchestrator.run(repository.edit_job(EDIT_JOB_ID))
        second = repository.jobs[EDIT_JOB_ID]["metrics"]

        self.assertEqual(first["agent_turns"], second["agent_turns"])


class RepairStepContractTests(unittest.TestCase):
    """PlanStepState.kind stays server-owned and backward compatible."""

    def test_kind_defaults_to_plan_for_checkpoints_that_predate_repair(self):
        payload = plan_output().model_dump(mode="json")
        payload.update(plan_id=str(uuid4()), goal_id=GOAL_ID, version=1)
        for step in payload["steps"]:
            step.pop("kind", None)

        plan = CadPlan.model_validate(payload)

        self.assertEqual(plan.steps[0].kind, "plan")

    def test_the_planner_is_never_asked_to_emit_kind(self):
        # CadPlanModelOutput is compiled into a strict schema where every
        # property is required, so a `kind` on the planner-facing model would
        # force the planning model to invent one.
        planner_schema = json.dumps(CadPlanModelOutput.model_json_schema())
        self.assertNotIn('"kind"', planner_schema)
        self.assertIn('"kind"', json.dumps(CadPlan.model_json_schema()))

    def test_the_step_count_bounds_survive_the_steps_override(self):
        payload = plan_output().model_dump(mode="json")
        payload.update(plan_id=str(uuid4()), goal_id=GOAL_ID, version=1, steps=[])
        with self.assertRaises(ValueError):
            CadPlan.model_validate(payload)

        payload["steps"] = [
            {
                "step_id": f"PS-{index}",
                "sequence": index,
                "objective": "Too many.",
                "depends_on": [],
                "addresses_criteria": [],
                "status": "pending",
            }
            for index in range(1, 66)
        ]
        with self.assertRaises(ValueError):
            CadPlan.model_validate(payload)


class FinalRepairLoopTests(unittest.TestCase):
    """The bounded repair loop that runs after final validation fails."""

    @staticmethod
    def _history_events(repository, name: str) -> list[dict]:
        return [
            event
            for event in repository.jobs[EDIT_JOB_ID]["history"]
            if event.get("event") == name
        ]

    @staticmethod
    def _final_plan(repository) -> dict:
        updates = [
            event
            for event in repository.jobs[EDIT_JOB_ID]["history"]
            if event.get("event") == "plan_updated"
        ]
        return updates[-1]["plan"]

    def test_a_passing_final_validation_never_appends_a_repair_step(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(self._history_events(repository, "repair_step_appended"), [])

    def test_a_repairable_final_failure_appends_exactly_one_repair_step(self):
        repository = FakeRepository()
        # Step gate off, so the first validation the job runs is the final
        # one -- this isolates the final boundary's own behavior.
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},
            {"status": "completed", "result": passed_report()},
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="call-2"),
                completion_turn(call_id="call-3"),
            ],
        )

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        appended = self._history_events(repository, "repair_step_appended")
        self.assertEqual(len(appended), 1)
        steps = self._final_plan(repository)["steps"]
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[1]["kind"], "repair")
        self.assertEqual(steps[1]["step_id"], "PS-2")
        self.assertEqual(steps[1]["addresses_criteria"], [])
        self.assertEqual(steps[1]["depends_on"], ["PS-1"])
        # A repair step is a plan revision, unlike a status change.
        self.assertEqual(self._final_plan(repository)["version"], 2)

    def test_the_repair_step_opens_a_fresh_chain_with_the_diagnostics(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report(line=42)},
            {"status": "completed", "result": passed_report()},
        ]
        orchestrator, _creator, _planner, _trace, agent_3d, _tools = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="call-2"),
                completion_turn(call_id="call-3"),
            ],
        )

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        # Two start_step calls: PS-1, then a brand-new chain for the repair
        # step -- never a continuation of the previous step's chain.
        repair_start = agent_3d.start_step_requests[-1]
        self.assertEqual(repair_start["active_step"].kind, "repair")
        feedback = repair_start["validation_feedback"]
        self.assertEqual(feedback["diagnostics"][0]["line"], 42)

    def test_repair_completion_is_gated_by_validation_and_appends_no_second_step(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},   # final
            {"status": "failed", "result": repairable_report()},   # repair try 1
            {"status": "completed", "result": passed_report()},    # repair try 2
        ]
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                # repair chain
                create_feature_turn(call_id="r-1", name="top_flange"),
                completion_turn(call_id="r-2"),
                create_feature_turn(call_id="r-3", name="side_rib"),
                completion_turn(call_id="r-4"),
            ],
        )

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        # One repair step containing two attempts -- not a step per attempt.
        self.assertEqual(len(self._history_events(repository, "repair_step_appended")), 1)
        self.assertEqual(len(self._final_plan(repository)["steps"]), 2)

    def test_a_successful_repair_commits_without_revalidating(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},
            {"status": "completed", "result": passed_report()},
        ]
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="r-1", name="top_flange"),
                completion_turn(call_id="r-2"),
            ],
        )

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        # The repair step's own gate is the final gate: its passing verdict
        # is the proof commit uses, so no third run is queued.
        self.assertEqual(len(repository.validation_calls), 2)

    def test_the_repair_budget_bounds_attempts_within_one_repair_step(self):
        repository = FakeRepository()
        repository.validation_result = repairable_report()
        repository.validation_status = "failed"
        names = ["top_flange", "side_rib", "corner_gusset", "edge_fillet"]
        script: list[list] = [completion_turn(call_id="call-1")]
        for index in range(MAX_REPAIR_ATTEMPTS + 1):
            script.append(create_feature_turn(call_id=f"r-{index}", name=names[index]))
            script.append(completion_turn(call_id=f"rc-{index}"))
        orchestrator, *_rest = runtime(repository, agent_script=script)

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "REPAIR_BUDGET_EXHAUSTED")
        self.assertEqual(
            result["details"]["repair_attempts"], MAX_REPAIR_ATTEMPTS
        )
        # One repair step holding every attempt -- a failed repair never
        # appends another step.
        self.assertEqual(
            len(self._history_events(repository, "repair_step_appended")), 1
        )
        self.assertEqual(repository.files[ACCEPTED_SOURCE_PATH], ACCEPTED_SOURCE)

    def test_a_non_repairable_final_failure_never_appends_a_repair_step(self):
        repository = FakeRepository()
        repository.validation_result = timeout_report()
        repository.validation_status = "failed"
        orchestrator, *_rest = runtime(repository)

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["error_code"], "VALIDATION_FAILED")
        self.assertEqual(self._history_events(repository, "repair_step_appended"), [])

    def test_completed_steps_are_never_rewritten_by_repair(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},
            {"status": "completed", "result": passed_report()},
        ]
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="r-1", name="top_flange"),
                completion_turn(call_id="r-2"),
            ],
        )

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        steps = self._final_plan(repository)["steps"]
        self.assertEqual(steps[0]["status"], "completed")
        self.assertEqual(steps[0]["kind"], "plan")

    def test_a_resumed_repair_step_is_not_appended_again(self):
        repository = FakeRepository()
        repaired_plan = {
            **plan_output().model_dump(mode="json"),
            "plan_id": str(uuid4()),
            "goal_id": GOAL_ID,
            "version": 2,
            "steps": [
                {
                    "step_id": "PS-1",
                    "sequence": 1,
                    "objective": "Change the bracket height.",
                    "depends_on": [],
                    "addresses_criteria": ["GC-1"],
                    "status": "completed",
                    "kind": "plan",
                },
                {
                    "step_id": "PS-2",
                    "sequence": 2,
                    "objective": "Resolve the reported failures.",
                    "depends_on": ["PS-1"],
                    "addresses_criteria": [],
                    "status": "pending",
                    "kind": "repair",
                },
            ],
        }
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
            {"event": "plan_created", "plan": repaired_plan},
            {"event": "plan_updated", "plan": repaired_plan},
        ]
        orchestrator, *_rest = runtime(
            repository, agent_script=[completion_turn(call_id="call-1")]
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        # The checkpointed plan already carried the repair step, so the loop
        # simply ran it -- the append path is never re-entered.
        self.assertEqual(self._history_events(repository, "repair_step_appended"), [])
        self.assertEqual(len(self._final_plan(repository)["steps"]), 2)

    def test_a_recorded_trigger_without_its_plan_write_still_appends_once(self):
        repository = FakeRepository()
        repository.validation_outcomes = [
            {"status": "failed", "result": repairable_report()},
            {"status": "completed", "result": passed_report()},
        ]
        # A previous worker recorded the trigger and died before the plan
        # checkpoint landed, so the plan it left behind has no repair step.
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
            {
                "event": "repair_step_appended",
                "step_id": "PS-2",
                "repair_attempt": 1,
                "plan_version": 2,
                "trigger": {
                    "validation_job_id": "validation-earlier",
                    "source_sha256": "c" * 64,
                    "stage": "cadquery_runtime",
                    "error_codes": ["CADQUERY_RUNTIME_ERROR"],
                },
            },
        ]
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                completion_turn(call_id="call-1"),
                create_feature_turn(call_id="r-1", name="top_flange"),
                completion_turn(call_id="r-2"),
            ],
        )

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        steps = self._final_plan(repository)["steps"]
        self.assertEqual([step["kind"] for step in steps], ["plan", "repair"])

    def test_a_full_plan_fails_cleanly_rather_than_raising_a_validation_error(self):
        repository = FakeRepository()
        repository.validation_result = repairable_report()
        repository.validation_status = "failed"
        full_plan = {
            **plan_output().model_dump(mode="json"),
            "plan_id": str(uuid4()),
            "goal_id": GOAL_ID,
            "version": 1,
            "steps": [
                {
                    "step_id": f"PS-{index}",
                    "sequence": index,
                    "objective": "Already done.",
                    "depends_on": [],
                    "addresses_criteria": ["GC-1"] if index == 1 else [],
                    "status": "completed",
                    "kind": "plan",
                }
                for index in range(1, 65)
            ],
        }
        repository.jobs[EDIT_JOB_ID]["history"] = [
            {"event": "goal_created", "goal": goal().model_dump(mode="json")},
            {"event": "plan_created", "plan": full_plan},
            {"event": "plan_updated", "plan": full_plan},
        ]
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["error_code"], "REPAIR_PLAN_FULL")

    def test_the_repair_chain_uses_its_own_turn_budget(self):
        repository = FakeRepository()
        repository.validation_result = repairable_report()
        repository.validation_status = "failed"
        # Never completes the repair step, so the budget is what stops it.
        script = [completion_turn(call_id="call-1")]
        script += [
            create_feature_turn(call_id=f"r-{index}", name=f"filler_{'x' * index}")
            for index in range(MAX_REPAIR_TURNS + 2)
        ]
        orchestrator, *_rest = runtime(repository, agent_script=script)

        with unittest.mock.patch.dict(
            os.environ, {"CAD_EDITOR_STEP_VALIDATION": "0"}
        ):
            result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "AGENT_TURN_LIMIT")
        self.assertEqual(result["details"]["turn_budget"], MAX_REPAIR_TURNS)


class CommitPipelineTests(unittest.TestCase):
    def test_infrastructure_validation_failure_fails_without_touching_canonical(self):
        repository = FakeRepository()
        repository.validation_result = worker_error_report()
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "VALIDATION_FAILED")
        self.assertEqual(repository.files[ACCEPTED_SOURCE_PATH], ACCEPTED_SOURCE)

    def test_source_changed_before_commit_fails_the_job(self):
        repository = FakeRepository()
        conflicting_source = ACCEPTED_SOURCE + "\n# someone else committed this\n"

        def mutate() -> None:
            repository.files[ACCEPTED_SOURCE_PATH] = conflicting_source

        repository.mutate_canonical_after_validation = mutate
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "SOURCE_CHANGED")
        self.assertEqual(repository.files[ACCEPTED_SOURCE_PATH], conflicting_source)

    def test_commit_is_idempotent_when_canonical_already_matches_the_candidate(self):
        repository = FakeRepository()

        def simulate_already_committed() -> None:
            # Canonical already holds the candidate's final content (as if a
            # prior worker committed successfully and crashed before
            # finishing reindex); accepted_source_sha256 still reflects the
            # original source this job started from.
            repository.files[ACCEPTED_SOURCE_PATH] = repository.files[CANDIDATE_PATH]

        repository.mutate_canonical_after_validation = simulate_already_committed
        orchestrator, *_rest = runtime(
            repository, agent_script=[create_feature_turn(), completion_turn()]
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outcome"], "committed")
        # The idempotent branch never re-writes canonical -- only the
        # bootstrap-time original backup and the candidate/attempt copies do.
        self.assertNotIn(ACCEPTED_SOURCE_PATH, repository.writes)

    def test_reindex_failure_rolls_back_canonical_and_fails_the_job(self):
        repository = FakeRepository()
        repository.index_should_fail = True
        orchestrator, *_rest = runtime(
            repository, agent_script=[create_feature_turn(), completion_turn()]
        )

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "REINDEX_FAILED")
        self.assertTrue(result["details"]["rolled_back"])
        self.assertEqual(repository.files[ACCEPTED_SOURCE_PATH], ACCEPTED_SOURCE)

    def test_export_failure_is_a_warning_not_a_job_failure(self):
        repository = FakeRepository()
        repository.export_should_fail = True
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outcome"], "committed")
        self.assertIsNone(result["export_job_id"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("export", result["warnings"][0])

    def test_validation_is_queued_against_a_run_scoped_path(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(len(repository.validation_calls), 1)
        call = repository.validation_calls[0]
        self.assertEqual(
            call["candidate_path"],
            f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/validation-1/model.py",
        )
        self.assertEqual(call["validation_run"], 1)


class AgentTraceEventTests(unittest.TestCase):
    """The orchestrator's own tool/step/loop trace events -- Agent3D's
    llm.request/llm.response events are FakeAgent3D-bypassed here and covered
    directly in test_agent_3d.py instead.
    """

    def _events(self, orchestrator, *event_types: str) -> list[dict]:
        return [
            call
            for call in orchestrator.trace_writer.calls
            if call["event"] in event_types
        ]

    def test_step_started_is_traced_once_for_a_single_step_plan(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        started = self._events(orchestrator, "step.started")
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["edit_job_id"], EDIT_JOB_ID)
        self.assertEqual(started[0]["step_id"], "PS-1")
        self.assertIsNone(started[0]["previous_step_id"])
        self.assertEqual(started[0]["observation_count_before"], 0)

    def test_step_changed_is_traced_with_the_previous_step_id(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                completion_turn("First step done.", "call-1"),
                completion_turn("Second step done.", "call-2"),
            ],
        )
        AgentLoopTests._seed_two_step_plan(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        transitions = self._events(orchestrator, "step.started", "step.changed")
        self.assertEqual([event["event"] for event in transitions], ["step.started", "step.changed"])
        self.assertEqual(transitions[0]["step_id"], "PS-1")
        self.assertIsNone(transitions[0]["previous_step_id"])
        self.assertEqual(transitions[1]["step_id"], "PS-2")
        self.assertEqual(transitions[1]["previous_step_id"], "PS-1")

    def test_tool_started_and_completed_are_traced_with_correlation_ids(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        started = self._events(orchestrator, "tool.started")
        completed = self._events(orchestrator, "tool.completed")
        self.assertEqual(len(started), 1)
        self.assertEqual(len(completed), 1)
        self.assertEqual(started[0]["tool_id"], "request_step_completion")
        self.assertEqual(started[0]["step_id"], "PS-1")
        self.assertEqual(started[0]["agent_turn"], 1)
        self.assertEqual(started[0]["step_attempt"], 1)
        self.assertEqual(started[0]["reasoning_round"], 1)
        self.assertEqual(started[0]["response_id"], "decision-1")
        self.assertEqual(started[0]["tool_call_id"], "call-1")
        self.assertEqual(completed[0]["tool_call_id"], started[0]["tool_call_id"])
        self.assertTrue(completed[0]["ok"])
        self.assertIn("result", completed[0])
        self.assertIsInstance(completed[0]["duration_seconds"], float)

    def test_a_batched_round_traces_one_pair_per_call_sharing_one_response_id(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(
            repository,
            agent_script=[
                [
                    tool_call("index_get_feature", "call-1", semantic_id="a"),
                    tool_call("index_get_feature", "call-2", semantic_id="b"),
                ],
                completion_turn("Done.", "call-3"),
            ],
        )

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        started = self._events(orchestrator, "tool.started")
        completed = self._events(orchestrator, "tool.completed")
        self.assertEqual(len(started), 3)
        self.assertEqual(len(completed), 3)
        first_round = [event for event in started if event["reasoning_round"] == 1]
        self.assertEqual(len(first_round), 2)
        self.assertEqual([event["tool_call_id"] for event in first_round], ["call-1", "call-2"])
        self.assertEqual({event["response_id"] for event in first_round}, {"decision-1"})
        self.assertEqual({event["agent_turn"] for event in first_round}, {1})

    def test_tool_completed_reflects_a_gate_blocked_result_not_the_original(self):
        repository = FakeRepository()
        EmptyPartCompletionGateTests._seed_single_step_plan(
            repository, criterion_type="required"
        )
        orchestrator, *_rest = runtime(repository, agent_script=[completion_turn()])

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        completed = self._events(orchestrator, "tool.completed")
        self.assertEqual(len(completed), 1)
        self.assertFalse(completed[0]["ok"])
        self.assertEqual(completed[0]["result"]["error"]["code"], "STEP_REQUIRES_A_FEATURE")

    def test_step_completed_is_traced_when_the_step_finishes(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        completed = self._events(orchestrator, "step.completed")
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["step_id"], "PS-1")
        self.assertEqual(completed[0]["summary"], "Step done.")

    def test_agent_loop_completed_is_traced_once_the_plan_is_done(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        completed = self._events(orchestrator, "agent_loop.completed")
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["agent_turn"], 1)

    def test_agent_loop_failed_is_traced_on_agent_no_action(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository, agent_script=[[]])

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        failed = self._events(orchestrator, "agent_loop.failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["error_code"], "AGENT_NO_ACTION")
        self.assertEqual(failed[0]["step_id"], "PS-1")

    def test_a_trace_write_failure_after_a_tool_call_does_not_fail_the_job(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        class BrokenTraceWriter:
            def log(self, event, *, edit_job_id, **_fields):
                raise OSError("disk full")

        orchestrator.trace_writer = BrokenTraceWriter()

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(result["status"], "completed")


if __name__ == "__main__":
    unittest.main()
