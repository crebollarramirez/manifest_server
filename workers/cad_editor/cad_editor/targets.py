from __future__ import annotations

import ast
import hashlib
import textwrap
from dataclasses import dataclass
from typing import Iterable

from .contracts import AllowedTarget, WorkflowFailure


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
MetadataValue = str | list[str]


@dataclass(frozen=True)
class TargetSpan:
    target: AllowedTarget
    start: int
    end: int
    indent: int


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def semantic_ids_in_source(source: str) -> list[str]:
    """Return literal top-level cad_part semantic IDs in source order."""
    tree = ast.parse(source)
    semantic_ids: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "cad_part"
            ):
                continue
            for keyword in decorator.keywords:
                if (
                    keyword.arg == "semantic_id"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    semantic_ids.append(keyword.value.value)
    return list(dict.fromkeys(semantic_ids))


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _line_start(offsets: list[int], line_number: int) -> int:
    return offsets[line_number - 1]


def _line_end(source: str, offsets: list[int], line_number: int) -> int:
    if line_number < len(offsets):
        return offsets[line_number]
    return len(source)


def _decorator_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _decorator_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _literal_strings(node: ast.AST) -> list[str]:
    if not isinstance(node, ast.Tuple):
        raise WorkflowFailure(
            "INVALID_ACCEPTED_SOURCE",
            "cad_part tuple metadata must use a literal tuple.",
        )
    values: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or type(item.value) is not str:
            raise WorkflowFailure(
                "INVALID_ACCEPTED_SOURCE",
                "cad_part tuple metadata must contain literal strings.",
            )
        values.append(item.value)
    return values


def _feature_data(
    function: FunctionNode,
) -> tuple[ast.Call, dict[str, MetadataValue]] | None:
    decorators = [
        decorator
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and _decorator_name(decorator.func) == "cad_part"
    ]
    if not decorators:
        return None
    if len(decorators) != 1:
        raise WorkflowFailure(
            "INVALID_ACCEPTED_SOURCE",
            f"{function.name} must have one cad_part decorator.",
        )
    decorator = decorators[0]
    values: dict[str, MetadataValue] = {}
    for keyword in decorator.keywords:
        if keyword.arg in {"semantic_id", "role", "library"}:
            if not (
                isinstance(keyword.value, ast.Constant)
                and type(keyword.value.value) is str
            ):
                raise WorkflowFailure(
                    "INVALID_ACCEPTED_SOURCE",
                    f"{function.name}.{keyword.arg} must be a literal string.",
                )
            values[keyword.arg] = keyword.value.value
        elif keyword.arg in {"parameters", "depends_on", "search_keys"}:
            values[keyword.arg] = _literal_strings(keyword.value)
    return decorator, values


def collect_target_spans(
    source: str,
    *,
    part_id: str,
    semantic_ids: Iterable[str],
    extra_function_names: Iterable[str] = (),
) -> dict[str, TargetSpan]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise WorkflowFailure(
            "INVALID_CANDIDATE_SOURCE",
            f"Source cannot be targeted because it is invalid Python: {exc.msg}",
        ) from exc

    requested_ids = set(semantic_ids)
    extra_functions = set(extra_function_names)
    offsets = _line_offsets(source)
    feature_functions: dict[
        str,
        tuple[FunctionNode, ast.Call, dict[str, MetadataValue]],
    ] = {}
    functions: dict[str, FunctionNode] = {}
    model_params: ast.ClassDef | None = None

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ModelParams":
            model_params = node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        functions[node.name] = node
        feature = _feature_data(node)
        if feature is None:
            continue
        decorator, values = feature
        semantic_id = str(values.get("semantic_id", ""))
        feature_functions[semantic_id] = (node, decorator, values)

    missing = sorted(requested_ids - feature_functions.keys())
    if missing:
        raise WorkflowFailure(
            "TARGET_NOT_FOUND",
            f"Indexed semantic targets are missing from source: {', '.join(missing)}.",
        )

    spans: dict[str, TargetSpan] = {}
    for semantic_id in sorted(requested_ids):
        function, decorator, values = feature_functions[semantic_id]

        metadata_id = f"{part_id}:cad_part_metadata:{semantic_id}"
        metadata_start = _line_start(offsets, decorator.lineno)
        metadata_end = _line_end(
            source,
            offsets,
            decorator.end_lineno or decorator.lineno,
        )
        spans[metadata_id] = TargetSpan(
            target=AllowedTarget(
                target_id=metadata_id,
                kind="cad_part_metadata",
                part_id=part_id,
                name=semantic_id,
                semantic_id=semantic_id,
                source=source[metadata_start:metadata_end].rstrip("\n"),
                line_start=decorator.lineno,
                line_end=decorator.end_lineno or decorator.lineno,
                details={
                    "role": values.get("role"),
                    "library": values.get("library"),
                    "parameters": values.get("parameters", []),
                    "depends_on": values.get("depends_on", []),
                    "search_keys": values.get("search_keys", []),
                },
            ),
            start=metadata_start,
            end=metadata_end,
            indent=function.col_offset,
        )

        if not function.body or function.body[0].lineno == function.lineno:
            raise WorkflowFailure(
                "UNSUPPORTED_SOURCE_LAYOUT",
                f"{function.name} must use a multiline function body.",
            )
        body_id = f"{part_id}:function_body:{function.name}"
        body_start = _line_start(offsets, function.body[0].lineno)
        body_end = _line_end(
            source,
            offsets,
            function.end_lineno or function.body[-1].end_lineno or function.lineno,
        )
        spans[body_id] = TargetSpan(
            target=AllowedTarget(
                target_id=body_id,
                kind="function_body",
                part_id=part_id,
                name=function.name,
                semantic_id=semantic_id,
                source=textwrap.dedent(source[body_start:body_end]).rstrip("\n"),
                line_start=function.body[0].lineno,
                line_end=function.end_lineno or function.body[-1].lineno,
            ),
            start=body_start,
            end=body_end,
            indent=function.body[0].col_offset,
        )

    for function_name in sorted(extra_functions):
        extra_function = functions.get(function_name)
        if extra_function is None or not extra_function.body:
            continue
        target_id = f"{part_id}:function_body:{function_name}"
        if target_id in spans:
            continue
        body_start = _line_start(offsets, extra_function.body[0].lineno)
        body_end = _line_end(
            source,
            offsets,
            extra_function.end_lineno
            or extra_function.body[-1].end_lineno
            or extra_function.lineno,
        )
        feature = _feature_data(extra_function)
        extra_semantic_id = (
            str(feature[1].get("semantic_id"))
            if feature is not None and feature[1].get("semantic_id")
            else None
        )
        spans[target_id] = TargetSpan(
            target=AllowedTarget(
                target_id=target_id,
                kind="function_body",
                part_id=part_id,
                name=function_name,
                semantic_id=extra_semantic_id,
                source=textwrap.dedent(source[body_start:body_end]).rstrip("\n"),
                line_start=extra_function.body[0].lineno,
                line_end=extra_function.end_lineno or extra_function.body[-1].lineno,
            ),
            start=body_start,
            end=body_end,
            indent=extra_function.body[0].col_offset,
        )

    build_model = functions.get("build_model")
    if build_model is None or not build_model.body:
        raise WorkflowFailure(
            "INVALID_ACCEPTED_SOURCE",
            "build_model is missing or has no body.",
        )
    build_target_id = f"{part_id}:build_model_body:build_model"
    build_body_start = _line_start(offsets, build_model.body[0].lineno)
    build_body_end = _line_end(
        source,
        offsets,
        build_model.end_lineno
        or build_model.body[-1].end_lineno
        or build_model.lineno,
    )
    spans[build_target_id] = TargetSpan(
        target=AllowedTarget(
            target_id=build_target_id,
            kind="build_model_body",
            part_id=part_id,
            name="build_model",
            source=textwrap.dedent(
                source[build_body_start:build_body_end]
            ).rstrip("\n"),
            line_start=build_model.body[0].lineno,
            line_end=build_model.end_lineno or build_model.body[-1].lineno,
        ),
        start=build_body_start,
        end=build_body_end,
        indent=build_model.body[0].col_offset,
    )

    if model_params is None:
        raise WorkflowFailure(
            "INVALID_ACCEPTED_SOURCE",
            "ModelParams is missing.",
        )
    parameter_nodes = {
        node.target.id: node
        for node in model_params.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for parameter_name in sorted(parameter_nodes):
        parameter_node = parameter_nodes.get(parameter_name)
        if parameter_node is None:
            raise WorkflowFailure(
                "TARGET_NOT_FOUND",
                f'ModelParams field "{parameter_name}" is missing.',
            )
        target_id = f"{part_id}:model_parameter:{parameter_name}"
        start = _line_start(offsets, parameter_node.lineno)
        end = _line_end(
            source,
            offsets,
            parameter_node.end_lineno or parameter_node.lineno,
        )
        spans[target_id] = TargetSpan(
            target=AllowedTarget(
                target_id=target_id,
                kind="model_parameter",
                part_id=part_id,
                name=parameter_name,
                source=textwrap.dedent(source[start:end]).rstrip("\n"),
                line_start=parameter_node.lineno,
                line_end=parameter_node.end_lineno or parameter_node.lineno,
            ),
            start=start,
            end=end,
            indent=parameter_node.col_offset,
        )
    return spans


def resolve_feature_body_span(spans: dict[str, object], semantic_id: str) -> object:
    """Resolve a public feature semantic ID to its current function body."""
    matches = [
        span
        for span in spans.values()
        if getattr(getattr(span, "target", span), "kind", None) == "function_body"
        and getattr(getattr(span, "target", span), "semantic_id", None) == semantic_id
    ]
    if not matches:
        raise WorkflowFailure(
            "TARGET_NOT_FOUND",
            f'CAD feature semantic ID "{semantic_id}" has no editable function body.',
        )
    if len(matches) != 1:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f'CAD feature semantic ID "{semantic_id}" is ambiguous.',
        )
    return matches[0]
