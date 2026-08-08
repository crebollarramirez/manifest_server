from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.planning.agent_contracts import (
    CadGoal,
    CadGoalDefinition,
)
from workers.agent_3d.planning.goal_creator import GoalCreator
from workers.agent_3d.planning.planning_agent import (
    MAX_TOOL_ROUNDS,
    PlanningAgent,
)
from workers.agent_3d.planning.prompt_loader import (
    load_goal_creation_prompt,
    load_planning_prompt,
    load_service_prompt,
)
from workers.agent_3d.tools import (
    IndexGetFeatureTool,
    IndexSearchTool,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
    ToolServices,
    Toolbox,
)


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PART_ID = "22222222-2222-4222-8222-222222222222"
GOAL_ID = "33333333-3333-4333-8333-333333333333"
PLAN_ID = "44444444-4444-4444-8444-444444444444"


SEMANTIC_INDEX = {
    "schema_version": 1,
    "project_id": PROJECT_ID,
    "parts": [
        {
            "part_id": PART_ID,
            "part_name": "Soap Holder",
            "model_params": [
                {
                    "name": "drainage_hole_diameter_mm",
                    "type_name": "float",
                    "default_source": "4.0",
                    "line_start": 8,
                    "line_end": 8,
                }
            ],
            "cad_parts": [
                {
                    "semantic_id": "holder_floor",
                    "role": "supporting floor",
                    "parameters": [],
                    "depends_on": [],
                    "search_keys": ["holder floor", "soap holder base"],
                    "function_name": "build_holder_floor",
                },
                {
                    "semantic_id": "drainage_holes",
                    "role": "drainage openings",
                    "parameters": ["drainage_hole_diameter_mm"],
                    "depends_on": ["holder_floor"],
                    "search_keys": ["drainage holes", "drainage openings"],
                    "function_name": "cut_drainage_holes",
                },
            ],
        }
    ],
}


def response(
    response_id: str,
    *,
    output: list[object] | None = None,
    output_parsed: object = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=response_id,
        output=output or [],
        output_parsed=output_parsed,
    )


class FakeResponses:
    def __init__(self, values: list[SimpleNamespace]):
        self.values = list(values)
        self.requests: list[dict] = []

    def parse(self, **request):
        self.requests.append(request)
        if not self.values:
            raise AssertionError("Unexpected OpenAI request")
        return self.values.pop(0)


class FakeClient:
    def __init__(self, values: list[SimpleNamespace]):
        self.responses = FakeResponses(values)


class IndexRepository:
    def read_text(self, _path: str) -> str:
        return json.dumps(SEMANTIC_INDEX)


def planning_runtime(
    client: FakeClient,
    *,
    allowed_tools: tuple[str, ...] = ("index_search", "index_get_feature"),
) -> tuple[PlanningAgent, ToolExecutionContext]:
    registry = ToolRegistry()
    registry.register_many([IndexSearchTool(), IndexGetFeatureTool()])
    agent = PlanningAgent(
        Toolbox(registry),
        ToolExecutor(registry),
        allowed_tools=allowed_tools,
        client=client,
    )
    tool_context = ToolExecutionContext(
        run_id=PLAN_ID,
        project_id=PROJECT_ID,
        part_id=PART_ID,
        candidate_id=None,
        services=ToolServices(repository=IndexRepository()),
    )
    return agent, tool_context


def goal() -> CadGoal:
    return CadGoal.model_validate(
        {
            "goal_id": GOAL_ID,
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
            "clarification": {
                "required": False,
                "question": None,
                "reason": None,
            },
        }
    )


