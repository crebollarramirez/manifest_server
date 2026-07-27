from __future__ import annotations

import ast
import textwrap
from typing import Iterable

from .contracts import (
    AddCadFeature,
    AddModelParameter,
    AddPrivateHelper,
    CandidateSource,
    EditPlan,
    ReplaceBuildModelBody,
    ReplaceFunctionBody,
    ReplaceParameterField,
    UpdateCadPartMetadata,
    WorkflowFailure,
)
from .targets import TargetSpan, collect_target_spans, source_hash


def _tuple_source(values: list[str]) -> str:
    if not values:
        return "()"
    rendered = ", ".join(repr(value) for value in values)
    if len(values) == 1:
        rendered += ","
    return f"({rendered})"


def _parameter_replacement(operation: ReplaceParameterField, span: TargetSpan) -> str:
    replacement = textwrap.dedent(operation.replacement_source).strip()
    try:
        tree = ast.parse(replacement)
    except SyntaxError as exc:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"Parameter replacement is invalid Python: {exc.msg}",
        ) from exc
    if not (
        len(tree.body) == 1
        and isinstance(tree.body[0], ast.AnnAssign)
        and isinstance(tree.body[0].target, ast.Name)
    ):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "replace_parameter_field must contain one annotated assignment.",
        )
    if tree.body[0].target.id != span.target.name:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "replace_parameter_field must preserve the targeted field name.",
        )
    return textwrap.indent(replacement, " " * span.indent) + "\n"


def _body_replacement(operation: ReplaceFunctionBody, span: TargetSpan) -> str:
    replacement = textwrap.dedent(operation.replacement_source).strip("\n")
    if not replacement.strip():
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "replace_function_body cannot use an empty body.",
        )
    indented = textwrap.indent(replacement, "    ")
    try:
        ast.parse(f"def _candidate():\n{indented}\n")
    except SyntaxError as exc:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"Function body replacement is invalid Python: {exc.msg}",
        ) from exc
    return textwrap.indent(replacement, " " * span.indent) + "\n"


def _build_model_replacement(
    operation: ReplaceBuildModelBody,
    span: TargetSpan,
) -> str:
    replacement = textwrap.dedent(operation.replacement_source).strip("\n")
    if not replacement.strip():
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "replace_build_model_body cannot use an empty body.",
        )
    try:
        ast.parse(f"def build_model(params):\n{textwrap.indent(replacement, '    ')}\n")
    except SyntaxError as exc:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"build_model body replacement is invalid Python: {exc.msg}",
        ) from exc
    return textwrap.indent(replacement, " " * span.indent) + "\n"


def _metadata_replacement(
    operation: UpdateCadPartMetadata,
    span: TargetSpan,
) -> str:
    values = [
        operation.role,
        *operation.parameters,
        *operation.depends_on,
        *operation.search_keys,
    ]
    if any(not value.strip() for value in values):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "cad_part metadata values must be non-empty strings.",
        )
    if span.target.semantic_id in operation.depends_on:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "A cad_part cannot depend on itself.",
        )
    indentation = " " * span.indent
    field_indent = indentation + "    "
    semantic_id = span.target.semantic_id
    return (
        f"{indentation}@cad_part(\n"
        f"{field_indent}semantic_id={semantic_id!r},\n"
        f"{field_indent}role={operation.role!r},\n"
        f'{field_indent}library="cadquery",\n'
        f"{field_indent}parameters={_tuple_source(operation.parameters)},\n"
        f"{field_indent}depends_on={_tuple_source(operation.depends_on)},\n"
        f"{field_indent}search_keys={_tuple_source(operation.search_keys)},\n"
        f"{indentation})\n"
    )


