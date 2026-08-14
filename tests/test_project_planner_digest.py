from __future__ import annotations

import re
import unittest
from uuid import uuid4

from workers.project_planner.project_planner.contracts import (
    AssemblyEndpoint,
    AssemblyNode,
    AssemblyRequirement,
    AssemblySpec,
    InterfaceContract,
    PartBinding,
)
from workers.project_planner.project_planner.digest import (
    canonical_assembly_spec_json,
    compute_definition_digest,
)


def _spec(
    *,
    spec_id: str | None = None,
    base_node_id: str | None = None,
    arm_node_id: str | None = None,
    interface_id: str | None = None,
    summary: str = "A test assembly.",
    swap_endpoints: bool = False,
) -> AssemblySpec:
    base_node_id = base_node_id or str(uuid4())
    arm_node_id = arm_node_id or str(uuid4())
    endpoint_a_id = arm_node_id if swap_endpoints else base_node_id
    endpoint_b_id = base_node_id if swap_endpoints else arm_node_id
    return AssemblySpec(
        spec_id=spec_id or str(uuid4()),
        schema_version=1,
        project_id=str(uuid4()),
        summary=summary,
        requirements=(AssemblyRequirement(ref="r1", description="d"),),
        nodes=(
            AssemblyNode(
                node_id=base_node_id, semantic_ref="base", name="Base",
                binding=PartBinding(mode="create"), purpose="p",
                responsibilities=(), addresses_requirements=("r1",),
            ),
            AssemblyNode(
                node_id=arm_node_id, semantic_ref="arm", name="Arm",
                binding=PartBinding(mode="create"), purpose="p",
                responsibilities=(), addresses_requirements=(),
            ),
        ),
        interfaces=(
            InterfaceContract(
                interface_id=interface_id or str(uuid4()),
                semantic_ref="base_arm",
                interface_type="rotational",
                endpoint_a=AssemblyEndpoint(node_id=endpoint_a_id, role="socket"),
                endpoint_b=AssemblyEndpoint(node_id=endpoint_b_id, role="shaft"),
                purpose="p",
                parameters=(),
                requirements=(),
            ),
        ),
        execution_dependencies=(),
        assumptions=(),
    )


class DigestTests(unittest.TestCase):
    def test_digest_is_a_valid_sha256_hex_string(self):
        digest = compute_definition_digest(_spec())
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", digest))

    def test_canonical_json_output_has_sorted_top_level_keys(self):
        import json

        canonical = canonical_assembly_spec_json(_spec())
        parsed = json.loads(canonical)
        self.assertEqual(list(parsed.keys()), sorted(parsed.keys()))
        # spec_id/schema_version/project_id must not survive canonicalization.
        self.assertNotIn("spec_id", parsed)
        self.assertNotIn("schema_version", parsed)
        self.assertNotIn("project_id", parsed)
        self.assertNotIn("node_id", parsed["nodes"][0])
        self.assertNotIn("interface_id", parsed["interfaces"][0])

    def test_digest_ignores_server_generated_random_ids(self):
        spec1 = _spec()
        spec2 = _spec()  # fresh spec_id, base_node_id, arm_node_id, interface_id every call
        self.assertEqual(compute_definition_digest(spec1), compute_definition_digest(spec2))

    def test_digest_changes_when_connectivity_changes(self):
        base_node_id, arm_node_id = str(uuid4()), str(uuid4())
        original = _spec(base_node_id=base_node_id, arm_node_id=arm_node_id, swap_endpoints=False)
        swapped = _spec(base_node_id=base_node_id, arm_node_id=arm_node_id, swap_endpoints=True)
        self.assertNotEqual(
            compute_definition_digest(original), compute_definition_digest(swapped)
        )

    def test_digest_changes_when_semantic_content_changes(self):
        spec1 = _spec(summary="A phone stand.")
        spec2 = _spec(summary="A completely different design.")
        self.assertNotEqual(compute_definition_digest(spec1), compute_definition_digest(spec2))


if __name__ == "__main__":
    unittest.main()
