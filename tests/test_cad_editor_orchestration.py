from __future__ import annotations

import copy
import json
import unittest
from types import SimpleNamespace
from uuid import uuid4

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.orchestrator import (
    MAX_AGENT_TURNS,
    MAX_STEP_TURNS,
    EditWorkflowOrchestrator,
    _active_step,
    _count_cad_features,
    _recent_messages,
    _step_needs_new_geometry,
    _with_step_status,
)
from workers.agent_3d.planning.agent_contracts import (
    CadGoal,
    CadPlan,
    CadPlanModelOutput,
)
from workers.agent_3d.tools import (
    CreateFeatureTool,
    EditCadBuildModelTool,
    RequestStepCompletionTool,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
    ToolServices,
)


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
        self.validation_result: dict = {"status": "passed", "valid": True}
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

    def queue_validation(
        self,
        edit_job_id: str,
        candidate_path: str,
        candidate_sha256: str,
        attempt_count: int,
        *,
        worker_id: str,
    ) -> str:
        self._owned(worker_id)
        self.validation_calls.append(
            {
                "edit_job_id": edit_job_id,
                "candidate_path": candidate_path,
                "candidate_sha256": candidate_sha256,
                "attempt_count": attempt_count,
            }
        )
        self.generation_count += 1
        generation_id = f"validation-{self.generation_count}"
        self.generation_jobs[generation_id] = {
            "id": generation_id,
            "edit_job_id": edit_job_id,
            "type": "validate_cad",
            "source_kind": "candidate",
            "source_storage_path": candidate_path,
            "source_sha256": candidate_sha256,
            "status": self.validation_status,
            "result": copy.deepcopy(self.validation_result),
            "error_message": None,
        }
        self.jobs[edit_job_id].update(
            state="validating_candidate",
            attempt_count=attempt_count,
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
    ) -> dict:
        self._owned(worker_id)
        self.jobs[edit_job_id].update(
            status="completed",
            state="completed",
            result=copy.deepcopy(result),
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
    ) -> dict:
        self._owned(worker_id)
        self.jobs[edit_job_id].update(
            status="failed",
            state="failed",
            error_code=code,
            error_message=message,
            result=copy.deepcopy(result),
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


def create_feature_turn(call_id: str = "call-1"):
    """Build a decision that adds one valid CAD feature to the candidate."""

    return [
        tool_call(
            "create_feature",
            call_id,
            semantic_id="base_plate",
            function_name="build_base_plate",
            role="primary_body",
            parameters=[],
            dependencies=[],
            search_keys=["base", "plate"],
            docstring="Build the base plate.",
            function_body='return cq.Workplane("XY").box(100, 100, 5)',
        )
    ]


class FakeAgent3D:
    """Agent stand-in returning scripted decisions, one per loop turn.

    Without a script it completes whichever step is active on that step's
    first turn, so the plan always runs to completion.
    """

    def __init__(self, trace: list[str] | None = None, script: list[list] | None = None):
        self.trace = trace
        self.invoked = False
        self.calls = 0
        self.requests: list[dict] = []
        self.script = list(script) if script is not None else None
        self.tool_catalog = (
            {"type": "function", "name": "request_step_completion"},
            {"type": "function", "name": "index_search"},
            {"type": "function", "name": "create_feature"},
            {"type": "function", "name": "edit_cad_build_model"},
        )

    def decide(self, **kwargs):
        self.invoked = True
        self.calls += 1
        self.requests.append(kwargs)
        if self.trace is not None:
            self.trace.append("agent-decision")
        if self.script is None:
            output = completion_turn()
        elif self.script:
            output = self.script.pop(0)
        else:
            output = []
        return SimpleNamespace(id=f"decision-{self.calls}", output=list(output))


class RecordingToolExecutor:
    """Real tool dispatch through a registry, with per-call recording."""

    def __init__(self):
        registry = ToolRegistry()
        registry.register(RequestStepCompletionTool())
        registry.register(CreateFeatureTool())
        registry.register(EditCadBuildModelTool())
        self._executor = ToolExecutor(registry)
        self.invoked = False
        self.calls: list[dict] = []

    def execute_sync(self, **kwargs):
        self.invoked = True
        self.calls.append(kwargs)
        return self._executor.execute_sync(**kwargs)


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
        call = tool_call("index_search", "call-1", query="bracket", limit=5)

        tool_id, _arguments, result = orchestrator._execute_call(
            call, tool_context, {"index_search"}, observations
        )

        self.assertEqual(tool_id, "index_search")
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "TOOL_CALL_REDUNDANT")
        # RecordingToolExecutor never registers index_search, so had the
        # redundancy check not short-circuited, execute_sync would have run
        # and failed with TOOL_NOT_FOUND instead -- proving this rejection
        # came from the pre-dispatch check, not the tool itself.
        self.assertFalse(tool_executor.invoked)


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
        self.assertEqual(result["validation_result"], {"status": "passed", "valid": True})
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
        self.assertEqual(agent_3d.requests[0]["observations"], [])
        carried = agent_3d.requests[1]["observations"]
        self.assertEqual(len(carried), 1)
        self.assertEqual(carried[0]["tool_id"], "index_search")
        self.assertEqual(carried[0]["arguments"], {"query": "bracket", "limit": 3})
        # index_search is outside this agent's catalog stand-in registry.
        self.assertFalse(carried[0]["result"]["ok"])

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
        self.assertEqual(agent_3d.requests[0]["active_step"].step_id, "PS-1")
        self.assertEqual(len(agent_3d.requests[1]["observations"]), 1)
        # PS-2 becomes active and must not inherit PS-1's tool transcript.
        self.assertEqual(agent_3d.requests[2]["active_step"].step_id, "PS-2")
        self.assertEqual(agent_3d.requests[2]["observations"], [])

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


