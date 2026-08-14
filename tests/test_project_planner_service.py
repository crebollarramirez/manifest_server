from __future__ import annotations

import unittest
from unittest import mock

from workers.project_planner.project_planner.contracts import (
    PlanViolation,
    ProjectPlanDraft,
    ProjectPlanningLimits,
)
from workers.project_planner.project_planner.failures import ProjectPlanningFailure
from workers.project_planner.project_planner.service import (
    process_project_planning_job,
)


class FakeProjectsRepository:
    def list_cad_sources(self, project_id: str):
        return []  # brand-new/empty project -- existing_parts resolves to []


class FakeRepository:
    def __init__(self):
        self.projects = FakeProjectsRepository()


class FakePlanner:
    def __init__(self, drafts: list[ProjectPlanDraft]):
        self._drafts = list(drafts)
        self.repair_calls: list[dict] = []

    def create_plan_draft(self, planning_input):
        return self._drafts.pop(0)

    def repair_plan_draft(self, planning_input, *, previous_draft, violations):
        self.repair_calls.append(
            {"previous_draft": previous_draft, "violations": violations}
        )
        return self._drafts.pop(0)


def _job() -> dict:
    return {
        "project_id": "11111111-1111-4111-8111-111111111111",
        "request_text": "an adjustable phone stand with a rotating arm",
    }


def _limits(max_parts: int = 12, max_interfaces: int = 24) -> ProjectPlanningLimits:
    return ProjectPlanningLimits(max_parts=max_parts, max_interfaces=max_interfaces)


def _draft_payload(**overrides) -> dict:
    payload = {
        "summary": "A two-part adjustable phone stand.",
        "design_mode": "multi_part",
        "requirements": [
            {"ref": "stable_support", "description": "The stand must remain stable."},
        ],
        "parts": [
            {
                "ref": "base",
                "name": "phone_stand_base",
                "kind": "new",
                "existing_part_id": None,
                "purpose": "Provide a stable desktop foundation.",
                "responsibilities": ["Provide stable footprint"],
                "addresses_requirements": ["stable_support"],
            },
            {
                "ref": "arm",
                "name": "adjustable_arm",
                "kind": "new",
                "existing_part_id": None,
                "purpose": "Raise the phone holder.",
                "responsibilities": ["Connect to the base"],
                "addresses_requirements": [],
            },
        ],
        "interfaces": [],
        "execution_dependencies": [],
        "assumptions": [],
        "clarification": {"required": False, "question": None, "reason": None},
    }
    payload.update(overrides)
    return payload


def _self_interfacing_draft() -> ProjectPlanDraft:
    payload = _draft_payload(
        interfaces=[
            {
                "ref": "base_arm",
                "interface_type": "rotational",
                "endpoint_a": {"part_ref": "base", "role": "socket"},
                "endpoint_b": {"part_ref": "base", "role": "shaft"},
                "purpose": "Bogus self-referencing interface.",
                "parameters": [],
                "requirements": [],
                "addresses_requirements": [],
            }
        ]
    )
    return ProjectPlanDraft.model_validate(payload)


def _valid_draft() -> ProjectPlanDraft:
    return ProjectPlanDraft.model_validate(_draft_payload())


def _clarifying_draft() -> ProjectPlanDraft:
    payload = _draft_payload(
        parts=[],
        requirements=[],
        clarification={
            "required": True,
            "question": "Should the arm be removable?",
            "reason": "This changes the interface type.",
        },
    )
    return ProjectPlanDraft.model_validate(payload)


class ProcessProjectPlanningJobTests(unittest.TestCase):
    def test_repairs_invalid_draft_then_succeeds(self):
        planner = FakePlanner([_self_interfacing_draft(), _valid_draft()])

        result = process_project_planning_job(
            FakeRepository(), planner, _job(), limits=_limits()
        )

        self.assertTrue(result["project_plan"]["plan_id"])
        self.assertEqual(len(planner.repair_calls), 1)
        repair_call = planner.repair_calls[0]
        self.assertEqual(
            repair_call["previous_draft"].model_dump(mode="json"),
            _self_interfacing_draft().model_dump(mode="json"),
        )
        self.assertEqual(
            [v.code for v in repair_call["violations"]],
            ["PROJECT_PLAN_INVALID_INTERFACE"],
        )
        self.assertEqual(
            repair_call["violations"][0].details["condition"], "self_reference"
        )

    def test_never_converges_fails_after_max_repair_attempts_plus_one_total_tries(self):
        planner = FakePlanner(
            [_self_interfacing_draft(), _self_interfacing_draft(), _self_interfacing_draft()]
        )

        with self.assertRaises(ProjectPlanningFailure) as ctx:
            process_project_planning_job(
                FakeRepository(), planner, _job(), limits=_limits(), max_repair_attempts=2
            )

        self.assertEqual(ctx.exception.code, "PROJECT_PLAN_INVALID")
        self.assertEqual(len(planner.repair_calls), 2)
        self.assertEqual(len(ctx.exception.details["attempts"]), 3)
        self.assertEqual(ctx.exception.details["attempts"][-1]["attempt"], 2)
        self.assertIn("violations", ctx.exception.details)

    def test_clarification_required_short_circuits_without_repair(self):
        planner = FakePlanner([_clarifying_draft()])

        with self.assertRaises(ProjectPlanningFailure) as ctx:
            process_project_planning_job(
                FakeRepository(), planner, _job(), limits=_limits()
            )

        self.assertEqual(ctx.exception.code, "PROJECT_CLARIFICATION_REQUIRED")
        self.assertEqual(len(planner.repair_calls), 0)
        self.assertEqual(
            ctx.exception.details["question"], "Should the arm be removable?"
        )
        self.assertTrue(ctx.exception.details["plan"]["clarification"]["required"])

    def test_non_retryable_violation_skips_repair_and_fails_immediately(self):
        planner = FakePlanner([_valid_draft()])
        synthetic_violation = PlanViolation(
            code="PROJECT_PLAN_UNKNOWN_PART",
            message="synthetic non-retryable violation",
            details={},
            retryable=False,
        )

        with mock.patch(
            "workers.project_planner.project_planner.service.validate_project_plan",
            return_value=[synthetic_violation],
        ):
            with self.assertRaises(ProjectPlanningFailure) as ctx:
                process_project_planning_job(
                    FakeRepository(), planner, _job(), limits=_limits()
                )

        self.assertEqual(ctx.exception.code, "PROJECT_PLAN_INVALID")
        self.assertEqual(len(planner.repair_calls), 0)
        self.assertEqual(len(ctx.exception.details["attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
