from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from uuid import UUID

from workers.agent_3d.agent_3d import Agent3D
from workers.agent_3d.planning.agent_contracts import CadGoal, CadPlan, PlanStep
from workers.agent_3d.planning.agent_trace import AgentTraceContext
from workers.agent_3d.planning.prompt_loader import load_agent_reasoning_prompt


GOAL_ID = UUID("33333333-3333-4333-8333-333333333333")
PLAN_ID = UUID("44444444-4444-4444-8444-444444444444")
EDIT_JOB_ID = "55555555-5555-4555-8555-555555555555"

TRACE_CONTEXT = AgentTraceContext(
    edit_job_id=EDIT_JOB_ID, agent_turn=3, step_attempt=1, reasoning_round=2
)


def goal() -> CadGoal:
    return CadGoal.model_validate(
        {
            "goal_id": str(GOAL_ID),
            "raw_request": "Make the drainage holes larger.",
            "description": "Increase the size of the existing drainage openings.",
            "completion_criteria": [
                {
                    "criterion_id": "GC-1",
                    "description": "The drainage openings are larger.",
                    "type": "required",
                }
            ],
            "constraints": [],
            "assumptions": [],
            "clarification": {"required": False, "question": None, "reason": None},
        }
    )


def plan() -> CadPlan:
    return CadPlan.model_validate(
        {
            "plan_id": str(PLAN_ID),
            "goal_id": str(GOAL_ID),
            "version": 1,
            "summary": "Enlarge the drainage holes.",
            "target_bindings": [],
            "steps": [
                {
                    "step_id": "PS-1",
                    "sequence": 1,
                    "objective": "Enlarge the drainage holes.",
                    "depends_on": [],
                    "addresses_criteria": ["GC-1"],
                    "status": "pending",
                }
            ],
        }
    )


def active_step() -> PlanStep:
    return plan().steps[0]


class FakeTraceWriter:
    """Records every trace event instead of writing to disk."""

    def __init__(self):
        self.calls: list[dict] = []

    def log(self, event: str, *, edit_job_id: str, **fields):
        self.calls.append({"event": event, "edit_job_id": edit_job_id, **fields})
        return f"/debug-logs/{edit_job_id}.trace.jsonl"


class FakeAgentResponses:
    def __init__(
        self,
        response: object | None = None,
        responses: list[object] | None = None,
        error: Exception | None = None,
    ):
        self._queue = list(responses) if responses is not None else None
        self._single = response if response is not None else SimpleNamespace(id="r1", output=[])
        self.error = error
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self._queue is not None:
            return self._queue.pop(0) if self._queue else self._single
        return self._single


class FakeAgentClient:
    def __init__(
        self,
        response: object | None = None,
        responses: list[object] | None = None,
        error: Exception | None = None,
    ):
        self.responses = FakeAgentResponses(response=response, responses=responses, error=error)


class Agent3DTests(unittest.TestCase):
    def test_model_client_and_tool_catalog_are_injected(self):
        model_client = object()
        catalog = [{"type": "function", "name": "index_search"}]

        agent = Agent3D(model_client=model_client, tool_catalog=catalog)

        self.assertIs(agent.model_client, model_client)
        self.assertEqual(tuple(agent.tool_catalog), (catalog[0],))

    def test_tool_catalog_is_immutable_after_construction(self):
        catalog = [{"type": "function", "name": "index_search"}]

        agent = Agent3D(model_client=object(), tool_catalog=catalog)
        catalog.append({"type": "function", "name": "index_get_feature"})

        self.assertIsInstance(agent.tool_catalog, tuple)
        self.assertEqual(len(agent.tool_catalog), 1)

    def test_agent_exposes_no_tool_execution_surface(self):
        agent = Agent3D(model_client=object(), tool_catalog=[])

        for attribute in ("execute", "run", "registry", "tool_registry", "tool_executor"):
            self.assertFalse(hasattr(agent, attribute))

    def test_tool_ids_are_not_hardcoded_in_the_static_reasoning_prompt(self):
        static_prompt = load_agent_reasoning_prompt()

        for tool_id in ("index_search", "index_get_feature"):
            self.assertNotIn(tool_id, static_prompt)


def _agent(client: FakeAgentClient, **overrides) -> Agent3D:
    catalog = overrides.pop("tool_catalog", [{"type": "function", "name": "index_search"}])
    overrides.setdefault("trace_writer", FakeTraceWriter())
    return Agent3D(model_client=client, tool_catalog=catalog, **overrides)


