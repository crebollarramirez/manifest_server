"""Shared CAD feature generation, location, validation, and layout primitives.

The full shared toolkit for the feature family: tuple/decorator/body
generation and validation, source-layout scanning, and the candidate-path
convention. Used by every tool in `tools/feature/` (`CreateFeatureTool`,
`EditFeatureTool`, `DeleteFeatureTool`) and by `tools/build_model/` (which
reuses the function-body validation helpers for `build_model`'s body) -- so
there is exactly one implementation of this logic.
``_candidate_source_path`` is the one non-feature-specific helper here -- the
edit-scoped candidate path convention, shared by every tool in this package
that mutates a part's candidate source.
"""

from __future__ import annotations

import ast
import keyword
import re
import textwrap
from dataclasses import dataclass

from pydantic import Field

from ...failures import WorkflowFailure
from ..base import StrictToolModel, ToolExecutionContext, ToolInputRejected


class AddCadFeature(StrictToolModel):
    """Parameters for generating one added semantic CAD feature's marked block."""

    semantic_id: str
    function_name: str
    role: str
    parameters: list[str]
    depends_on: list[str]
    search_keys: list[str] = Field(min_length=1)
    function_source: str

_SEMANTIC_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_IDENTIFIER_TOKEN_RE = re.compile(r"^[a-z][a-z0-9]*$")

_FEATURE_VERBS = frozenset(
    {
        "add",
        "attach",
        "build",
        "chamfer",
        "create",
        "cut",
        "drill",
        "extrude",
        "fillet",
        "mount",
        "pattern",
        "round",
        "shell",
        "sweep",
        "taper",
        "thread",
        "trim",
    }
)

_FORBIDDEN_CALL_NAMES = frozenset(
    {"eval", "exec", "compile", "__import__", "open", "exit", "quit", "globals", "locals", "vars", "input"}
)

_FORBIDDEN_MODULE_ROOTS = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "shutil",
        "requests",
        "urllib",
        "urllib2",
        "importlib",
        "pathlib",
        "sqlite3",
        "psycopg2",
        "boto3",
        "http",
        "ftplib",
        "smtplib",
        "multiprocessing",
        "threading",
        "ctypes",
        "pickle",
    }
)

_PROVENANCE_MARKERS = ("PART-START", "PART-END", "CAD-AGENT-START", "CAD-AGENT-END")

_PART_START_RE = re.compile(r"^[ \t]*# PART-START: (?P<name>[a-zA-Z_][a-zA-Z0-9_]*)[ \t]*$")
_PART_END_RE = re.compile(r"^[ \t]*# PART-END: (?P<name>[a-zA-Z_][a-zA-Z0-9_]*)[ \t]*$")


def _is_snake_case_semantic_id(value: str) -> bool:
    return bool(_SEMANTIC_ID_RE.fullmatch(value)) and not keyword.iskeyword(value)


def _is_public_verb_noun_name(value: str) -> bool:
    """A function name must be public snake_case with a recognized leading verb.

    ``cut_mounting_holes`` and ``build_holder_floor`` are representative of the
    naming style already used by generated CAD feature functions in this repo.
    """

    if not value or value.startswith("_") or keyword.iskeyword(value):
        return False
    tokens = value.split("_")
    if len(tokens) < 2:
        return False
    if not all(_IDENTIFIER_TOKEN_RE.fullmatch(token) for token in tokens):
        return False
    return tokens[0] in _FEATURE_VERBS


def _is_public_identifier(value: str) -> bool:
    return value.isidentifier() and not value.startswith("_") and not keyword.iskeyword(value)


