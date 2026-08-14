from __future__ import annotations

import unittest
from uuid import uuid4

from workers.project_planner.project_planner.contracts import AssemblySpec, ProjectPlan
from workers.project_planner.project_planner.spec_builder import build_assembly_spec


def _plan(**overrides) -> ProjectPlan:
    payload = {
        "plan_id": str(uuid4()),
        "schema_version": 1,
        "summary": "Create a two-part adjustable phone stand.",
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
                "kind": "existing",
                "existing_part_id": "part-123",
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
                "parameters": [{"name": "nominal_diameter", "value": "20.0", "unit": "mm"}],
                "requirements": ["The connection must support rotation."],
                "addresses_requirements": [],
            }
        ],
        "execution_dependencies": [
            {
                "prerequisite_part_ref": "arm",
                "dependent_part_ref": "base",
                "reason": "The base must match the existing arm's mount diameter.",
            }
        ],
        "assumptions": ["Assume desktop use."],
        "clarification": {"required": False, "question": None, "reason": None},
    }
    payload.update(overrides)
    return ProjectPlan.model_validate(payload)


class SpecBuilderTests(unittest.TestCase):
    def test_resolves_part_refs_to_distinct_node_ids(self):
        spec = build_assembly_spec(_plan(), project_id="project-1")
        node_ids = {node.node_id for node in spec.nodes}
        self.assertEqual(len(node_ids), 2)
        self.assertEqual({node.semantic_ref for node in spec.nodes}, {"base", "arm"})

    def test_binding_existing_vs_new(self):
        spec = build_assembly_spec(_plan(), project_id="project-1")
        by_ref = {node.semantic_ref: node for node in spec.nodes}
        self.assertEqual(by_ref["base"].binding.mode, "create")
        self.assertIsNone(by_ref["base"].binding.part_id)
        self.assertEqual(by_ref["arm"].binding.mode, "existing")
        self.assertEqual(by_ref["arm"].binding.part_id, "part-123")

    def test_interface_endpoints_use_node_ids_not_refs(self):
        spec = build_assembly_spec(_plan(), project_id="project-1")
        node_ids = {node.semantic_ref: node.node_id for node in spec.nodes}
        interface = spec.interfaces[0]
        self.assertEqual(interface.endpoint_a.node_id, node_ids["base"])
        self.assertEqual(interface.endpoint_b.node_id, node_ids["arm"])
        self.assertNotIn(interface.endpoint_a.node_id, {"base", "arm"})

    def test_execution_dependency_resolves_both_refs(self):
        spec = build_assembly_spec(_plan(), project_id="project-1")
        node_ids = {node.semantic_ref: node.node_id for node in spec.nodes}
        dependency = spec.execution_dependencies[0]
        self.assertEqual(dependency.prerequisite_node_id, node_ids["arm"])
        self.assertEqual(dependency.dependent_node_id, node_ids["base"])
        self.assertEqual(dependency.prerequisite_ref, "arm")
        self.assertEqual(dependency.dependent_ref, "base")

    def test_output_has_no_geometric_or_provenance_fields(self):
        expected_fields = {
            "spec_id",
            "schema_version",
            "project_id",
            "summary",
            "requirements",
            "nodes",
            "interfaces",
            "execution_dependencies",
            "assumptions",
        }
        self.assertEqual(set(AssemblySpec.model_fields.keys()), expected_fields)


if __name__ == "__main__":
    unittest.main()