class Agent3DStartStepTests(unittest.TestCase):
    def test_goal_plan_and_active_step_appear_in_priority_order(self):
        client = FakeAgentClient()
        agent = _agent(client)

        agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        payload = json.loads(client.responses.requests[0]["input"][0]["content"])
        workflow_state = payload["workflow_state"]
        self.assertEqual(
            list(workflow_state.keys())[:3], ["goal", "plan", "active_step"]
        )
        self.assertEqual(workflow_state["goal"]["goal_id"], str(GOAL_ID))
        self.assertEqual(workflow_state["plan"]["plan_id"], str(PLAN_ID))
        self.assertEqual(workflow_state["active_step"]["step_id"], "PS-1")

    def test_project_inventory_defaults_to_an_empty_roster_when_omitted(self):
        client = FakeAgentClient()
        agent = _agent(client)

        agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        payload = json.loads(client.responses.requests[0]["input"][0]["content"])
        workflow_state = payload["workflow_state"]
        self.assertEqual(
            list(workflow_state.keys())[:3], ["goal", "plan", "active_step"]
        )
        self.assertEqual(
            workflow_state["project_inventory"],
            {
                "current_part": {
                    "part_id": "",
                    "part_name": "",
                    "features": [],
                    "parameters": [],
                },
                "other_parts": [],
            },
        )

    def test_project_inventory_round_trips_unchanged_when_supplied(self):
        client = FakeAgentClient()
        agent = _agent(client)
        inventory = {
            "current_part": {
                "part_id": "part-1",
                "part_name": "Soap Holder",
                "features": [
                    {"semantic_id": "holder_floor", "function_name": "build_holder_floor", "role": "supporting floor"}
                ],
            },
            "other_parts": [
                {"part_id": "part-2", "part_name": "Bracket", "features": []}
            ],
        }

        agent.start_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            project_inventory=inventory,
        )

        payload = json.loads(client.responses.requests[0]["input"][0]["content"])
        self.assertEqual(payload["workflow_state"]["project_inventory"], inventory)

    def test_recent_messages_are_embedded_in_order(self):
        # Agent3D trusts the caller's bounding (the orchestrator owns that, see
        # `_recent_messages` in orchestrator.py) and only preserves order.
        client = FakeAgentClient()
        agent = _agent(client)
        messages = [
            {"role": "user", "content": f"message {index}"} for index in range(5)
        ]

        agent.start_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            recent_messages=messages,
        )

        payload = json.loads(client.responses.requests[0]["input"][0]["content"])
        self.assertEqual(
            [item["content"] for item in payload["recent_conversation"]],
            [f"message {index}" for index in range(5)],
        )

    def test_fewer_than_four_messages_are_passed_through_unpadded(self):
        client = FakeAgentClient()
        agent = _agent(client)
        messages = [{"role": "user", "content": "hello"}]

        agent.start_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            recent_messages=messages,
        )

        payload = json.loads(client.responses.requests[0]["input"][0]["content"])
        self.assertEqual(payload["recent_conversation"], messages)

    def test_observations_are_passed_through_with_no_carryover_between_chains(self):
        client = FakeAgentClient()
        agent = _agent(client)
        first_observations = [{"tool_id": "index_search", "arguments": {}, "result": {}}]

        agent.start_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            observations=first_observations,
        )
        agent.start_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            observations=[],
        )

        first_payload = json.loads(client.responses.requests[0]["input"][0]["content"])
        second_payload = json.loads(client.responses.requests[1]["input"][0]["content"])
        self.assertEqual(first_payload["step_observations"], first_observations)
        self.assertEqual(second_payload["step_observations"], [])

    def test_cad_system_prompt_is_included_in_instructions(self):
        client = FakeAgentClient()
        agent = _agent(client)

        agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        instructions = client.responses.requests[0]["instructions"]
        self.assertIn("CadQuery Source Style Contract", instructions)
        self.assertIn("Forbidden source behavior", instructions)

    def test_injected_tool_catalog_is_passed_to_the_model_client(self):
        client = FakeAgentClient()
        catalog = [
            {"type": "function", "name": "index_search"},
            {"type": "function", "name": "index_get_feature"},
        ]
        agent = _agent(client, tool_catalog=catalog)

        agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        self.assertEqual(client.responses.requests[0]["tools"], catalog)

    def test_start_step_sends_no_previous_response_id(self):
        client = FakeAgentClient()
        agent = _agent(client)

        agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        self.assertNotIn("previous_response_id", client.responses.requests[0])

    def test_reasoning_omits_context_for_a_model_outside_the_gpt_5_6_family(self):
        client = FakeAgentClient()
        agent = _agent(client, model="gpt-5.4-mini")

        agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        self.assertEqual(client.responses.requests[0]["reasoning"], {"effort": "medium"})

    def test_reasoning_includes_context_all_turns_for_a_gpt_5_6_model(self):
        client = FakeAgentClient()
        agent = _agent(client, model="gpt-5.6")

        agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        self.assertEqual(
            client.responses.requests[0]["reasoning"],
            {"effort": "medium", "context": "all_turns"},
        )

    def test_start_step_makes_exactly_one_model_call_and_returns_it_unchanged(self):
        response = SimpleNamespace(id="decision-1", output=[])
        client = FakeAgentClient(response)
        agent = _agent(client)

        result = agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        self.assertEqual(len(client.responses.requests), 1)
        self.assertIs(result, response)

    def test_start_step_does_not_mutate_goal_or_plan(self):
        client = FakeAgentClient()
        agent = _agent(client)
        the_goal, the_plan = goal(), plan()
        goal_before = the_goal.model_dump(mode="json")
        plan_before = the_plan.model_dump(mode="json")

        agent.start_step(
            goal=the_goal,
            plan=the_plan,
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
        )

        self.assertEqual(the_goal.model_dump(mode="json"), goal_before)
        self.assertEqual(the_plan.model_dump(mode="json"), plan_before)

    def test_start_step_traces_the_exact_request_sent_to_the_model_before_calling_it(self):
        client = FakeAgentClient()
        writer = FakeTraceWriter()
        agent = _agent(client, trace_writer=writer)
        observations = [{"tool_id": "index_search", "arguments": {}, "result": {}}]
        messages = [{"role": "user", "content": "make it wider"}]

        agent.start_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            recent_messages=messages,
            observations=observations,
        )

        # llm.request is logged before the model is ever called.
        self.assertEqual(len(client.responses.requests), 1)
        request_event = writer.calls[0]
        self.assertEqual(request_event["event"], "llm.request")
        self.assertEqual(request_event["edit_job_id"], EDIT_JOB_ID)
        self.assertEqual(request_event["goal_id"], str(GOAL_ID))
        self.assertEqual(request_event["plan_id"], str(PLAN_ID))
        self.assertEqual(request_event["step_id"], "PS-1")
        self.assertEqual(request_event["agent_turn"], TRACE_CONTEXT.agent_turn)
        self.assertEqual(request_event["step_attempt"], TRACE_CONTEXT.step_attempt)
        self.assertEqual(request_event["reasoning_round"], TRACE_CONTEXT.reasoning_round)
        self.assertIsNone(request_event["previous_response_id"])

        # The logged request IS the exact payload the client received -- not
        # a separately reconstructed approximation.
        logged_request = request_event["request"]
        sent_request = client.responses.requests[0]
        self.assertEqual(logged_request, sent_request)
        self.assertEqual(logged_request["instructions"], agent.instructions)
        self.assertEqual(logged_request["tools"], list(agent.tool_catalog))
        self.assertEqual(logged_request["model"], agent.model)
        self.assertEqual(logged_request["tool_choice"], "required")
        self.assertEqual(logged_request["parallel_tool_calls"], True)
        self.assertEqual(logged_request["reasoning"], {"effort": "medium"})
        self.assertNotIn("previous_response_id", logged_request)

        self.assertIsInstance(request_event["instructions_sha256"], str)
        self.assertIsInstance(request_event["tool_catalog_sha256"], str)

    def test_start_step_fails_clearly_when_the_request_trace_cannot_be_written(self):
        from workers.agent_3d.failures import WorkflowFailure

        class BrokenTraceWriter:
            def log(self, _event, *, edit_job_id, **_fields):
                raise OSError("disk full")

        client = FakeAgentClient()
        agent = _agent(client, trace_writer=BrokenTraceWriter())

        with self.assertRaises(WorkflowFailure) as raised:
            agent.start_step(
                goal=goal(),
                plan=plan(),
                active_step=active_step(),
                trace_context=TRACE_CONTEXT,
            )

        self.assertEqual(raised.exception.code, "AGENT_PROMPT_LOG_WRITE_FAILED")
        self.assertEqual(len(client.responses.requests), 0)

    def test_start_step_traces_the_raw_response_after_the_model_call(self):
        response = SimpleNamespace(
            id="decision-1",
            status="completed",
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="index_search",
                    call_id="call-1",
                    arguments="{}",
                )
            ],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                output_tokens_details=SimpleNamespace(reasoning_tokens=8),
            ),
        )
        client = FakeAgentClient(response)
        writer = FakeTraceWriter()
        agent = _agent(client, trace_writer=writer)

        result = agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        self.assertIs(result, response)
        self.assertEqual([call["event"] for call in writer.calls], ["llm.request", "llm.response"])
        response_event = writer.calls[1]
        self.assertEqual(response_event["response_id"], "decision-1")
        self.assertEqual(response_event["status"], "completed")
        self.assertEqual(response_event["input_tokens"], 100)
        self.assertEqual(response_event["output_tokens"], 20)
        self.assertEqual(response_event["reasoning_tokens"], 8)
        # The raw response is captured in full, not reduced to a tool name.
        # (FakeTraceWriter records the object as-is; AgentTraceWriter's own
        # JSON-safe serialization is covered in test_agent_trace.py.)
        self.assertIs(response_event["response"], response)

    def test_response_trace_write_failure_does_not_fail_an_otherwise_successful_start_step_call(
        self,
    ):
        class RequestOnlyTraceWriter:
            def __init__(self):
                self.calls: list[dict] = []

            def log(self, event, *, edit_job_id, **fields):
                self.calls.append({"event": event, "edit_job_id": edit_job_id, **fields})
                if event == "llm.response":
                    raise OSError("disk full")
                return "/x"

        response = SimpleNamespace(id="decision-1", output=[])
        client = FakeAgentClient(response)
        writer = RequestOnlyTraceWriter()
        agent = _agent(client, trace_writer=writer)

        result = agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        # The model already answered -- a lost debug event must not discard that.
        self.assertIs(result, response)
        self.assertEqual([call["event"] for call in writer.calls], ["llm.request", "llm.response"])

    def test_start_step_raises_agent_decision_failed_when_the_model_call_raises(self):
        from workers.agent_3d.failures import WorkflowFailure

        client = FakeAgentClient(error=RuntimeError("boom"))
        agent = _agent(client)

        with self.assertRaises(WorkflowFailure) as raised:
            agent.start_step(
                goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
            )

        self.assertEqual(raised.exception.code, "AGENT_DECISION_FAILED")


