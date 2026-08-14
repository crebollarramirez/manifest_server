from __future__ import annotations

from .contracts import (
    PartInventoryItem,
    PlanViolation,
    ProjectPlanDraft,
    ProjectPlanningLimits,
)


def validate_project_plan(
    plan: ProjectPlanDraft,
    *,
    existing_parts: list[PartInventoryItem],
    limits: ProjectPlanningLimits,
) -> list[PlanViolation]:
    """Runs every deterministic check and returns every violation found
    (empty list means the plan is valid). Never raises for an ordinary
    rule violation -- callers (service.py's repair loop) decide what to
    do with the result."""
    violations: list[PlanViolation] = []
    violations.extend(_check_unique_part_refs(plan))
    violations.extend(_check_part_bindings(plan, existing_parts))
    violations.extend(_check_interfaces(plan))
    violations.extend(_check_unique_interface_refs(plan))
    violations.extend(_check_execution_dependencies(plan))
    violations.extend(_check_execution_dependency_cycles(plan))
    violations.extend(_check_requirement_coverage(plan))
    violations.extend(_check_policy_limits(plan, limits))
    return violations


def _check_unique_part_refs(plan: ProjectPlanDraft) -> list[PlanViolation]:
    refs = [part.ref for part in plan.parts]
    if len(refs) == len(set(refs)):
        return []
    duplicates = sorted({ref for ref in refs if refs.count(ref) > 1})
    return [
        PlanViolation(
            code="PROJECT_PLAN_DUPLICATE_PART_REF",
            message="Two or more planned parts share the same ref.",
            details={"duplicate_refs": duplicates},
        )
    ]


def _check_part_bindings(
    plan: ProjectPlanDraft, existing_parts: list[PartInventoryItem]
) -> list[PlanViolation]:
    known_ids = {item.part_id for item in existing_parts}
    valid_existing_part_ids = sorted(known_ids)
    violations: list[PlanViolation] = []
    for part in plan.parts:
        if part.kind == "existing":
            if not part.existing_part_id:
                violations.append(
                    PlanViolation(
                        code="PROJECT_PLAN_UNKNOWN_PART",
                        message=(
                            f'Planned part "{part.ref}" has kind="existing" but '
                            "did not supply an existing_part_id."
                        ),
                        details={
                            "condition": "missing_existing_part_id",
                            "ref": part.ref,
                            "existing_part_id": part.existing_part_id,
                            "valid_existing_part_ids": valid_existing_part_ids,
                        },
                    )
                )
            elif part.existing_part_id not in known_ids:
                violations.append(
                    PlanViolation(
                        code="PROJECT_PLAN_UNKNOWN_PART",
                        message=(
                            f'Planned part "{part.ref}" claims kind="existing" but '
                            "its existing_part_id was not in the supplied project "
                            "roster."
                        ),
                        details={
                            "condition": "unknown_existing_part_id",
                            "ref": part.ref,
                            "existing_part_id": part.existing_part_id,
                            "valid_existing_part_ids": valid_existing_part_ids,
                        },
                    )
                )
        elif part.existing_part_id is not None:
            violations.append(
                PlanViolation(
                    code="PROJECT_PLAN_UNKNOWN_PART",
                    message=(
                        f'Planned part "{part.ref}" has kind="new" but supplied an '
                        "existing_part_id."
                    ),
                    details={
                        "condition": "unexpected_existing_part_id_for_new_part",
                        "ref": part.ref,
                        "existing_part_id": part.existing_part_id,
                        "valid_existing_part_ids": valid_existing_part_ids,
                    },
                )
            )
    return violations


def _check_interfaces(plan: ProjectPlanDraft) -> list[PlanViolation]:
    part_refs = {part.ref for part in plan.parts}
    violations: list[PlanViolation] = []
    for interface in plan.interfaces:
        endpoint_a = interface.endpoint_a.part_ref
        endpoint_b = interface.endpoint_b.part_ref
        if endpoint_a not in part_refs or endpoint_b not in part_refs:
            violations.append(
                PlanViolation(
                    code="PROJECT_PLAN_INVALID_INTERFACE",
                    message=f'Interface "{interface.ref}" references an unknown part ref.',
                    details={
                        "condition": "unknown_endpoint_ref",
                        "interface_ref": interface.ref,
                        "endpoint_a": endpoint_a,
                        "endpoint_b": endpoint_b,
                    },
                )
            )
        elif endpoint_a == endpoint_b:
            violations.append(
                PlanViolation(
                    code="PROJECT_PLAN_INVALID_INTERFACE",
                    message=f'Interface "{interface.ref}" connects a part to itself.',
                    details={
                        "condition": "self_reference",
                        "interface_ref": interface.ref,
                        "endpoint_a": endpoint_a,
                        "endpoint_b": endpoint_b,
                    },
                )
            )
    return violations