def _dedupe_preserve_order(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _tuple_source(values: list[str]) -> str:
    if not values:
        return "()"
    rendered = ", ".join(repr(value) for value in values)
    if len(values) == 1:
        rendered += ","
    return f"({rendered})"


def _normalize_body_source(source: str, *, label: str) -> tuple[str, bool]:
    """Normalize only unambiguous one-level indentation mistakes."""
    normalized = source.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    normalized = textwrap.dedent(normalized).strip("\n")
    if not normalized.strip():
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"{label} cannot use an empty body.",
        )

    def parses(body: str) -> bool:
        try:
            ast.parse(f"def _candidate():\n{textwrap.indent(body, '    ')}\n")
            return True
        except SyntaxError:
            return False

    if parses(normalized):
        return normalized, normalized != source.strip("\n")

    lines = normalized.split("\n")
    nonempty = [line for line in lines if line.strip()]
    if nonempty and not nonempty[0].startswith((" ", "\t")):
        later_indents = [len(line) - len(line.lstrip(" ")) for line in nonempty[1:]]
        positive = [indent for indent in later_indents if indent > 0]
        if positive and min(positive) == 4:
            repaired = [lines[0]]
            repaired.extend(
                line[4:] if line.strip() and line.startswith("    ") else line
                for line in lines[1:]
            )
            repaired_source = "\n".join(repaired)
            if parses(repaired_source):
                return repaired_source, True

    raise WorkflowFailure(
        "INVALID_EDIT_PLAN",
        f"{label} is invalid Python or has ambiguous indentation.",
    )


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


def _parse_body_function(normalized_body: str) -> ast.FunctionDef:
    """Parse normalized statements as the body of a temporary synthetic function."""

    wrapped = f"def _candidate_feature():\n{textwrap.indent(normalized_body, '    ')}\n"
    tree = ast.parse(wrapped)
    return tree.body[0]  # type: ignore[return-value]


def _walk_body(function_node: ast.FunctionDef):
    """Walk statements inside a synthetic wrapper, excluding the wrapper itself."""

    for statement in function_node.body:
        yield from ast.walk(statement)


def _reject_forbidden_body_nodes(function_node: ast.FunctionDef) -> None:
    for node in _walk_body(function_node):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            raise ToolInputRejected(
                "function_body must not contain imports or nested function/class definitions.",
                details={"reason": "forbidden_syntax", "node": type(node).__name__},
            )
        if isinstance(node, ast.Call):
            called = node.func
            if isinstance(called, ast.Name) and called.id in _FORBIDDEN_CALL_NAMES:
                raise ToolInputRejected(
                    f'function_body must not call "{called.id}".',
                    details={"reason": "forbidden_call", "name": called.id},
                )
            if (
                isinstance(called, ast.Attribute)
                and isinstance(called.value, ast.Name)
                and called.value.id in _FORBIDDEN_MODULE_ROOTS
            ):
                raise ToolInputRejected(
                    f'function_body must not use module "{called.value.id}".',
                    details={"reason": "forbidden_module", "module": called.value.id},
                )
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_MODULE_ROOTS:
            raise ToolInputRejected(
                f'function_body must not reference "{node.id}".',
                details={"reason": "forbidden_module", "module": node.id},
            )


def _referenced_params(function_node: ast.FunctionDef) -> set[str]:
    return {
        node.attr
        for node in _walk_body(function_node)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "params"
    }


