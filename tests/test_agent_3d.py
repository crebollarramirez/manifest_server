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

TRACE_CONTEXT = AgentTraceContext(edit_job_id=EDIT_JOB_ID, agent_turn=3, step_turn=2)


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
    def __init__(self, response: object):
        self.response = response
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(request)
        return self.response


class FakeAgentClient:
    def __init__(self, response: object | None = None):
        self.responses = FakeAgentResponses(
            response if response is not None else SimpleNamespace(id="r1", output=[])
        )


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


class Agent3DDecideTests(unittest.TestCase):
    def _agent(self, client: FakeAgentClient, **overrides) -> Agent3D:
        catalog = overrides.pop(
            "tool_catalog", [{"type": "function", "name": "index_search"}]
        )
        overrides.setdefault("trace_writer", FakeTraceWriter())
        return Agent3D(model_client=client, tool_catalog=catalog, **overrides)

    def test_goal_plan_and_active_step_appear_in_priority_order(self):
        client = FakeAgentClient()
        agent = self._agent(client)

        agent.decide(
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

    def test_recent_messages_are_embedded_in_order(self):
        # Agent3D trusts the caller's bounding (the orchestrator owns that, see
        # `_recent_messages` in orchestrator.py) and only preserves order.
        client = FakeAgentClient()
        agent = self._agent(client)
        messages = [
            {"role": "user", "content": f"message {index}"} for index in range(5)
        ]

        agent.decide(
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
        agent = self._agent(client)
        messages = [{"role": "user", "content": "hello"}]

        agent.decide(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            recent_messages=messages,
        )

        payload = json.loads(client.responses.requests[0]["input"][0]["content"])
        self.assertEqual(payload["recent_conversation"], messages)

    def test_observations_are_passed_through_with_no_carryover_between_calls(self):
        client = FakeAgentClient()
        agent = self._agent(client)
        first_observations = [{"tool_id": "index_search", "arguments": {}, "result": {}}]

        agent.decide(
            goal=goal(),
            plan=plan(),
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
            observations=first_observations,
        )
        agent.decide(
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
        agent = self._agent(client)

        agent.decide(
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
        agent = self._agent(client, tool_catalog=catalog)

        agent.decide(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        self.assertEqual(client.responses.requests[0]["tools"], catalog)

    def test_tool_ids_are_not_hardcoded_in_the_static_reasoning_prompt(self):
        static_prompt = load_agent_reasoning_prompt()

        for tool_id in ("index_search", "index_get_feature"):
            self.assertNotIn(tool_id, static_prompt)

    def test_decide_makes_exactly_one_model_call_and_returns_it_unchanged(self):
        response = SimpleNamespace(id="decision-1", output=[])
        client = FakeAgentClient(response)
        agent = self._agent(client)

        result = agent.decide(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        self.assertEqual(len(client.responses.requests), 1)
        self.assertIs(result, response)

    def test_decide_does_not_mutate_goal_or_plan(self):
        client = FakeAgentClient()
        agent = self._agent(client)
        the_goal, the_plan = goal(), plan()
        goal_before = the_goal.model_dump(mode="json")
        plan_before = the_plan.model_dump(mode="json")

        agent.decide(
            goal=the_goal,
            plan=the_plan,
            active_step=active_step(),
            trace_context=TRACE_CONTEXT,
        )

        self.assertEqual(the_goal.model_dump(mode="json"), goal_before)
        self.assertEqual(the_plan.model_dump(mode="json"), plan_before)

    def test_decide_traces_the_exact_request_sent_to_the_model_before_calling_it(self):
        client = FakeAgentClient()
        writer = FakeTraceWriter()
        agent = self._agent(client, trace_writer=writer)
        observations = [{"tool_id": "index_search", "arguments": {}, "result": {}}]
        messages = [{"role": "user", "content": "make it wider"}]

        agent.decide(
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
        self.assertEqual(request_event["step_turn"], TRACE_CONTEXT.step_turn)

        # The logged request IS the exact payload the client received -- not
        # a separately reconstructed approximation.
        logged_request = request_event["request"]
        sent_request = client.responses.requests[0]
        self.assertEqual(logged_request, sent_request)
        self.assertEqual(logged_request["instructions"], agent.instructions)
        self.assertEqual(logged_request["tools"], list(agent.tool_catalog))
        self.assertEqual(logged_request["model"], agent.model)
        self.assertEqual(logged_request["tool_choice"], "required")
        self.assertEqual(logged_request["parallel_tool_calls"], False)
        self.assertEqual(logged_request["reasoning"], {"effort": "medium"})

        self.assertIsInstance(request_event["instructions_sha256"], str)
        self.assertIsInstance(request_event["tool_catalog_sha256"], str)

    def test_decide_fails_clearly_when_the_request_trace_cannot_be_written(self):
        from workers.agent_3d.failures import WorkflowFailure

        class BrokenTraceWriter:
            def log(self, _event, *, edit_job_id, **_fields):
                raise OSError("disk full")

        client = FakeAgentClient()
        agent = self._agent(client, trace_writer=BrokenTraceWriter())

        with self.assertRaises(WorkflowFailure) as raised:
            agent.decide(
                goal=goal(),
                plan=plan(),
                active_step=active_step(),
                trace_context=TRACE_CONTEXT,
            )

        self.assertEqual(raised.exception.code, "AGENT_PROMPT_LOG_WRITE_FAILED")
        self.assertEqual(len(client.responses.requests), 0)

    def test_decide_traces_the_raw_response_after_the_model_call(self):
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
        agent = self._agent(client, trace_writer=writer)

        result = agent.decide(
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

    def test_response_trace_write_failure_does_not_fail_an_otherwise_successful_turn(self):
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
        agent = self._agent(client, trace_writer=writer)

        result = agent.decide(
            goal=goal(), plan=plan(), active_step=active_step(), trace_context=TRACE_CONTEXT
        )

        # The model already answered -- a lost debug event must not discard that.
        self.assertIs(result, response)
        self.assertEqual([call["event"] for call in writer.calls], ["llm.request", "llm.response"])


if __name__ == "__main__":
    unittest.main()
