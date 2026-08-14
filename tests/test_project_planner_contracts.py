from __future__ import annotations

import unittest

from workers.project_planner.project_planner.contracts import (
    PROJECT_PLAN_SCHEMA_VERSION,
    PlanViolation,
    ProjectClarification,
    ProjectPlanDraft,
    finalize_plan_draft,
)


def _clarification(required: bool = False, question=None, reason=None) -> dict:
    return {"required": required, "question": question, "reason": reason}


def _draft_payload(**overrides) -> dict:
    payload = {
        "summary": "Create a single flower pot with drainage holes.",
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
        "clarification": _clarification(),
    }
    payload.update(overrides)
    return payload


class ProjectPlanDraftTests(unittest.TestCase):
    def test_well_formed_draft_parses(self):
        draft = ProjectPlanDraft.model_validate(_draft_payload())
        self.assertEqual(draft.design_mode, "single_part")
        self.assertEqual(len(draft.parts), 1)

    def test_unknown_field_is_rejected(self):
        payload = _draft_payload()
        payload["unexpected_field"] = "nope"
        with self.assertRaises(ValueError):
            ProjectPlanDraft.model_validate(payload)

    def test_empty_parts_requires_clarification(self):
        payload = _draft_payload(parts=[])
        with self.assertRaises(ValueError):
            ProjectPlanDraft.model_validate(payload)

    def test_empty_parts_allowed_when_clarification_required(self):
        payload = _draft_payload(
            parts=[],
            requirements=[],
            clarification=_clarification(
                required=True,
                question="Should the lid be removable or permanently attached?",
                reason="This changes whether the lid is a separate part.",
            ),
        )
        draft = ProjectPlanDraft.model_validate(payload)
        self.assertTrue(draft.clarification.required)
        self.assertEqual(draft.parts, [])


class FinalizePlanDraftTests(unittest.TestCase):
    def test_injects_plan_id_and_schema_version_not_the_model(self):
        draft = ProjectPlanDraft.model_validate(_draft_payload())

        plan = finalize_plan_draft(draft)

        self.assertEqual(plan.schema_version, PROJECT_PLAN_SCHEMA_VERSION)
        self.assertTrue(str(plan.plan_id))

    def test_generates_unique_plan_ids(self):
        draft = ProjectPlanDraft.model_validate(_draft_payload())

        first = finalize_plan_draft(draft)
        second = finalize_plan_draft(draft)

        self.assertNotEqual(first.plan_id, second.plan_id)


class PlanViolationTests(unittest.TestCase):
    def test_well_formed_violation_parses_with_retryable_default(self):
        violation = PlanViolation(
            code="PROJECT_PLAN_TOO_COMPLEX",
            message="Too many parts.",
            details={"condition": "too_many_parts"},
        )
        self.assertTrue(violation.retryable)

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValueError):
            PlanViolation.model_validate(
                {
                    "code": "PROJECT_PLAN_TOO_COMPLEX",
                    "message": "Too many parts.",
                    "details": {},
                    "unexpected_field": "nope",
                }
            )

    def test_retryable_can_be_set_false(self):
        violation = PlanViolation(
            code="PROJECT_PLAN_TOO_COMPLEX",
            message="Too many parts.",
            details={},
            retryable=False,
        )
        self.assertFalse(violation.retryable)


class ProjectClarificationTests(unittest.TestCase):
    def test_required_without_question_or_reason_is_rejected(self):
        with self.assertRaises(ValueError):
            ProjectClarification.model_validate(_clarification(required=True))

    def test_required_with_question_and_reason_is_accepted(self):
        clarification = ProjectClarification.model_validate(
            _clarification(
                required=True,
                question="Should the arm be removable?",
                reason="This changes the interface type.",
            )
        )
        self.assertTrue(clarification.required)

    def test_not_required_with_question_is_rejected(self):
        with self.assertRaises(ValueError):
            ProjectClarification.model_validate(
                _clarification(required=False, question="Why though?")
            )

    def test_not_required_without_question_or_reason_is_accepted(self):
        clarification = ProjectClarification.model_validate(_clarification())
        self.assertFalse(clarification.required)


if __name__ == "__main__":
    unittest.main()