def _operation_replacement(operation, span: TargetSpan) -> str:
    if isinstance(operation, ReplaceParameterField):
        if span.target.kind != "model_parameter":
            raise WorkflowFailure(
                "INVALID_EDIT_PLAN",
                "replace_parameter_field must target a ModelParams field.",
            )
        return _parameter_replacement(operation, span)
    if isinstance(operation, UpdateCadPartMetadata):
        if span.target.kind != "cad_part_metadata":
            raise WorkflowFailure(
                "INVALID_EDIT_PLAN",
                "update_cad_part_metadata must target cad_part metadata.",
            )
        return _metadata_replacement(operation, span)
    if isinstance(operation, ReplaceFunctionBody):
        if span.target.kind != "function_body":
            raise WorkflowFailure(
                "INVALID_EDIT_PLAN",
                "replace_function_body must target a function body.",
            )
        return _body_replacement(operation, span)
    if isinstance(operation, ReplaceBuildModelBody):
        if span.target.kind != "build_model_body":
            raise WorkflowFailure(
                "INVALID_EDIT_PLAN",
                "replace_build_model_body must target build_model.",
            )
        return _build_model_replacement(operation, span)
    raise WorkflowFailure("INVALID_EDIT_PLAN", "Unsupported edit operation.")


def _single_function(source: str, label: str) -> tuple[ast.FunctionDef, str]:
    normalized = textwrap.dedent(source).strip()
    try:
        tree = ast.parse(normalized)
    except SyntaxError as exc:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"{label} is invalid Python: {exc.msg}",
        ) from exc
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"{label} must contain exactly one synchronous function definition.",
        )
    function = tree.body[0]
    if function.decorator_list:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"{label} must not provide decorators; the server owns decorators.",
        )
    if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(function)):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"{label} must not contain imports.",
        )
    return function, normalized


def _added_parameter(operation: AddModelParameter) -> str:
    if not operation.name.isidentifier() or operation.name.startswith("_"):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "Added ModelParams names must be public Python identifiers.",
        )
    normalized = textwrap.dedent(operation.field_source).strip()
    try:
        tree = ast.parse(normalized)
    except SyntaxError as exc:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"Added ModelParams field is invalid Python: {exc.msg}",
        ) from exc
    if not (
        len(tree.body) == 1
        and isinstance(tree.body[0], ast.AnnAssign)
        and isinstance(tree.body[0].target, ast.Name)
        and tree.body[0].target.id == operation.name
        and tree.body[0].value is not None
    ):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "add_model_parameter must contain one matching annotated assignment with a default.",
        )
    return textwrap.indent(normalized, "    ") + "\n"


def _added_helper(operation: AddPrivateHelper) -> str:
    function, normalized = _single_function(
        operation.function_source,
        "Added private helper",
    )
    if (
        function.name != operation.function_name
        or not function.name.startswith("_")
        or function.name.startswith("__")
    ):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "Added helper name must match function_source and begin with one underscore.",
        )
    return normalized + "\n\n"


def _added_feature(operation: AddCadFeature) -> str:
    values = [
        operation.semantic_id,
        operation.function_name,
        operation.role,
        *operation.parameters,
        *operation.depends_on,
        *operation.search_keys,
    ]
    if any(not value.strip() for value in values):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "Added CAD feature identifiers and metadata must be non-empty.",
        )
    if (
        not operation.semantic_id.isidentifier()
        or not operation.function_name.isidentifier()
        or operation.function_name.startswith("_")
    ):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "Added CAD feature names must be public Python identifiers.",
        )
    if operation.semantic_id in operation.depends_on:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "An added CAD feature cannot depend on itself.",
        )
    function, normalized = _single_function(
        operation.function_source,
        "Added CAD feature",
    )
    if function.name != operation.function_name:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "Added CAD feature name must match function_source.",
        )
    return (
        f"# PART-START: {operation.semantic_id}\n"
        "@cad_part(\n"
        f"    semantic_id={operation.semantic_id!r},\n"
        f"    role={operation.role!r},\n"
        '    library="cadquery",\n'
        f"    parameters={_tuple_source(operation.parameters)},\n"
        f"    depends_on={_tuple_source(operation.depends_on)},\n"
        f"    search_keys={_tuple_source(operation.search_keys)},\n"
        ")\n"
        f"{normalized}\n"
        f"# PART-END: {operation.semantic_id}\n\n"
    )


