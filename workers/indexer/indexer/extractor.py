from __future__ import annotations

import ast
from typing import Any, NoReturn

from .models import IndexingError, SourceFile


REQUIRED_DECORATOR_FIELDS = (
    "semantic_id",
    "role",
    "library",
    "parameters",
    "depends_on",
    "search_keys",
)
STRING_FIELDS = {"semantic_id", "role", "library"}
TUPLE_FIELDS = {"parameters", "depends_on", "search_keys"}
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _error(
    source: SourceFile,
    message: str,
    node: ast.AST | None = None,
) -> NoReturn:
    location = source.storage_path
    if node is not None and hasattr(node, "lineno"):
        location += f":{node.lineno}:{getattr(node, 'col_offset', 0) + 1}"
    raise IndexingError(f"{location}: {message}")


def _source_segment(source: SourceFile, node: ast.AST | None) -> str | None:
    if node is None:
        return None
    segment = ast.get_source_segment(source.content, node)
    return segment.strip() if segment is not None else None


def _decorator_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _decorator_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _literal_value(source: SourceFile, node: ast.AST) -> str | tuple[str, ...]:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    if isinstance(node, ast.Tuple):
        values: list[str] = []
        for item in node.elts:
            if not isinstance(item, ast.Constant) or type(item.value) is not str:
                _error(source, "Decorator tuple values must be literal strings.", item)
            values.append(item.value)
        return tuple(values)
    _error(source, "Decorator values must be literal strings or tuples of strings.", node)


def _model_parameters(
    source: SourceFile,
    tree: ast.Module,
) -> list[dict[str, Any]]:
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ModelParams"
    ]
    if len(classes) != 1:
        _error(
            source,
            f"Expected exactly one top-level ModelParams class; found {len(classes)}.",
        )

    parameters: list[dict[str, Any]] = []
    for node in classes[0].body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        parameters.append(
            {
                "name": node.target.id,
                "type_name": _source_segment(source, node.annotation),
                "default_source": _source_segment(source, node.value),
                "line_start": node.lineno,
                "line_end": node.end_lineno or node.lineno,
            }
        )
    if not parameters:
        _error(source, "ModelParams must define at least one annotated field.", classes[0])
    return parameters


def _function_record(node: FunctionNode) -> dict[str, Any]:
    return {
        "name": node.name,
        "line_start": node.lineno,
        "line_end": node.end_lineno or node.lineno,
    }


def _feature_records(
    source: SourceFile,
    functions: list[FunctionNode],
    parameter_names: set[str],
) -> list[dict[str, Any]]:
    parsed: list[tuple[FunctionNode, ast.Call, dict[str, Any]]] = []
    for function in functions:
        decorators = [
            decorator
            for decorator in function.decorator_list
            if isinstance(decorator, ast.Call)
            and _decorator_name(decorator.func) == "cad_part"
        ]
        if not decorators:
            continue
        if len(decorators) != 1:
            _error(
                source,
                f"{function.name} must have exactly one @cad_part(...) decorator.",
                function,
            )
        decorator = decorators[0]
        if decorator.args or any(keyword.arg is None for keyword in decorator.keywords):
            _error(
                source,
                f"{function.name} must use named @cad_part arguments only.",
                decorator,
            )
        fields = tuple(keyword.arg for keyword in decorator.keywords)
        if fields != REQUIRED_DECORATOR_FIELDS:
            _error(
                source,
                f"{function.name} must use @cad_part fields in this order: "
                f"{', '.join(REQUIRED_DECORATOR_FIELDS)}.",
                decorator,
            )
        values = {
            keyword.arg: _literal_value(source, keyword.value)
            for keyword in decorator.keywords
            if keyword.arg is not None
        }
        parsed.append((function, decorator, values))

    if not parsed:
        _error(source, "At least one @cad_part(...) function is required.")

    semantic_ids = {
        values["semantic_id"]
        for _function, _decorator, values in parsed
        if isinstance(values["semantic_id"], str)
    }
    if len(semantic_ids) != len(parsed):
        _error(source, "cad_part semantic_id values must be unique within a part.")

    records: list[dict[str, Any]] = []
    for function, decorator, values in parsed:
        for field in STRING_FIELDS:
            value = values[field]
            if not isinstance(value, str) or not value.strip():
                _error(source, f"{function.name}.{field} must be non-empty.", function)
        for field in TUPLE_FIELDS:
            value = values[field]
            if not isinstance(value, tuple) or any(not item.strip() for item in value):
                _error(
                    source,
                    f"{function.name}.{field} must contain literal non-empty strings.",
                    function,
                )
        if values["library"] != "cadquery":
            _error(source, f'{function.name}.library must be "cadquery".', function)
        if not values["search_keys"]:
            _error(source, f"{function.name}.search_keys must not be empty.", function)

        unknown_parameters = sorted(set(values["parameters"]) - parameter_names)
        if unknown_parameters:
            _error(
                source,
                f"{function.name} references unknown ModelParams fields: "
                f"{', '.join(unknown_parameters)}.",
                function,
            )
        unknown_dependencies = sorted(set(values["depends_on"]) - semantic_ids)
        if unknown_dependencies:
            _error(
                source,
                f"{function.name} references unknown semantic IDs: "
                f"{', '.join(unknown_dependencies)}.",
                function,
            )
        if values["semantic_id"] in values["depends_on"]:
            _error(source, f"{function.name} must not depend on itself.", function)

        records.append(
            {
                "semantic_id": values["semantic_id"],
                "role": values["role"],
                "library": values["library"],
                "parameters": list(values["parameters"]),
                "depends_on": list(values["depends_on"]),
                "search_keys": list(values["search_keys"]),
                "function_name": function.name,
                "decorator_line_start": decorator.lineno,
                "function_line_start": function.lineno,
                "function_line_end": function.end_lineno or function.lineno,
            }
        )
    return records


def extract_part_index(source: SourceFile) -> dict[str, Any]:
    try:
        tree = ast.parse(source.content, filename=source.storage_path)
    except SyntaxError as exc:
        line = exc.lineno or 0
        column = exc.offset or 0
        raise IndexingError(
            f"{source.storage_path}:{line}:{column}: {exc.msg}"
        ) from exc

    parameters = _model_parameters(source, tree)
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    build_models = [node for node in functions if node.name == "build_model"]
    if len(build_models) != 1:
        _error(
            source,
            f"Expected exactly one top-level build_model function; found "
            f"{len(build_models)}.",
        )

    features = _feature_records(
        source,
        [
            function
            for function in functions
            if function.name != "build_model" and not function.name.startswith("_")
        ],
        {parameter["name"] for parameter in parameters},
    )
    return {
        "part_id": source.part_id,
        "part_name": source.part_name,
        "file_path": source.storage_path,
        "content_hash": source.content_hash,
        "model_params": parameters,
        "functions": [_function_record(function) for function in functions],
        "cad_parts": features,
        "build_model": _function_record(build_models[0]),
    }
