from __future__ import annotations

import unittest
from pathlib import Path

from workers.project_planner.project_planner.planner import (
    _load_project_planning_prompt,
)


ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = (
    ROOT
    / "workers"
    / "project_planner"
    / "project_planner"
    / "prompts"
    / "project-planning.md"
)


class ProjectPlanningPromptTests(unittest.TestCase):
    def test_prompt_file_exists_and_is_non_empty(self):
        self.assertTrue(PROMPT_PATH.is_file())
        self.assertTrue(PROMPT_PATH.read_text(encoding="utf-8").strip())

    def test_loader_caches_the_same_instructions(self):
        self.assertIs(_load_project_planning_prompt(), _load_project_planning_prompt())

    def test_fit_critical_dimensions_are_never_left_to_chance(self):
        normalized = " ".join(_load_project_planning_prompt().split())
        self.assertIn(
            "the interface must record that shared dimension as a parameter",
            normalized,
        )
        self.assertIn(
            "If the user did not state it, infer one reasonable value from "
            "the context of the request",
            normalized,
        )
        self.assertIn("record it as a parameter anyway", normalized)
        self.assertIn(
            "add a matching entry to `assumptions` noting that the value "
            "was inferred rather than specified by the user",
            normalized,
        )

    def test_missing_dimension_is_not_grounds_for_clarification(self):
        normalized = " ".join(_load_project_planning_prompt().split())
        self.assertIn(
            "A missing fit-critical dimension is never, by itself, a reason to "
            "request clarification",
            normalized,
        )

    def test_repair_requests_return_a_complete_corrected_draft(self):
        normalized = " ".join(_load_project_planning_prompt().split())
        self.assertIn(
            'If the input includes a message whose JSON content has '
            '"repair_request": true, you are being asked to correct a '
            "previous draft, not create a new one.",
            normalized,
        )
        self.assertIn("Fix every listed violation.", normalized)
        self.assertIn(
            "Do not reintroduce any of the listed problems, and do not "
            "introduce new ones.",
            normalized,
        )

    def test_interfaces_must_connect_two_different_planned_parts(self):
        normalized = " ".join(_load_project_planning_prompt().split())
        self.assertIn(
            "Each interface connects exactly two *different* parts by their "
            "refs -- never the same part on both sides, and never a ref that "
            "is not one of the parts you defined in `parts`",
            normalized,
        )
        self.assertIn(
            "is not a part and must never be used as an interface endpoint",
            normalized,
        )
        self.assertIn(
            "address the relevant requirement directly on that part rather "
            "than inventing an interface for it",
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
