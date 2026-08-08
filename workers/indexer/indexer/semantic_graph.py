from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from workers.commons.semantic_graph import _called_name, effective_parameter_references

__all__ = [
    "SemanticGraphError",
    "build_model_dependencies",
    "effective_parameter_references",
    "graph_context",
    "validate_dependency_graph",
]


@dataclass(frozen=True)
class SemanticGraphError(ValueError):
    code: str
    message: str
    semantic_id: str | None = None

    def __str__(self) -> str:
        return self.message


def validate_dependency_graph(features: list[dict[str, Any]]) -> None:
    feature_ids = {str(feature["semantic_id"]) for feature in features}
    graph: dict[str, list[str]] = {}
    for feature in features:
        semantic_id = str(feature["semantic_id"])
        dependencies = [str(value) for value in feature.get("depends_on", [])]
        if len(dependencies) != len(set(dependencies)):
            raise SemanticGraphError(
                "duplicate_dependency",
                f'{semantic_id}.depends_on must not contain duplicate semantic IDs.',
                semantic_id,
            )
        unknown = sorted(set(dependencies) - feature_ids)
        if unknown:
            raise SemanticGraphError(
                "unknown_dependencies",
                f'{semantic_id}.depends_on references unknown semantic IDs: '
                + ", ".join(unknown),
                semantic_id,
            )
        if semantic_id in dependencies:
            raise SemanticGraphError(
                "self_dependency",
                f"{semantic_id} must not depend on itself.",
                semantic_id,
            )
        graph[semantic_id] = dependencies

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(semantic_id: str, path: list[str]) -> None:
        if semantic_id in visiting:
            start = path.index(semantic_id)
            cycle = path[start:] + [semantic_id]
            raise SemanticGraphError(
                "cyclic_dependency",
                "CAD feature dependencies must be acyclic: " + " → ".join(cycle),
                semantic_id,
            )
        if semantic_id in visited:
            return
        visiting.add(semantic_id)
        for dependency in graph[semantic_id]:
            visit(dependency, [*path, semantic_id])
        visiting.remove(semantic_id)
        visited.add(semantic_id)

    for semantic_id in graph:
        visit(semantic_id, [])


def build_model_dependencies(
    build_model: ast.FunctionDef | ast.AsyncFunctionDef,
    features: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], list[str]]:
    """Infer immediate producer dependencies from simple build_model dataflow."""
    function_to_semantic = {
        str(feature["function_name"]): str(feature["semantic_id"])
        for feature in features
    }
    produced_by: dict[str, str] = {}
    observed: dict[str, set[str]] = {
        semantic_id: set() for semantic_id in function_to_semantic.values()
    }
    invoked: list[str] = []

    def process_call(call: ast.Call, assigned_name: str | None) -> None:
        function_name = _called_name(call)
        if function_name not in function_to_semantic:
            return
        semantic_id = function_to_semantic[function_name]
        if semantic_id not in invoked:
            invoked.append(semantic_id)
        for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
            if isinstance(argument, ast.Name) and argument.id in produced_by:
                observed[semantic_id].add(produced_by[argument.id])
        if assigned_name:
            produced_by[assigned_name] = semantic_id

    for statement in build_model.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Call)
        ):
            process_call(statement.value, statement.targets[0].id)
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and isinstance(statement.value, ast.Call)
        ):
            process_call(statement.value, statement.target.id)
        elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            process_call(statement.value, None)
        elif isinstance(statement, ast.Return) and isinstance(statement.value, ast.Call):
            process_call(statement.value, None)

    return (
        {semantic_id: sorted(dependencies) for semantic_id, dependencies in observed.items()},
        invoked,
    )


def graph_context(features: list[dict[str, Any]]) -> dict[str, Any]:
    """Build planner-facing direct/transitive dependency information."""
    order = [str(feature["semantic_id"]) for feature in features]
    dependencies = {
        str(feature["semantic_id"]): [str(value) for value in feature.get("depends_on", [])]
        for feature in features
    }
    dependents: dict[str, list[str]] = {semantic_id: [] for semantic_id in order}
    for semantic_id, upstream in dependencies.items():
        for dependency in upstream:
            dependents[dependency].append(semantic_id)

    def closure(root: str, adjacency: dict[str, list[str]]) -> list[str]:
        values: list[str] = []
        queue = list(adjacency[root])
        while queue:
            current = queue.pop(0)
            if current in values:
                continue
            values.append(current)
            queue.extend(adjacency[current])
        return values

    def paths_from(root: str) -> dict[str, list[str]]:
        paths: dict[str, list[str]] = {}
        queue: list[tuple[str, list[str]]] = [
            (child, [root, child]) for child in dependents[root]
        ]
        while queue:
            current, path = queue.pop(0)
            if current in paths:
                continue
            paths[current] = path
            queue.extend(
                (child, [*path, child])
                for child in dependents[current]
            )
        return paths

    nodes = []
    for feature in features:
        semantic_id = str(feature["semantic_id"])
        nodes.append(
            {
                "semantic_id": semantic_id,
                "function_name": feature.get("function_name"),
                "role": feature.get("role"),
                "parameters": list(feature.get("parameters", [])),
                "parameter_references": list(
                    feature.get("parameter_references", feature.get("parameters", []))
                ),
                "depends_on": dependencies[semantic_id],
                "direct_dependents": dependents[semantic_id],
                "transitive_dependencies": closure(semantic_id, dependencies),
                "transitive_dependents": closure(semantic_id, dependents),
                "dependent_paths": paths_from(semantic_id),
            }
        )
    parameter_consumers: dict[str, list[str]] = {}
    for node in nodes:
        for parameter in node["parameter_references"]:
            parameter_consumers.setdefault(str(parameter), []).append(node["semantic_id"])
    return {
        "nodes": nodes,
        "parameter_consumers": parameter_consumers,
    }