def _source_layout(source: str):
    tree = ast.parse(source)
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    model_params = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ModelParams"
        ),
        None,
    )
    build_model = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "build_model"
        ),
        None,
    )
    if model_params is None or build_model is None:
        raise WorkflowFailure(
            "INVALID_ACCEPTED_SOURCE",
            "ModelParams and build_model are required for additive edits.",
        )
    parameter_nodes = [
        node
        for node in model_params.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    if not parameter_nodes:
        raise WorkflowFailure(
            "INVALID_ACCEPTED_SOURCE",
            "ModelParams must have at least one field.",
        )
    parameter_insert = offsets[
        (parameter_nodes[-1].end_lineno or parameter_nodes[-1].lineno)
    ]
    definition_insert = offsets[build_model.lineno - 1]
    names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    parameters = {node.target.id for node in parameter_nodes}
    semantic_ids: set[str] = set()
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
                    semantic_ids.add(keyword.value.value)
    return parameter_insert, definition_insert, names, parameters, semantic_ids


def validate_edit_plan(
    plan: EditPlan,
    *,
    part_id: str,
    semantic_ids: Iterable[str],
    spans: dict[str, TargetSpan],
) -> None:
    allowed_semantic_ids = set(semantic_ids)
    if not set(plan.target_semantic_ids).issubset(allowed_semantic_ids):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "The edit plan targets semantic IDs outside the resolved scope.",
        )
    seen: set[str] = set()
    selected_spans: list[TargetSpan] = []
    for operation in plan.operations:
        if isinstance(
            operation,
            (AddModelParameter, AddPrivateHelper, AddCadFeature),
        ):
            continue
        if operation.target_id in seen:
            raise WorkflowFailure(
                "INVALID_EDIT_PLAN",
                f'Target "{operation.target_id}" is modified more than once.',
            )
        seen.add(operation.target_id)
        span = spans.get(operation.target_id)
        if span is None or span.target.part_id != part_id:
            raise WorkflowFailure(
                "INVALID_EDIT_PLAN",
                f'Target "{operation.target_id}" is outside the allowed context.',
            )
        selected_spans.append(span)
        _operation_replacement(operation, span)

    ordered = sorted(selected_spans, key=lambda item: item.start)
    for previous, current in zip(ordered, ordered[1:]):
        if current.start < previous.end:
            raise WorkflowFailure(
                "INVALID_EDIT_PLAN",
                "Edit operations contain overlapping source regions.",
            )


