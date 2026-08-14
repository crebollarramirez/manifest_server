from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from workers.project_planner.project_planner.contracts import (
    PlanViolation,
    ProjectPlanDraft,
    ProjectPlanningInput,
    ProjectPlanningLimits,
)
from workers.project_planner.project_planner.failures import ProjectPlanningFailure
from workers.project_planner.project_planner.planner import ProjectPlanner


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


def _planning_input() -> ProjectPlanningInput:
    return ProjectPlanningInput(
        project_id="11111111-1111-4111-8111-111111111111",
        request_text="a flower pot with drainage holes",
        existing_parts=[],
        planning_limits=ProjectPlanningLimits(max_parts=12, max_interfaces=24),
    )


def _draft_payload() -> dict:
    return {
        "summary": "A single flower pot with drainage holes.",
        "design_mode": "single_part",
        "requirements": [
            {"ref": "drains_water", "description": "The pot must drain excess water."}
        ],
        "parts": [
            {
                "ref": "pot",
                "name": "flower_pot",
                "kind": "new",
                "existing_part_id": None,
                "purpose": "Hold soil and a plant while draining excess water.",
                "responsibilities": ["Provide a soil cavity", "Drain excess water"],
                "addresses_requirements": ["drains_water"],
            }
        ],
        "interfaces": [],
        "execution_dependencies": [],
        "assumptions": [],
        "clarification": {"required": False, "question": None, "reason": None},
    }


class ProjectPlannerReasoningTests(unittest.TestCase):
    def test_request_uses_medium_reasoning_effort(self):
        client = FakeClient([response("resp-1", output_parsed=_draft_payload())])
        planner = ProjectPlanner(client=client, model="test-model")

        planner.create_plan_draft(_planning_input())

        request = client.responses.requests[0]
        self.assertEqual(request["reasoning"], {"effort": "medium"})
        self.assertIs(request["text_format"].__name__, "ProjectPlanDraft")

    def test_repair_uses_medium_reasoning_effort_and_three_input_messages(self):
        client = FakeClient([response("resp-1", output_parsed=_draft_payload())])
        planner = ProjectPlanner(client=client, model="test-model")
        previous_draft = ProjectPlanDraft.model_validate(_draft_payload())
        violation = PlanViolation(
            code="PROJECT_PLAN_INVALID_INTERFACE",
            message='Interface "x" connects a part to itself.',
            details={"condition": "self_reference", "interface_ref": "x"},
        )

        planner.repair_plan_draft(
            _planning_input(), previous_draft=previous_draft, violations=[violation]
        )

        request = client.responses.requests[0]
        self.assertEqual(request["reasoning"], {"effort": "medium"})
        self.assertIs(request["text_format"].__name__, "ProjectPlanDraft")
        input_items = request["input"]
        self.assertEqual(len(input_items), 3)
        self.assertEqual(
            [item["role"] for item in input_items], ["user", "assistant", "user"]
        )
        repair_message = json.loads(input_items[2]["content"])
        self.assertTrue(repair_message["repair_request"])
        self.assertEqual(len(repair_message["violations"]), 1)
        self.assertEqual(
            repair_message["violations"][0]["code"], "PROJECT_PLAN_INVALID_INTERFACE"
        )

    def test_repair_refusal_is_raised_as_a_project_planning_failure(self):
        client = FakeClient(
            [
                response(
                    "resp-1",
                    output=[
                        {"content": [{"type": "refusal", "refusal": "cannot help with that"}]}
                    ],
                )
            ]
        )
        planner = ProjectPlanner(client=client, model="test-model")
        previous_draft = ProjectPlanDraft.model_validate(_draft_payload())
        violation = PlanViolation(
            code="PROJECT_PLAN_TOO_COMPLEX",
            message="Too many parts.",
            details={"condition": "too_many_parts"},
        )

        with self.assertRaises(ProjectPlanningFailure) as ctx:
            planner.repair_plan_draft(
                _planning_input(), previous_draft=previous_draft, violations=[violation]
            )
        self.assertEqual(ctx.exception.code, "AI_REFUSAL")

    def test_refusal_is_raised_as_a_project_planning_failure(self):
        client = FakeClient(
            [
                response(
                    "resp-1",
                    output=[
                        {"content": [{"type": "refusal", "refusal": "cannot help with that"}]}
                    ],
                )
            ]
        )
        planner = ProjectPlanner(client=client, model="test-model")

        with self.assertRaises(ProjectPlanningFailure) as ctx:
            planner.create_plan_draft(_planning_input())
        self.assertEqual(ctx.exception.code, "AI_REFUSAL")

    def test_missing_structured_output_is_a_response_invalid_failure(self):
        client = FakeClient([response("resp-1", output_parsed=None)])
        planner = ProjectPlanner(client=client, model="test-model")

        with self.assertRaises(ProjectPlanningFailure) as ctx:
            planner.create_plan_draft(_planning_input())
        self.assertEqual(ctx.exception.code, "PROJECT_PLAN_RESPONSE_INVALID")


if __name__ == "__main__":
    unittest.main()
