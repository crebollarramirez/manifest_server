from __future__ import annotations

import unittest
from uuid import uuid4

from workers.project_planner.project_planner.contracts import (
    ExecutionDependency,
    InterfaceEndpoint,
    PartInventoryItem,
    ProjectPlan,
    ProjectPlanningLimits,
    ProjectRequirement,
)
from workers.project_planner.project_planner.validator import validate_project_plan


def _plan(**overrides) -> ProjectPlan:
    payload = {
        "plan_id": str(uuid4()),
        "schema_version": 1,
        "summary": "Create a two-part adjustable phone stand.",
        "design_mode": "multi_part",
        "requirements": [
            {"ref": "stable_support", "description": "The stand must remain stable."},
            {"ref": "arm_rotation", "description": "The arm must rotate relative to the base."},
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
        "interfaces": [
            {
                "ref": "base_arm",
                "interface_type": "rotational",
                "endpoint_a": {"part_ref": "base", "role": "receiving rotational socket"},
                "endpoint_b": {"part_ref": "arm", "role": "lower rotating shaft"},
                "purpose": "Allow the arm to rotate relative to the base.",
                "parameters": [],
                "requirements": ["The connection must support rotation."],
                "addresses_requirements": ["arm_rotation"],
            }
        ],
        "execution_dependencies": [],
        "assumptions": [],
        "clarification": {"required": False, "question": None, "reason": None},
    }
    payload.update(overrides)
    return ProjectPlan.model_validate(payload)


def _limits(max_parts: int = 12, max_interfaces: int = 24) -> ProjectPlanningLimits:
    return ProjectPlanningLimits(max_parts=max_parts, max_interfaces=max_interfaces)


class ProjectPlanValidatorTests(unittest.TestCase):
    def test_well_formed_plan_passes_validation(self):
        self.assertEqual(
            validate_project_plan(_plan(), existing_parts=[], limits=_limits()), []
        )

    def test_duplicate_part_ref_rejected(self):
        plan = _plan()
        duplicated = plan.parts[1].model_copy(update={"ref": "base"})
        # Drop the interface (and the requirement it alone addressed) too
        # -- the interface targets "arm", which no longer exists once both
        # parts share the ref "base", and this test isolates the
        # duplicate-ref violation specifically.
        plan = plan.model_copy(
            update={
                "parts": [plan.parts[0], duplicated],
                "interfaces": [],
                "requirements": [plan.requirements[0]],
            }
        )

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_DUPLICATE_PART_REF")
        self.assertEqual(violations[0].details["duplicate_refs"], ["base"])
        self.assertTrue(violations[0].retryable)

    def test_existing_part_id_not_in_roster_rejected(self):
        plan = _plan()
        base = plan.parts[0].model_copy(
            update={"kind": "existing", "existing_part_id": "part-not-in-roster"}
        )
        plan = plan.model_copy(update={"parts": [base, plan.parts[1]]})

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_UNKNOWN_PART")
        self.assertEqual(violations[0].details["condition"], "unknown_existing_part_id")
        self.assertEqual(violations[0].details["valid_existing_part_ids"], [])

    def test_existing_kind_missing_existing_part_id_rejected(self):
        plan = _plan()
        base = plan.parts[0].model_copy(update={"kind": "existing", "existing_part_id": None})
        plan = plan.model_copy(update={"parts": [base, plan.parts[1]]})

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_UNKNOWN_PART")
        self.assertEqual(violations[0].details["condition"], "missing_existing_part_id")

    def test_new_kind_with_existing_part_id_rejected(self):
        plan = _plan()
        base = plan.parts[0].model_copy(update={"existing_part_id": "part-123"})
        plan = plan.model_copy(update={"parts": [base, plan.parts[1]]})

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_UNKNOWN_PART")
        self.assertEqual(
            violations[0].details["condition"], "unexpected_existing_part_id_for_new_part"
        )

    def test_existing_part_id_in_roster_is_accepted(self):
        plan = _plan()
        base = plan.parts[0].model_copy(
            update={"kind": "existing", "existing_part_id": "part-123"}
        )
        plan = plan.model_copy(update={"parts": [base, plan.parts[1]]})
        roster = [
            PartInventoryItem(
                part_id="part-123", part_name="desk_base", part_type="cad",
                features=[], parameters=[],
            )
        ]

        self.assertEqual(
            validate_project_plan(plan, existing_parts=roster, limits=_limits()), []
        )

    def test_multiple_unknown_parts_are_each_reported(self):
        plan = _plan()
        base = plan.parts[0].model_copy(
            update={"kind": "existing", "existing_part_id": "not-in-roster"}
        )
        arm = plan.parts[1].model_copy(update={"existing_part_id": "also-bad"})
        plan = plan.model_copy(update={"parts": [base, arm]})

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 2)
        self.assertTrue(all(v.code == "PROJECT_PLAN_UNKNOWN_PART" for v in violations))
        self.assertEqual({v.details["ref"] for v in violations}, {"base", "arm"})

    def test_interface_endpoint_references_unknown_part_rejected(self):
        plan = _plan()
        interface = plan.interfaces[0].model_copy(
            update={"endpoint_b": InterfaceEndpoint(part_ref="nonexistent", role="shaft")}
        )
        plan = plan.model_copy(update={"interfaces": [interface]})

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_INVALID_INTERFACE")
        self.assertEqual(violations[0].details["condition"], "unknown_endpoint_ref")
        self.assertEqual(violations[0].details["endpoint_a"], "base")
        self.assertEqual(violations[0].details["endpoint_b"], "nonexistent")

    def test_self_interface_rejected(self):
        plan = _plan()
        interface = plan.interfaces[0].model_copy(
            update={"endpoint_b": InterfaceEndpoint(part_ref="base", role="shaft")}
        )
        plan = plan.model_copy(update={"interfaces": [interface]})

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_INVALID_INTERFACE")
        self.assertEqual(violations[0].details["condition"], "self_reference")
        self.assertEqual(violations[0].details["endpoint_a"], "base")
        self.assertEqual(violations[0].details["endpoint_b"], "base")

    def test_multiple_invalid_interfaces_are_each_reported(self):
        plan = _plan()
        self_interface = plan.interfaces[0].model_copy(
            update={
                "ref": "self_iface",
                "endpoint_b": InterfaceEndpoint(part_ref="base", role="shaft"),
            }
        )
        unknown_interface = plan.interfaces[0].model_copy(
            update={
                "ref": "unknown_iface",
                "endpoint_b": InterfaceEndpoint(part_ref="nonexistent", role="shaft"),
            }
        )
        plan = plan.model_copy(update={"interfaces": [self_interface, unknown_interface]})

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 2)
        self.assertTrue(all(v.code == "PROJECT_PLAN_INVALID_INTERFACE" for v in violations))
        self.assertEqual(
            {v.details["interface_ref"] for v in violations},
            {"self_iface", "unknown_iface"},
        )

    def test_execution_dependency_references_unknown_part_rejected(self):
        plan = _plan()
        plan = plan.model_copy(
            update={
                "execution_dependencies": [
                    ExecutionDependency(
                        prerequisite_part_ref="base",
                        dependent_part_ref="nonexistent",
                        reason="Bogus dependency.",
                    )
                ]
            }
        )

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_INVALID_DEPENDENCY")
        self.assertEqual(violations[0].details["invalid_refs"], ["nonexistent"])

    def test_requirement_with_no_owner_rejected(self):
        plan = _plan()
        plan = plan.model_copy(
            update={
                "requirements": [
                    *plan.requirements,
                    ProjectRequirement(ref="orphan", description="Nothing addresses this."),
                ]
            }
        )

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_REQUIREMENT_UNADDRESSED")
        self.assertIn("orphan", violations[0].details["unaddressed_refs"])

    def test_addresses_requirements_references_unknown_ref_rejected(self):
        plan = _plan()
        base = plan.parts[0].model_copy(
            update={"addresses_requirements": ["stable_support", "no_such_requirement"]}
        )
        plan = plan.model_copy(update={"parts": [base, plan.parts[1]]})

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_REQUIREMENT_UNADDRESSED")
        self.assertIn("no_such_requirement", violations[0].details["unknown_refs"])

    def test_max_parts_exceeded_rejected(self):
        violations = validate_project_plan(
            _plan(), existing_parts=[], limits=_limits(max_parts=1)
        )

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_TOO_COMPLEX")
        self.assertEqual(violations[0].details["condition"], "too_many_parts")

    def test_max_interfaces_exceeded_rejected(self):
        violations = validate_project_plan(
            _plan(), existing_parts=[], limits=_limits(max_interfaces=0)
        )

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "PROJECT_PLAN_TOO_COMPLEX")
        self.assertEqual(violations[0].details["condition"], "too_many_interfaces")

    def test_both_complexity_limits_exceeded_reported_as_two_violations(self):
        violations = validate_project_plan(
            _plan(), existing_parts=[], limits=_limits(max_parts=1, max_interfaces=0)
        )

        self.assertEqual(len(violations), 2)
        self.assertTrue(all(v.code == "PROJECT_PLAN_TOO_COMPLEX" for v in violations))
        self.assertEqual(
            {v.details["condition"] for v in violations},
            {"too_many_parts", "too_many_interfaces"},
        )

    def test_unrelated_violations_are_all_returned_together(self):
        plan = _plan()
        duplicated = plan.parts[1].model_copy(update={"ref": "base"})
        plan = plan.model_copy(
            update={
                "parts": [plan.parts[0], duplicated],
                "requirements": [
                    *plan.requirements,
                    ProjectRequirement(ref="orphan", description="Nothing addresses this."),
                ],
            }
        )

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        codes = {v.code for v in violations}
        self.assertIn("PROJECT_PLAN_DUPLICATE_PART_REF", codes)
        self.assertIn("PROJECT_PLAN_REQUIREMENT_UNADDRESSED", codes)