def _check_execution_dependencies(plan: ProjectPlanDraft) -> list[PlanViolation]:
    part_refs = {part.ref for part in plan.parts}
    violations: list[PlanViolation] = []
    for dependency in plan.execution_dependencies:
        invalid_refs = [
            ref
            for ref in (dependency.prerequisite_part_ref, dependency.dependent_part_ref)
            if ref not in part_refs
        ]
        if invalid_refs:
            violations.append(
                PlanViolation(
                    code="PROJECT_PLAN_INVALID_DEPENDENCY",
                    message="An execution dependency references an unknown part ref.",
                    details={
                        "prerequisite_part_ref": dependency.prerequisite_part_ref,
                        "dependent_part_ref": dependency.dependent_part_ref,
                        "invalid_refs": invalid_refs,
                    },
                )
            )
    return violations


def _check_requirement_coverage(plan: ProjectPlanDraft) -> list[PlanViolation]:
    all_refs = {requirement.ref for requirement in plan.requirements}
    addressed = {
        ref for part in plan.parts for ref in part.addresses_requirements
    } | {
        ref for interface in plan.interfaces for ref in interface.addresses_requirements
    }
    unaddressed = all_refs - addressed
    unknown = addressed - all_refs
    if not unaddressed and not unknown:
        return []
    return [
        PlanViolation(
            code="PROJECT_PLAN_REQUIREMENT_UNADDRESSED",
            message=(
                "Every requirement must be addressed by exactly the parts/interfaces "
                "that reference it, and vice versa."
            ),
            details={
                "unaddressed_refs": sorted(unaddressed),
                "unknown_refs": sorted(unknown),
            },
        )
    ]


def _check_policy_limits(
    plan: ProjectPlanDraft, limits: ProjectPlanningLimits
) -> list[PlanViolation]:
    violations: list[PlanViolation] = []
    if len(plan.parts) > limits.max_parts:
        violations.append(
            PlanViolation(
                code="PROJECT_PLAN_TOO_COMPLEX",
                message="The plan has more parts than the configured limit allows.",
                details={
                    "condition": "too_many_parts",
                    "part_count": len(plan.parts),
                    "max_parts": limits.max_parts,
                },
            )
        )
    if len(plan.interfaces) > limits.max_interfaces:
        violations.append(
            PlanViolation(
                code="PROJECT_PLAN_TOO_COMPLEX",
                message="The plan has more interfaces than the configured limit allows.",
                details={
                    "condition": "too_many_interfaces",
                    "interface_count": len(plan.interfaces),
                    "max_interfaces": limits.max_interfaces,
                },
            )
        )
    return violations


def _check_unique_interface_refs(plan: ProjectPlanDraft) -> list[PlanViolation]:
    refs = [interface.ref for interface in plan.interfaces]
    if len(refs) == len(set(refs)):
        return []
    duplicates = sorted({ref for ref in refs if refs.count(ref) > 1})
    return [
        PlanViolation(
            code="PROJECT_PLAN_DUPLICATE_INTERFACE_REF",
            message="Two or more planned interfaces share the same ref.",
            details={"duplicate_refs": duplicates},
        )
    ]


def _check_execution_dependency_cycles(plan: ProjectPlanDraft) -> list[PlanViolation]:
    """Mirrors workers/indexer/indexer/semantic_graph.py's
    validate_dependency_graph DFS (a 'visiting' set catches back-edges, a
    'visited' set avoids re-walking a cleared node, the current path is
    threaded through recursion to build a readable cycle trace) -- applied
    to part_refs instead of semantic_ids. Only walks edges between refs
    already known to exist: a dangling ref is already reported by
    _check_execution_dependencies as PROJECT_PLAN_INVALID_DEPENDENCY, and
    reporting it again here under a different code would be confusing.
    Reports the first cycle found rather than enumerating every cycle in
    the graph."""
    part_refs = {part.ref for part in plan.parts}
    graph: dict[str, list[str]] = {ref: [] for ref in part_refs}
    for dependency in plan.execution_dependencies:
        if (
            dependency.prerequisite_part_ref in part_refs
            and dependency.dependent_part_ref in part_refs
        ):
            graph[dependency.prerequisite_part_ref].append(
                dependency.dependent_part_ref
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(ref: str, path: list[str]) -> list[str] | None:
        if ref in visiting:
            start = path.index(ref)
            return path[start:] + [ref]
        if ref in visited:
            return None
        visiting.add(ref)
        for dependent in graph[ref]:
            cycle = visit(dependent, [*path, ref])
            if cycle is not None:
                return cycle
        visiting.discard(ref)
        visited.add(ref)
        return None

    for ref in sorted(graph):
        cycle = visit(ref, [])
        if cycle is not None:
            return [
                PlanViolation(
                    code="PROJECT_PLAN_EXECUTION_DEPENDENCY_CYCLE",
                    message=(
                        "Execution dependencies must be acyclic: "
                        + " -> ".join(cycle)
                    ),
                    details={"cycle": cycle},
                )
            ]
    return []