def _added_feature(operation: AddCadFeature) -> str:
    """Generate the PART-marked block for one added semantic CAD feature."""

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
    offsets = _line_offsets(source)
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
    if parameter_nodes:
        parameter_insert = offsets[
            (parameter_nodes[-1].end_lineno or parameter_nodes[-1].lineno)
        ]
    else:
        # ModelParams has no fields yet (e.g. a freshly created part's
        # skeleton). A Python class body can never be truly empty, so there
        # is always at least one statement (such as a bare ``pass``) to
        # anchor the insertion point for the first field added later.
        last_statement = model_params.body[-1]
        parameter_insert = offsets[
            (last_statement.end_lineno or last_statement.lineno)
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
            for keyword_node in decorator.keywords:
                if (
                    keyword_node.arg == "semantic_id"
                    and isinstance(keyword_node.value, ast.Constant)
                    and isinstance(keyword_node.value.value, str)
                ):
                    semantic_ids.add(keyword_node.value.value)
    return parameter_insert, definition_insert, names, parameters, semantic_ids


def _candidate_source_path(context: ToolExecutionContext) -> str:
    return f"{context.project_id}/candidates/cad/{context.part_id}/{context.candidate_id}/model.py"


@dataclass(frozen=True)
class FeatureLocation:
    """Where one existing @cad_part feature lives, and its current values.

    Two independently editable spans: ``decorator_*`` covers just the
    `@cad_part(...)` block (role/parameters/depends_on/search_keys).
    ``definition_*`` covers the signature line through the last body
    statement together -- dependencies affect the signature (each becomes a
    positional argument after ``params``), so a dependency change must
    regenerate the signature alongside the docstring/body, not just the
    decorator.
    """

    semantic_id: str
    function_name: str
    decorator_start: int
    decorator_end: int
    decorator_indent: int
    definition_start: int
    definition_end: int
    marker_start: int | None
    marker_end: int | None
    role: str
    parameters: tuple[str, ...]
    depends_on: tuple[str, ...]
    dependency_argument_names: tuple[str, ...]
    search_keys: tuple[str, ...]
    docstring: str | None
    function_body: str


def _feature_marker_span(source: str, semantic_id: str) -> tuple[int, int] | None:
    """Locate the full PART-marked block for an agent-created feature.

    Returns the span from `# PART-START` through `# PART-END` (inclusive),
    plus one trailing blank line if present (matching `_added_feature`'s own
    trailing blank line, so a clean delete doesn't leave extra blank lines
    behind). Returns `None` if the feature isn't marker-wrapped -- meaning it
    isn't owned/deletable.
    """

    offsets = _line_offsets(source)
    lines = source.splitlines(keepends=True)
    start_line = None
    for index, line in enumerate(lines):
        match = _PART_START_RE.match(line)
        if match and match.group("name") == semantic_id:
            start_line = index
            break
    if start_line is None:
        return None
    for index in range(start_line, len(lines)):
        match = _PART_END_RE.match(lines[index])
        if match and match.group("name") == semantic_id:
            end_line = index + 1
            if end_line < len(lines) and not lines[end_line].strip():
                end_line += 1
            return offsets[start_line], offsets[end_line]
    return None


def _find_feature(
    source: str,
    *,
    semantic_id: str | None = None,
    function_name: str | None = None,
) -> FeatureLocation | None:
    """Locate one @cad_part feature matching every identifier given.

    Whichever of `semantic_id`/`function_name` are provided must *all* match
    the same feature; if neither is provided, nothing matches. Returns
    `None` if no single feature satisfies every given identifier -- callers
    that pass both should independently probe each identifier alone to
    distinguish "doesn't exist at all" from "identifiers point to different
    features" for a clearer rejection message.
    """

    if semantic_id is None and function_name is None:
        return None

    tree = ast.parse(source)
    offsets = _line_offsets(source)
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        decorator = next(
            (
                d
                for d in node.decorator_list
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "cad_part"
            ),
            None,
        )
        if decorator is None:
            continue

        found_semantic_id: str | None = None
        role = ""
        parameters: tuple[str, ...] = ()
        depends_on: tuple[str, ...] = ()
        search_keys: tuple[str, ...] = ()
        for kw in decorator.keywords:
            if kw.arg == "semantic_id" and isinstance(kw.value, ast.Constant):
                found_semantic_id = kw.value.value
            elif kw.arg == "role" and isinstance(kw.value, ast.Constant):
                role = kw.value.value
            elif kw.arg == "parameters" and isinstance(kw.value, (ast.Tuple, ast.List)):
                parameters = tuple(e.value for e in kw.value.elts if isinstance(e, ast.Constant))
            elif kw.arg == "depends_on" and isinstance(kw.value, (ast.Tuple, ast.List)):
                depends_on = tuple(e.value for e in kw.value.elts if isinstance(e, ast.Constant))
            elif kw.arg == "search_keys" and isinstance(kw.value, (ast.Tuple, ast.List)):
                search_keys = tuple(e.value for e in kw.value.elts if isinstance(e, ast.Constant))

        if found_semantic_id is None:
            continue
        if semantic_id is not None and found_semantic_id != semantic_id:
            continue
        if function_name is not None and node.name != function_name:
            continue
        if not node.body:
            continue

        last_stmt = node.body[-1]
        docstring = None
        body_statements = node.body
        if (
            isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            docstring = node.body[0].value.value
            body_statements = node.body[1:]
        if not body_statements:
            continue

        definition_end = offsets[last_stmt.end_lineno or last_stmt.lineno]
        statements_start = offsets[body_statements[0].lineno - 1]
        function_body_text = textwrap.dedent(source[statements_start:definition_end])
        dependency_argument_names = tuple(arg.arg for arg in node.args.args[1:])

        marker_span = _feature_marker_span(source, found_semantic_id)

        return FeatureLocation(
            semantic_id=found_semantic_id,
            function_name=node.name,
            decorator_start=offsets[decorator.lineno - 1],
            decorator_end=offsets[decorator.end_lineno],
            decorator_indent=node.col_offset,
            definition_start=offsets[node.lineno - 1],
            definition_end=definition_end,
            marker_start=marker_span[0] if marker_span else None,
            marker_end=marker_span[1] if marker_span else None,
            role=role,
            parameters=parameters,
            depends_on=depends_on,
            dependency_argument_names=dependency_argument_names,
            search_keys=search_keys,
            docstring=docstring,
            function_body=function_body_text,
        )
    return None


def _render_feature_decorator(
    indent: int,
    semantic_id: str,
    role: str,
    parameters: tuple[str, ...],
    depends_on: tuple[str, ...],
    search_keys: tuple[str, ...],
) -> str:
    """Regenerate a feature's `@cad_part(...)` decorator, same field order as `_added_feature`."""

    prefix = " " * indent
    field_prefix = prefix + "    "
    return (
        f"{prefix}@cad_part(\n"
        f"{field_prefix}semantic_id={semantic_id!r},\n"
        f"{field_prefix}role={role!r},\n"
        f'{field_prefix}library="cadquery",\n'
        f"{field_prefix}parameters={_tuple_source(list(parameters))},\n"
        f"{field_prefix}depends_on={_tuple_source(list(depends_on))},\n"
        f"{field_prefix}search_keys={_tuple_source(list(search_keys))},\n"
        f"{prefix})\n"
    )


def _render_feature_definition(
    function_name: str,
    dependency_argument_names: tuple[str, ...],
    docstring: str | None,
    function_body: str,
) -> str:
    """Regenerate a feature's signature, docstring (if any), and body statements.

    Mirrors ``CreateFeatureTool.execute()``'s own generation exactly (same
    multi-line signature form, same docstring-then-body layout) so an edited
    feature is indistinguishable in style from a newly created one. Top-level
    features are always at column 0, so there is no ``indent`` parameter.
    """

    signature_lines = [f"def {function_name}(", "    params: ModelParams,"]
    signature_lines.extend(f"    {argument}," for argument in dependency_argument_names)
    signature_lines.append("):")
    lines = ["\n".join(signature_lines)]
    if docstring is not None:
        lines.append(f'    """{docstring}"""')
    lines.append(textwrap.indent(function_body, "    "))
    return "\n".join(lines) + "\n"


def _feature_usage_owners(source: str, *, semantic_id: str, function_name: str) -> set[str]:
    """Return every other function name that still depends on or calls this feature.

    Checks both a dependent feature's declared `cad_part(depends_on=(...))`
    tuple and any direct call to `function_name` in any other function's
    body (including `build_model`).
    """

    tree = ast.parse(source)
    owners: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name == function_name:
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "cad_part"
            ):
                continue
            for kw in decorator.keywords:
                if kw.arg != "depends_on":
                    continue
                value = kw.value
                if isinstance(value, (ast.Tuple, ast.List)) and any(
                    isinstance(e, ast.Constant) and e.value == semantic_id for e in value.elts
                ):
                    owners.add(node.name)
        for call_node in ast.walk(node):
            if (
                isinstance(call_node, ast.Call)
                and isinstance(call_node.func, ast.Name)
                and call_node.func.id == function_name
            ):
                owners.add(node.name)
    return owners


def _build_model_body_span(source: str) -> tuple[int, int, int]:
    """Locate `build_model`'s body span (statements only, not the `def` line)."""

    tree = ast.parse(source)
    build_model = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_model"),
        None,
    )
    if build_model is None or not build_model.body:
        raise WorkflowFailure(
            "INVALID_ACCEPTED_SOURCE",
            "build_model is required to edit its body.",
        )
    offsets = _line_offsets(source)
    first = build_model.body[0]
    last = build_model.body[-1]
    return offsets[first.lineno - 1], offsets[last.end_lineno or last.lineno], first.col_offset