class ExecutionDependencyCycleAndInterfaceRefValidatorTests(unittest.TestCase):
    """Dependency-cycle and duplicate-interface-ref checks used to live in
    a separate validate_project_plan_for_publish, only run at publish time
    with no chance to repair. They're now part of validate_project_plan
    itself, so the repair loop can catch and fix them too -- these tests
    exercise validate_project_plan directly."""

    def test_self_execution_dependency_is_a_cycle(self):
        plan = _plan().model_copy(
            update={
                "execution_dependencies": [
                    ExecutionDependency(
                        prerequisite_part_ref="base",
                        dependent_part_ref="base",
                        reason="Bogus self-dependency.",
                    )
                ]
            }
        )

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertTrue(
            any(v.code == "PROJECT_PLAN_EXECUTION_DEPENDENCY_CYCLE" for v in violations)
        )

    def test_execution_dependency_cycle_rejected(self):
        plan = _plan()
        holder = plan.parts[1].model_copy(
            update={"ref": "holder", "name": "phone_holder", "addresses_requirements": []}
        )
        plan = plan.model_copy(
            update={
                "parts": [*plan.parts, holder],
                "execution_dependencies": [
                    ExecutionDependency(
                        prerequisite_part_ref="base", dependent_part_ref="arm", reason="r"
                    ),
                    ExecutionDependency(
                        prerequisite_part_ref="arm", dependent_part_ref="holder", reason="r"
                    ),
                    ExecutionDependency(
                        prerequisite_part_ref="holder", dependent_part_ref="base", reason="r"
                    ),
                ],
            }
        )

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertTrue(
            any(v.code == "PROJECT_PLAN_EXECUTION_DEPENDENCY_CYCLE" for v in violations)
        )

    def test_duplicate_interface_ref_rejected(self):
        plan = _plan()
        duplicate_interface = plan.interfaces[0].model_copy()
        plan = plan.model_copy(
            update={"interfaces": [plan.interfaces[0], duplicate_interface]}
        )

        violations = validate_project_plan(plan, existing_parts=[], limits=_limits())

        self.assertTrue(
            any(v.code == "PROJECT_PLAN_DUPLICATE_INTERFACE_REF" for v in violations)
        )


if __name__ == "__main__":
    unittest.main()