def model_plan() -> dict:
    return {
        "plan_id": "model-supplied-plan-id",
        "goal_id": "model-supplied-goal-id",
        "version": 9,
        "summary": "Inspect the drainage feature, then plan the narrow change.",
        "target_bindings": [
            {
                "semantic_id": "drainage_holes",
                "relationship": "primary",
                "reason": "The retrieved feature produces the openings.",
            },
            {
                "semantic_id": "holder_floor",
                "relationship": "related",
                "reason": "The retrieved feature directly depends on the floor.",
            },
            {
                "semantic_id": "invented_feature",
                "relationship": "related",
                "reason": "This feature was not observed.",
            },
        ],
        "steps": [
            {
                "step_id": "PS-1",
                "sequence": 1,
                "objective": "Inspect how the opening size is controlled.",
                "depends_on": [],
                "addresses_criteria": [],
                "status": "pending",
            },
            {
                "step_id": "PS-2",
                "sequence": 2,
                "objective": "Increase the drainage-opening size.",
                "depends_on": ["PS-1"],
                "addresses_criteria": ["GC-1"],
                "status": "pending",
            },
        ],
    }


class PromptLoaderTests(unittest.TestCase):
    def test_loads_the_goal_creation_and_planning_prompts(self):
        goal_prompt = load_goal_creation_prompt()
        planning = load_planning_prompt()

        self.assertIn("goal-creation component", goal_prompt)
        self.assertNotIn('"goal_id"', goal_prompt)
        self.assertIn("available read-only semantic tools", planning)
        self.assertNotIn("replace_parameter_field", planning)
        self.assertIs(
            load_service_prompt("goal-creation"),
            load_service_prompt("goal-creation"),
        )

        repository_root = Path(__file__).resolve().parents[1]
        for name in ("goal-creation", "planning"):
            prompt_path = (
                repository_root / "workers/agent_3d/planning/prompts" / f"{name}.md"
            )
            self.assertTrue(prompt_path.is_file())
            self.assertTrue(prompt_path.read_text(encoding="utf-8").strip())


class GoalCreatorTests(unittest.TestCase):
    def test_goal_schema_avoids_unsupported_one_of(self):
        schema = CadGoalDefinition.model_json_schema()

        def contains_one_of(value: object) -> bool:
            if isinstance(value, dict):
                return "oneOf" in value or any(
                    contains_one_of(item) for item in value.values()
                )
            if isinstance(value, list):
                return any(contains_one_of(item) for item in value)
            return False

        self.assertFalse(contains_one_of(schema))

    def test_clarification_fields_remain_conditionally_validated(self):
        base = {
            "description": "Create a rectangular soup holder.",
            "completion_criteria": [
                {
                    "description": "The holder has a rectangular form.",
                    "type": "required",
                }
            ],
            "constraints": [],
            "assumptions": [],
        }

        with self.assertRaises(ValueError):
            CadGoalDefinition.model_validate(
                {
                    **base,
                    "clarification": {
                        "required": True,
                        "question": None,
                        "reason": None,
                    },
                }
            )
        with self.assertRaises(ValueError):
            CadGoalDefinition.model_validate(
                {
                    **base,
                    "clarification": {
                        "required": False,
                        "question": "What size should it be?",
                        "reason": "Dimensions were omitted.",
                    },
                }
            )

    def test_worker_adds_goal_and_criterion_identity_and_preserves_request(self):
        client = FakeClient(
            [
                response(
                    "goal-response",
                    output_parsed={
                        "description": "Make the plate wider while preserving hole spacing.",
                        "completion_criteria": [
                            {
                                "description": "The plate is 20 mm wider.",
                                "type": "required",
                            },
                            {
                                "description": "Hole spacing remains unchanged.",
                                "type": "preserve",
                            },
                        ],
                        "constraints": ["Hole spacing must not change."],
                        "assumptions": [],
                        "clarification": {
                            "required": False,
                            "question": None,
                            "reason": None,
                        },
                    },
                )
            ]
        )
        creator = GoalCreator(client=client, model="test-model")
        raw_request = "  Make the plate 20 mm wider; keep hole spacing.  "

        created = creator.create_goal(raw_request)

        self.assertEqual(created.raw_request, raw_request)
        self.assertEqual(
            [item.criterion_id for item in created.completion_criteria],
            ["GC-1", "GC-2"],
        )
        self.assertEqual(created.goal_id.version, 4)
        request = client.responses.requests[0]
        self.assertIs(request["text_format"].__name__, "CadGoalDefinition")
        self.assertEqual(
            json.loads(request["input"][0]["content"])["request"], raw_request
        )

    def test_refusal_is_preserved_as_a_workflow_failure(self):
        client = FakeClient(
            [
                response(
                    "goal-refusal",
                    output=[
                        SimpleNamespace(
                            content=[
                                SimpleNamespace(
                                    type="refusal", refusal="Cannot create this goal."
                                )
                            ]
                        )
                    ],
                )
            ]
        )
        with self.assertRaises(WorkflowFailure) as raised:
            GoalCreator(client=client).create_goal("Create a bracket.")
        self.assertEqual(raised.exception.code, "AI_REFUSAL")
        self.assertEqual(str(raised.exception), "Cannot create this goal.")