def apply_edit_plan(
    source: str,
    *,
    expected_hash: str,
    part_id: str,
    semantic_ids: Iterable[str],
    plan: EditPlan,
    extra_function_names: Iterable[str] = (),
) -> CandidateSource:
    actual_hash = source_hash(source)
    if actual_hash != expected_hash:
        raise WorkflowFailure(
            "SOURCE_CHANGED",
            "The source changed after edit context was created.",
            details={"expected_hash": expected_hash, "actual_hash": actual_hash},
        )

    spans = collect_target_spans(
        source,
        part_id=part_id,
        semantic_ids=semantic_ids,
        extra_function_names=extra_function_names,
    )
    validate_edit_plan(
        plan,
        part_id=part_id,
        semantic_ids=semantic_ids,
        spans=spans,
    )

    changes: list[tuple[int, int, str, str]] = []
    changed_symbols: list[str] = []
    (
        parameter_insert,
        definition_insert,
        existing_names,
        existing_parameters,
        existing_semantic_ids,
    ) = _source_layout(source)
    added_names: set[str] = set()
    added_parameters: set[str] = set()
    added_semantic_ids: set[str] = set()
    parameter_source: list[str] = []
    definition_source: list[str] = []

    for operation in plan.operations:
        if isinstance(operation, AddModelParameter):
            if (
                operation.name in existing_parameters
                or operation.name in added_parameters
            ):
                raise WorkflowFailure(
                    "INVALID_EDIT_PLAN",
                    f'ModelParams field "{operation.name}" already exists.',
                )
            added_parameters.add(operation.name)
            parameter_source.append(_added_parameter(operation))
            changed_symbols.append(operation.name)
        elif isinstance(operation, AddPrivateHelper):
            if (
                operation.function_name in existing_names
                or operation.function_name in added_names
            ):
                raise WorkflowFailure(
                    "INVALID_EDIT_PLAN",
                    f'Function "{operation.function_name}" already exists.',
                )
            added_names.add(operation.function_name)
            definition_source.append(_added_helper(operation))
            changed_symbols.append(operation.function_name)
        elif isinstance(operation, AddCadFeature):
            if (
                operation.function_name in existing_names
                or operation.function_name in added_names
            ):
                raise WorkflowFailure(
                    "INVALID_EDIT_PLAN",
                    f'Function "{operation.function_name}" already exists.',
                )
            if (
                operation.semantic_id in existing_semantic_ids
                or operation.semantic_id in added_semantic_ids
            ):
                raise WorkflowFailure(
                    "INVALID_EDIT_PLAN",
                    f'Semantic ID "{operation.semantic_id}" already exists.',
                )
            added_names.add(operation.function_name)
            added_semantic_ids.add(operation.semantic_id)
            definition_source.append(_added_feature(operation))
            changed_symbols.extend(
                value
                for value in (operation.semantic_id, operation.function_name)
                if value not in changed_symbols
            )

    known_parameters = existing_parameters | added_parameters
    known_semantic_ids = existing_semantic_ids | added_semantic_ids
    for operation in plan.operations:
        if not isinstance(operation, AddCadFeature):
            continue
        unknown_parameters = sorted(set(operation.parameters) - known_parameters)
        unknown_dependencies = sorted(set(operation.depends_on) - known_semantic_ids)
        if unknown_parameters:
            raise WorkflowFailure(
                "INVALID_EDIT_PLAN",
                "Added CAD feature references unknown ModelParams fields: "
                + ", ".join(unknown_parameters),
            )
        if unknown_dependencies:
            raise WorkflowFailure(
                "INVALID_EDIT_PLAN",
                "Added CAD feature references unknown semantic IDs: "
                + ", ".join(unknown_dependencies),
            )

    for operation in plan.operations:
        if isinstance(
            operation,
            (AddModelParameter, AddPrivateHelper, AddCadFeature),
        ):
            continue
        span = spans[operation.target_id]
        changes.append(
            (
                span.start,
                span.end,
                _operation_replacement(operation, span),
                span.target.name,
            )
        )
        if span.target.name not in changed_symbols:
            changed_symbols.append(span.target.name)

    if parameter_source:
        changes.append(
            (
                parameter_insert,
                parameter_insert,
                "".join(parameter_source),
                "ModelParams",
            )
        )
    if definition_source:
        changes.append(
            (
                definition_insert,
                definition_insert,
                "".join(definition_source),
                "new_definitions",
            )
        )

    candidate = source
    for start, end, replacement, _name in sorted(changes, reverse=True):
        candidate = candidate[:start] + replacement + candidate[end:]
    try:
        ast.parse(candidate)
    except SyntaxError as exc:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"Applied candidate is invalid Python: {exc.msg}",
            details={"line": exc.lineno, "column": exc.offset},
        ) from exc

    return CandidateSource(
        content=candidate,
        content_hash=source_hash(candidate),
        base_hash=actual_hash,
        changed_symbols=changed_symbols,
        applied_operations=[
            operation.model_dump(mode="json") for operation in plan.operations
        ],
    )