class CandidateBootstrapTests(unittest.TestCase):
    def test_accepted_source_is_copied_to_an_edit_scoped_candidate(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        result = orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(repository.files[CANDIDATE_PATH], ACCEPTED_SOURCE)
        # current_candidate_path/sha256 are set on the job row by the
        # queue_edit_candidate_validation RPC itself once the commit pipeline
        # validates the final candidate, not by _prepare_candidate directly.
        self.assertEqual(
            repository.jobs[EDIT_JOB_ID]["current_candidate_path"],
            f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/attempt-1/model.py",
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
        # PS-1 was already completed, so only PS-2 is worked.
        self.assertEqual(agent_3d.calls, 1)
        self.assertEqual(agent_3d.requests[0]["active_step"].step_id, "PS-2")
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

        # The scripted turn was rejected; the next turn had nothing left in
        # the script, so the loop fails on AGENT_NO_ACTION -- proof the step
        # was never marked completed off the rejected call.
        self.assertEqual(agent_3d.calls, 2)
        rejected = agent_3d.requests[1]["observations"][0]
        self.assertEqual(rejected["tool_id"], "request_step_completion")
        self.assertFalse(rejected["result"]["ok"])
        self.assertEqual(rejected["result"]["error"]["code"], "STEP_REQUIRES_A_FEATURE")
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

        # Turn 1: the wiring attempt is rejected at the tool itself.
        wiring_rejected = agent_3d.requests[1]["observations"][0]
        self.assertEqual(wiring_rejected["tool_id"], "edit_cad_build_model")
        self.assertFalse(wiring_rejected["result"]["ok"])
        self.assertEqual(
            wiring_rejected["result"]["error"]["code"], "TOOL_VALIDATION_FAILED"
        )
        self.assertEqual(
            wiring_rejected["result"]["error"]["details"]["reason"],
            "undefined_function_call",
        )
        # build_model was never actually rewritten to call the missing name.
        self.assertNotIn(
            "build_soap_holder_body", repository.files[CANDIDATE_PATH]
        )

        # Turn 2: completion is still correctly blocked -- zero features
        # exist either way, so the completion gate also fires.
        completion_rejected = agent_3d.requests[2]["observations"][1]
        self.assertEqual(completion_rejected["tool_id"], "request_step_completion")
        self.assertEqual(
            completion_rejected["result"]["error"]["code"], "STEP_REQUIRES_A_FEATURE"
        )

        # The script ran out after that -- the job fails on the next empty
        # decision rather than ever completing on nothing.
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "AGENT_NO_ACTION")


class CommitPipelineTests(unittest.TestCase):
    def test_validation_failure_fails_the_job_without_touching_canonical(self):
        repository = FakeRepository()
        repository.validation_result = {"status": "failed", "valid": False}
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

    def test_validation_is_queued_against_an_attempt_scoped_path(self):
        repository = FakeRepository()
        orchestrator, *_rest = runtime(repository)

        orchestrator.run(repository.edit_job(EDIT_JOB_ID))

        self.assertEqual(len(repository.validation_calls), 1)
        call = repository.validation_calls[0]
        self.assertEqual(
            call["candidate_path"],
            f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/attempt-1/model.py",
        )
        self.assertEqual(call["attempt_count"], 1)


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
        self.assertEqual(started[0]["step_turn"], 1)
        self.assertEqual(started[0]["response_id"], "decision-1")
        self.assertEqual(started[0]["tool_call_id"], "call-1")
        self.assertEqual(completed[0]["tool_call_id"], started[0]["tool_call_id"])
        self.assertTrue(completed[0]["ok"])
        self.assertIn("result", completed[0])
        self.assertIsInstance(completed[0]["duration_seconds"], float)

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
        self.assertEqual(completed[0]["total_turns"], 1)

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