class PlanningAgentTests(unittest.TestCase):
    def test_uses_only_read_tools_and_filters_unobserved_bindings(self):
        client = FakeClient(
            [
                response(
                    "response-1",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            name="index_search",
                            arguments=json.dumps(
                                {"query": "drainage holes", "limit": 5}
                            ),
                            call_id="call-1",
                        )
                    ],
                ),
                response(
                    "response-2",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            name="index_get_feature",
                            arguments=json.dumps(
                                {"semantic_id": "drainage_holes"}
                            ),
                            call_id="call-2",
                        )
                    ],
                ),
                response("response-3", output_parsed=model_plan()),
            ]
        )
        agent, tool_context = planning_runtime(client)
        planned = agent.create_plan(
            goal=goal(),
            plan_id=PLAN_ID,
            tool_context=tool_context,
            version=1,
        )

        self.assertEqual(
            [tool["name"] for tool in client.responses.requests[0]["tools"]],
            ["index_search", "index_get_feature"],
        )
        self.assertEqual(
            [binding.semantic_id for binding in planned.target_bindings],
            ["drainage_holes", "holder_floor"],
        )
        self.assertEqual(planned.plan_id, "model-supplied-plan-id")
        self.assertEqual(planned.goal_id, "model-supplied-goal-id")
        self.assertEqual(planned.version, 9)
        second_input = client.responses.requests[1]["input"][0]
        self.assertEqual(second_input["type"], "function_call_output")
        first_result = json.loads(second_input["output"])
        self.assertTrue(first_result["ok"])
        self.assertEqual(first_result["data"]["status"], "ok")

    def test_rejects_an_unselected_exact_tool_name_without_translation(self):
        client = FakeClient(
            [
                response(
                    "response-1",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            name="index_get_feature",
                            arguments=json.dumps(
                                {"semantic_id": "drainage_holes"}
                            ),
                            call_id="call-1",
                        )
                    ],
                ),
                response("response-2", output_parsed=model_plan()),
            ]
        )
        agent, tool_context = planning_runtime(client, allowed_tools=("index_search",))

        agent.create_plan(
            goal=goal(),
            plan_id=PLAN_ID,
            tool_context=tool_context,
            version=1,
        )

        self.assertEqual(
            [tool["name"] for tool in client.responses.requests[0]["tools"]],
            ["index_search"],
        )
        tool_output = json.loads(
            client.responses.requests[1]["input"][0]["output"]
        )
        self.assertFalse(tool_output["ok"])
        self.assertEqual(tool_output["error"]["code"], "TOOL_NOT_FOUND")

    def test_enforces_the_eight_round_read_tool_limit(self):
        values = [
            response(
                f"response-{index}",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="index_search",
                        arguments=json.dumps({"query": "body", "limit": 1}),
                        call_id=f"call-{index}",
                    )
                ],
            )
            for index in range(MAX_TOOL_ROUNDS + 1)
        ]
        client = FakeClient(values)
        agent, tool_context = planning_runtime(client)
        with self.assertRaises(WorkflowFailure) as raised:
            agent.create_plan(
                goal=goal(),
                plan_id=PLAN_ID,
                tool_context=tool_context,
            )
        self.assertEqual(raised.exception.code, "PLANNING_TOOL_LIMIT")
        self.assertEqual(len(client.responses.requests), MAX_TOOL_ROUNDS + 1)


if __name__ == "__main__":
    unittest.main()
