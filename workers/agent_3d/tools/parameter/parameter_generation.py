"""Shared ModelParams field generation, location, and usage primitives.

Used by the parameter tools (``parameter_tools.py``): ``_added_parameter_block``
and ``_replacement_field_source`` generate a field's marked block/replacement
source; ``_find_parameter_span``, ``_owned_parameter_marker_span``, and
``_parameter_usage_owners`` locate an existing field and its usages via narrow
AST-aware scanning -- the same pattern ``CreateFeatureTool`` and
``CreateCadPartTool`` use for their own family.
"""

from __future__ import annotations

import ast
import re
import textwrap

from workers.commons.semantic_graph import effective_parameter_references

from ...failures import WorkflowFailure

_OWNED_START = re.compile(r"^[ \t]*# CAD-AGENT-START: model_parameter:(?P<name>\w+)[ \t]*$")
_OWNED_END = re.compile(r"^[ \t]*# CAD-AGENT-END: model_parameter:(?P<name>\w+)[ \t]*$")


def _added_parameter_block(name: str, field_source: str) -> str:
    """Generate the CAD-AGENT-marked block for one added ModelParams field."""

    if not name.isidentifier() or name.startswith("_"):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "Added ModelParams names must be public Python identifiers.",
        )
    normalized = textwrap.dedent(field_source).strip()
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
        and tree.body[0].target.id == name
        and tree.body[0].value is not None
    ):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "add_model_parameter must contain one matching annotated assignment with a default.",
        )
    return (
        f"    # CAD-AGENT-START: model_parameter:{name}\n"
        f"{textwrap.indent(normalized, '    ')}\n"
        f"    # CAD-AGENT-END: model_parameter:{name}\n"
    )


def _replacement_field_source(field_name: str, replacement_source: str, indent: int) -> str:
    """Validate and reindent a replacement ModelParams field declaration."""

    replacement = textwrap.dedent(replacement_source).strip()
    if any(
        marker in replacement
        for marker in ("CAD-AGENT-START", "CAD-AGENT-END", "PART-START", "PART-END")
    ):
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "replace_parameter_field cannot supply server-owned provenance markers.",
        )
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
    if tree.body[0].target.id != field_name:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            "replace_parameter_field must preserve the targeted field name.",
        )
    return textwrap.indent(replacement, " " * indent) + "\n"


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _find_parameter_span(source: str, parameter_name: str) -> tuple[int, int, int] | None:
    """Locate an existing ModelParams field's bare declaration span.

    Returns ``(start, end, indent)`` covering exactly the one ``AnnAssign``
    line (including its leading indentation, excluded from re-splicing via
    ``indent``), or ``None`` if ``ModelParams`` or the field doesn't exist.
    """

    tree = ast.parse(source)
    model_params = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ModelParams"),
        None,
    )
    if model_params is None:
        return None
    offsets = _line_offsets(source)
    for node in model_params.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == parameter_name
        ):
            start = offsets[node.lineno - 1]
            end = offsets[node.end_lineno or node.lineno]
            return start, end, node.col_offset
    return None


def _owned_parameter_marker_span(source: str, parameter_name: str) -> tuple[int, int] | None:
    """Locate the full CAD-AGENT-marked block for an agent-created field.

    Returns the span from the ``# CAD-AGENT-START`` comment line through the
    ``# CAD-AGENT-END`` comment line (inclusive), or ``None`` if the field
    isn't marker-wrapped -- meaning it isn't owned/deletable.
    """

    offsets = _line_offsets(source)
    lines = source.splitlines(keepends=True)
    start_line = None
    for index, line in enumerate(lines):
        match = _OWNED_START.match(line)
        if match and match.group("name") == parameter_name:
            start_line = index
            break
    if start_line is None:
        return None
    for index in range(start_line, len(lines)):
        match = _OWNED_END.match(lines[index])
        if match and match.group("name") == parameter_name:
            return offsets[start_line], offsets[index + 1]
    return None


def _parameter_insertion_span(source: str) -> tuple[int, int]:
    """Compute where a new ModelParams field should be spliced in.

    Returns ``(start, end)`` such that ``source[:start] + new_block +
    source[end:]`` correctly appends the field. If ``ModelParams`` is
    currently an empty ``pass`` placeholder (``CreateCadPartTool``'s
    skeleton), that statement's span is returned so the new field replaces
    it entirely instead of leaving it dangling. Otherwise, this is a pure
    insertion point (``start == end``) placed after the *last* field's full
    agent-owned marker block when it has one -- deliberately not just after
    its bare ``AnnAssign`` line, which would splice a new field inside the
    previous field's own ``# CAD-AGENT-END`` marker.
    """

    tree = ast.parse(source)
    model_params = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ModelParams"),
        None,
    )
    if model_params is None:
        raise WorkflowFailure(
            "INVALID_ACCEPTED_SOURCE",
            "ModelParams is required to add a parameter.",
        )
    offsets = _line_offsets(source)
    last_statement = model_params.body[-1]

    if len(model_params.body) == 1 and isinstance(last_statement, ast.Pass):
        start = offsets[last_statement.lineno - 1]
        end = offsets[last_statement.end_lineno or last_statement.lineno]
        return start, end

    end = offsets[last_statement.end_lineno or last_statement.lineno]
    if isinstance(last_statement, ast.AnnAssign) and isinstance(last_statement.target, ast.Name):
        marker_span = _owned_parameter_marker_span(source, last_statement.target.id)
        if marker_span is not None:
            end = max(end, marker_span[1])
    return end, end


def _parameter_usage_owners(source: str, parameter_name: str) -> set[str]:
    """Return every function name still referencing ``parameter_name``.

    Checks both a feature's declared ``cad_part(parameters=(...))`` tuple and
    actual ``params.<field>`` usage in any function body (resolved
    transitively through private-helper calls via
    ``effective_parameter_references``), across every top-level function in
    the source -- not just one feature at a time.
    """

    tree = ast.parse(source)
    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    owners: set[str] = {
        name
        for name, used in effective_parameter_references(functions).items()
        if parameter_name in used
    }
    for node in functions:
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
                if keyword.arg != "parameters":
                    continue
                value = keyword.value
                if isinstance(value, (ast.Tuple, ast.List)) and any(
                    isinstance(element, ast.Constant) and element.value == parameter_name
                    for element in value.elts
                ):
                    owners.add(node.name)
    return owners
