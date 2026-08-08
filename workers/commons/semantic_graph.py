from __future__ import annotations

import ast
from typing import Iterable


def _called_name(node: ast.Call) -> str | None:
    return node.func.id if isinstance(node.func, ast.Name) else None


def effective_parameter_references(
    functions: Iterable[ast.FunctionDef | ast.AsyncFunctionDef],
) -> dict[str, list[str]]:
    """Return params fields used by each function, including private helpers."""
    function_map = {function.name: function for function in functions}
    direct: dict[str, set[str]] = {}
    calls: dict[str, set[str]] = {}
    for name, function in function_map.items():
        direct[name] = {
            node.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "params"
        }
        calls[name] = {
            called
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and (called := _called_name(node)) is not None
            and called in function_map
            and called.startswith("_")
        }

    memo: dict[str, set[str]] = {}

    def resolve(name: str, visiting: set[str]) -> set[str]:
        if name in memo:
            return memo[name]
        if name in visiting:
            return set(direct.get(name, set()))
        values = set(direct.get(name, set()))
        for helper in calls.get(name, set()):
            values.update(resolve(helper, visiting | {name}))
        memo[name] = values
        return values

    return {
        name: sorted(resolve(name, set()))
        for name in function_map
    }