class Agent3DContinueStepTests(unittest.TestCase):
    def test_continue_step_sends_only_function_call_output_items_as_input(self):
        client = FakeAgentClient()
        agent = _agent(client)
        tool_outputs = [
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": json.dumps({"ok": True, "data": {"status": "created"}}),
            },
        ]

        agent.continue_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            previous_response_id="decision-1",
            tool_outputs=tool_outputs,
        )

        self.assertEqual(client.responses.requests[0]["input"], tool_outputs)

    def test_continue_step_sends_the_supplied_previous_response_id(self):
        client = FakeAgentClient()
        agent = _agent(client)

        agent.continue_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            previous_response_id="decision-7",
            tool_outputs=[],
        )

        self.assertEqual(client.responses.requests[0]["previous_response_id"], "decision-7")

    def test_continue_step_resends_instructions_tools_tool_choice_parallel_tool_calls_and_reasoning(
        self,
    ):
        client = FakeAgentClient()
        catalog = [{"type": "function", "name": "index_search"}]
        agent = _agent(client, tool_catalog=catalog)

        agent.continue_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            previous_response_id="decision-1",
            tool_outputs=[],
        )

        request = client.responses.requests[0]
        self.assertEqual(request["instructions"], agent.instructions)
        self.assertEqual(request["tools"], catalog)
        self.assertEqual(request["tool_choice"], "required")
        self.assertEqual(request["parallel_tool_calls"], True)
        self.assertEqual(request["reasoning"], {"effort": "medium"})

    def test_continue_step_traces_previous_response_id_step_attempt_and_reasoning_round(self):
        client = FakeAgentClient()
        writer = FakeTraceWriter()
        agent = _agent(client, trace_writer=writer)
        trace_context = AgentTraceContext(
            edit_job_id=EDIT_JOB_ID, agent_turn=4, step_attempt=1, reasoning_round=2
        )

        agent.continue_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=trace_context,
            previous_response_id="decision-1",
            tool_outputs=[],
        )

        request_event = writer.calls[0]
        self.assertEqual(request_event["previous_response_id"], "decision-1")
        self.assertEqual(request_event["step_attempt"], 1)
        self.assertEqual(request_event["reasoning_round"], 2)
        self.assertEqual(request_event["agent_turn"], 4)

    def test_continue_step_makes_exactly_one_model_call_and_returns_it_unchanged(self):
        response = SimpleNamespace(id="decision-2", output=[])
        client = FakeAgentClient(response)
        agent = _agent(client)

        result = agent.continue_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            previous_response_id="decision-1",
            tool_outputs=[],
        )

        self.assertEqual(len(client.responses.requests), 1)
        self.assertIs(result, response)

    def test_continue_step_does_not_mutate_goal_or_plan(self):
        client = FakeAgentClient()
        agent = _agent(client)
        the_goal, the_plan = goal(), plan()
        goal_before = the_goal.model_dump(mode="json")
        plan_before = the_plan.model_dump(mode="json")

        agent.continue_step(
            goal=the_goal,
            plan=the_plan,
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            previous_response_id="decision-1",
            tool_outputs=[],
        )

        self.assertEqual(the_goal.model_dump(mode="json"), goal_before)
        self.assertEqual(the_plan.model_dump(mode="json"), plan_before)

    def test_continue_step_fails_clearly_when_the_request_trace_cannot_be_written(self):
        from workers.agent_3d.failures import WorkflowFailure

        class BrokenTraceWriter:
            def log(self, _event, *, edit_job_id, **_fields):
                raise OSError("disk full")

        client = FakeAgentClient()
        agent = _agent(client, trace_writer=BrokenTraceWriter())

        with self.assertRaises(WorkflowFailure) as raised:
            agent.continue_step(
                goal=goal(),
                plan=plan(),
                active_step=active_step(),
                trace_context=TRACE_CONTEXT,
                previous_response_id="decision-1",
                tool_outputs=[],
            )

        self.assertEqual(raised.exception.code, "AGENT_PROMPT_LOG_WRITE_FAILED")
        self.assertEqual(len(client.responses.requests), 0)

    def test_continue_step_passes_through_a_failed_tool_result_verbatim(self):
        client = FakeAgentClient()
        agent = _agent(client)
        failed_output = [
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "TOOL_NOT_FOUND",
                            "message": "The requested tool is unavailable.",
                            "retryable": False,
                            "details": {},
                        },
                    }
                ),
            }
        ]

        agent.continue_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            previous_response_id="decision-1",
            tool_outputs=failed_output,
        )

        # Agent3D does not interpret ok/error -- it forwards whatever it's given.
        self.assertEqual(client.responses.requests[0]["input"], failed_output)

    def test_continue_step_raises_agent_decision_failed_when_the_model_call_raises(self):
        from workers.agent_3d.failures import WorkflowFailure

        client = FakeAgentClient(error=RuntimeError("boom"))
        agent = _agent(client)

        with self.assertRaises(WorkflowFailure) as raised:
            agent.continue_step(
                goal=goal(),
                plan=plan(),
                active_step=active_step(),
                trace_context=TRACE_CONTEXT,
                previous_response_id="decision-1",
                tool_outputs=[],
            )

        self.assertEqual(raised.exception.code, "AGENT_DECISION_FAILED")

    def test_a_start_step_then_continue_step_sequence_shares_the_same_instructions_and_tools_and_chains_on_response_id(
        self,
    ):
        first_response = SimpleNamespace(id="decision-1", output=[])
        second_response = SimpleNamespace(id="decision-2", output=[])
        client = FakeAgentClient(responses=[first_response, second_response])
        agent = _agent(client)

        start_result = agent.start_step(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )
        agent.continue_step(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            previous_response_id=start_result.id,
            tool_outputs=[
                {"type": "function_call_output", "call_id": "call-1", "output": "{}"}
            ],
        )

        first_request, second_request = client.responses.requests
        self.assertNotIn("previous_response_id", first_request)
        self.assertEqual(second_request["previous_response_id"], "decision-1")
        self.assertEqual(second_request["instructions"], first_request["instructions"])
        self.assertEqual(second_request["tools"], first_request["tools"])
        self.assertEqual(second_request["tool_choice"], first_request["tool_choice"])
        self.assertEqual(
            second_request["parallel_tool_calls"], first_request["parallel_tool_calls"]
        )
        self.assertEqual(second_request["reasoning"], first_request["reasoning"])


if __name__ == "__main__":
    unittest.main()
