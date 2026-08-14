from __future__ import annotations

from uuid import uuid4

from .contracts import (
    AssemblyDependency,
    AssemblyEndpoint,
    AssemblyNode,
    AssemblyRequirement,
    AssemblySpec,
    InterfaceContract,
    PartBinding,
    ProjectPlan,
)


def build_assembly_spec(plan: ProjectPlan, *, project_id: str) -> AssemblySpec:
    """Deterministically derives an AssemblySpec from an already-validated
    plan. Every ref used below has been proven by validator.py to exist and
    be unique, so the ref -> node_id lookups can never KeyError."""

    node_ids: dict[str, str] = {part.ref: str(uuid4()) for part in plan.parts}

    nodes = tuple(
        AssemblyNode(
            node_id=node_ids[part.ref],
            semantic_ref=part.ref,
            name=part.name,
            binding=PartBinding(
                mode="existing" if part.kind == "existing" else "create",
                part_id=part.existing_part_id,
            ),
            purpose=part.purpose,
            responsibilities=tuple(part.responsibilities),
            addresses_requirements=tuple(part.addresses_requirements),
        )
        for part in plan.parts
    )

    interfaces = tuple(
        InterfaceContract(
            interface_id=str(uuid4()),
            semantic_ref=interface.ref,
            interface_type=interface.interface_type,
            endpoint_a=AssemblyEndpoint(
                node_id=node_ids[interface.endpoint_a.part_ref],
                role=interface.endpoint_a.role,
            ),
            endpoint_b=AssemblyEndpoint(
                node_id=node_ids[interface.endpoint_b.part_ref],
                role=interface.endpoint_b.role,
            ),
            purpose=interface.purpose,
            parameters=tuple(interface.parameters),
            requirements=tuple(interface.requirements),
        )
        for interface in plan.interfaces
    )

    dependencies = tuple(
        AssemblyDependency(
            prerequisite_node_id=node_ids[dependency.prerequisite_part_ref],
            dependent_node_id=node_ids[dependency.dependent_part_ref],
            prerequisite_ref=dependency.prerequisite_part_ref,
            dependent_ref=dependency.dependent_part_ref,
            reason=dependency.reason,
        )
        for dependency in plan.execution_dependencies
    )

    return AssemblySpec(
        spec_id=str(uuid4()),
        schema_version=plan.schema_version,
        project_id=project_id,
        summary=plan.summary,
        requirements=tuple(
            AssemblyRequirement(ref=requirement.ref, description=requirement.description)
            for requirement in plan.requirements
        ),
        nodes=nodes,
        interfaces=interfaces,
        execution_dependencies=dependencies,
        assumptions=tuple(plan.assumptions),
    )
