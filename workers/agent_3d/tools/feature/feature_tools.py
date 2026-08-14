from __future__ import annotations

import ast
import textwrap
from typing import Literal

from pydantic import Field

from ...failures import WorkflowFailure
from ..hashing import source_hash
from ..base import AgentTool, StrictToolModel, ToolExecutionContext, ToolInputRejected
from .feature_generation import (
    AddCadFeature,
    _added_feature,
    _candidate_source_path,
    _dedupe_preserve_order,
    _feature_usage_owners,
    _find_feature,
    _is_public_identifier,
    _is_public_verb_noun_name,
    _is_snake_case_semantic_id,
    _normalize_body_source,
    _parse_body_function,
    _referenced_params,
    _reject_forbidden_body_nodes,
    _render_feature_decorator,
    _render_feature_definition,
    _source_layout,
    _walk_body,
    _PROVENANCE_MARKERS,
)


class FeatureDependencyInput(StrictToolModel):
    """One direct dependency the feature calls as a positional argument.

    ``semantic_id`` must already exist in the current part; ``argument_name`` is
    the local parameter name the generated function signature uses for the
    dependency's return value.
    """

    semantic_id: str = Field(min_length=1, max_length=200)
    argument_name: str = Field(min_length=1, max_length=100)


class CreateFeatureInput(StrictToolModel):
    """Strict, LLM-facing arguments for adding one semantic CAD feature.

    Project, part, run, and candidate identity come from ``ToolExecutionContext``,
    never from these fields. ``function_body`` contains only statements (no
    `def function_name(...):` line, no `@cad_part(...)` decorator, no
    docstring) -- the same convention as `edit_cad_build_model`'s
    `function_body`. Supplying a full function definition instead of just its
    body is rejected as forbidden syntax.
    """

    semantic_id: str = Field(min_length=1, max_length=200)
    function_name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=200)

    parameters: tuple[str, ...] = Field(
        default=(),
        max_length=32,
        description=(
            "Exactly the ModelParams field names your function_body reads as "
            "params.<name> -- no more, no fewer. A declared name the body "
            "never reads is rejected just as hard as a name the body reads "
            "but omits here. Each name must already exist as a ModelParams "
            "field; create any missing ones with create_parameter first."
        ),
    )
    dependencies: tuple[FeatureDependencyInput, ...] = Field(default=(), max_length=16)
    search_keys: tuple[str, ...] = Field(min_length=1, max_length=16)

    docstring: str = Field(min_length=1, max_length=500)
    function_body: str = Field(
        min_length=1,
        max_length=8_000,
        description=(
            "Only the function body statements, indented as they will appear "
            "inside `def function_name(params: ModelParams):`. Do not include "
            "the `def` line, the `@cad_part(...)` decorator, or a docstring -- "
            "those come from the other fields on this tool."
        ),
    )


class CreateFeatureOutput(StrictToolModel):
    """Structured result of a successful ``CreateFeatureTool`` invocation."""

    status: Literal["created"]
    semantic_id: str
    function_name: str
    candidate_id: str
    content_hash: str
    source_path: str
    parameters: tuple[str, ...]
    dependency_semantic_ids: tuple[str, ...]
    requires_assembly_wiring: Literal[True] = True
    summary: str


class CreateFeatureTool(AgentTool[CreateFeatureInput, CreateFeatureOutput]):
    """Add one new, isolated semantic CadQuery feature to the edit-scoped candidate.

    This tool creates exactly one new ``@cad_part``-decorated function and
    inserts it into the current candidate source, before ``build_model``. It
    modifies only the candidate identified by the execution context's
    ``candidate_id`` and never touches accepted source. Declared parameters and
    dependencies must already exist in the current part; this tool does not
    create model parameters and does not wire the new feature into
    ``build_model`` or any assembly. A separate assembly step is required
    before the new feature affects the final model.
    """

    tool_id = "create_feature"
    version = 1
    description = (
        "Create one new, isolated semantic CadQuery feature function and add it "
        "to the current edit-scoped candidate source. Parameters and "
        "dependencies referenced by the feature must already exist in the "
        "current part; this tool does not create model parameters. It does not "
        "modify build_model or wire the feature into the assembly -- a "
        "separate assembly step is required before the feature affects the "
        "final model."
    )
    input_model = CreateFeatureInput
    output_model = CreateFeatureOutput
    batch_group = None

    async def normalize_input(self, tool_input: CreateFeatureInput) -> CreateFeatureInput:
        search_keys = _dedupe_preserve_order([key.strip() for key in tool_input.search_keys])
        dependencies = tuple(
            FeatureDependencyInput(
                semantic_id=dependency.semantic_id.strip(),
                argument_name=dependency.argument_name.strip(),
            )
            for dependency in tool_input.dependencies
        )
        normalized_body = tool_input.function_body.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
        normalized_body = textwrap.dedent(normalized_body).strip("\n")

        return tool_input.model_copy(
            update={
                "semantic_id": tool_input.semantic_id.strip(),
                "function_name": tool_input.function_name.strip(),
                "role": tool_input.role.strip(),
                "parameters": tuple(parameter.strip() for parameter in tool_input.parameters),
                "dependencies": dependencies,
                "search_keys": search_keys,
                "docstring": tool_input.docstring.strip(),
                "function_body": normalized_body,
            }
        )

    async def validate_input(
        self,
        tool_input: CreateFeatureInput,
        context: ToolExecutionContext,
    ) -> None:
        if not context.candidate_id or not context.candidate_id.strip():
            raise ToolInputRejected(
                "An edit-scoped candidate is required to create a feature.",
                details={"reason": "missing_candidate"},
            )

        if not _is_snake_case_semantic_id(tool_input.semantic_id):
            raise ToolInputRejected(
                "semantic_id must be a lowercase snake_case identifier.",
                details={"field": "semantic_id"},
            )

        if not _is_public_verb_noun_name(tool_input.function_name):
            raise ToolInputRejected(
                "function_name must be a public snake_case verb_noun identifier.",
                details={"field": "function_name"},
            )

        if len(set(tool_input.parameters)) != len(tool_input.parameters):
            raise ToolInputRejected(
                "parameters must not contain duplicates.",
                details={"field": "parameters"},
            )
        for parameter in tool_input.parameters:
            if not _is_public_identifier(parameter):
                raise ToolInputRejected(
                    f'parameter "{parameter}" is not a valid ModelParams field name.',
                    details={"field": "parameters", "value": parameter},
                )

        dependency_ids = [dependency.semantic_id for dependency in tool_input.dependencies]
        if len(set(dependency_ids)) != len(dependency_ids):
            raise ToolInputRejected(
                "dependencies must not repeat the same semantic_id.",
                details={"field": "dependencies"},
            )
        argument_names = [dependency.argument_name for dependency in tool_input.dependencies]
        if len(set(argument_names)) != len(argument_names):
            raise ToolInputRejected(
                "dependencies must not repeat the same argument_name.",
                details={"field": "dependencies"},
            )
        for dependency in tool_input.dependencies:
            if not _is_snake_case_semantic_id(dependency.semantic_id):
                raise ToolInputRejected(
                    f'dependency semantic_id "{dependency.semantic_id}" is invalid.',
                    details={"field": "dependencies.semantic_id"},
                )
            if not _is_public_identifier(dependency.argument_name) or dependency.argument_name == "params":
                raise ToolInputRejected(
                    f'dependency argument_name "{dependency.argument_name}" is invalid.',
                    details={"field": "dependencies.argument_name"},
                )
        if tool_input.semantic_id in dependency_ids:
            raise ToolInputRejected(
                "A feature cannot directly depend on itself.",
                details={"reason": "self_dependency"},
            )

        if any(not key for key in tool_input.search_keys):
            raise ToolInputRejected(
                "search_keys must be non-empty strings.",
                details={"field": "search_keys"},
            )

        if '"""' in tool_input.docstring:
            raise ToolInputRejected(
                "docstring must not contain triple double quotes.",
                details={"field": "docstring"},
            )

        try:
            normalized_body, _ = _normalize_body_source(
                tool_input.function_body,
                label="function_body",
            )
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                str(exc),
                details={"field": "function_body", "code": exc.code},
            ) from exc

        for marker in _PROVENANCE_MARKERS:
            if marker in normalized_body:
                raise ToolInputRejected(
                    "function_body must not contain system-owned provenance markers.",
                    details={"field": "function_body", "marker": marker},
                )

        try:
            function_node = _parse_body_function(normalized_body)
        except SyntaxError as exc:
            raise ToolInputRejected(
                "function_body is invalid Python.",
                details={"field": "function_body", "message": exc.msg},
            ) from exc

        _reject_forbidden_body_nodes(function_node)

        if not any(isinstance(node, ast.Return) for node in _walk_body(function_node)):
            raise ToolInputRejected(
                "function_body must contain a return statement.",
                details={"field": "function_body", "reason": "missing_return"},
            )

        referenced = _referenced_params(function_node)
        declared = set(tool_input.parameters)
        if referenced != declared:
            raise ToolInputRejected(
                "Declared parameters must exactly match params.<field> references "
                "in function_body.",
                details={
                    "field": "parameters",
                    "missing_from_parameters": sorted(referenced - declared),
                    "unused_parameters": sorted(declared - referenced),
                },
            )

        try:
            source = context.services.repository.read_text(_candidate_source_path(context))
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                "The edit-scoped candidate source could not be read.",
                details={"reason": "candidate_unavailable", "code": exc.code},
            ) from exc

        try:
            _, _, existing_names, existing_parameters, existing_semantic_ids = _source_layout(source)
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                "The edit-scoped candidate source is not in an editable state.",
                details={"reason": "invalid_candidate", "code": exc.code},
            ) from exc

        if tool_input.semantic_id in existing_semantic_ids:
            raise ToolInputRejected(
                f'semantic_id "{tool_input.semantic_id}" already exists.',
                details={"field": "semantic_id", "reason": "duplicate"},
            )
        if tool_input.function_name in existing_names:
            raise ToolInputRejected(
                f'function_name "{tool_input.function_name}" already exists.',
                details={"field": "function_name", "reason": "duplicate"},
            )

        unknown_parameters = sorted(set(tool_input.parameters) - existing_parameters)
        if unknown_parameters:
            raise ToolInputRejected(
                "parameters reference unknown ModelParams fields: "
                + ", ".join(unknown_parameters),
                details={"field": "parameters", "unknown": unknown_parameters},
            )

        unknown_dependencies = sorted(set(dependency_ids) - existing_semantic_ids)
        if unknown_dependencies:
            raise ToolInputRejected(
                "dependencies reference unknown semantic IDs: "
                + ", ".join(unknown_dependencies),
                details={"field": "dependencies", "unknown": unknown_dependencies},
            )

    async def execute(
        self,
        tool_input: CreateFeatureInput,
        context: ToolExecutionContext,
    ) -> CreateFeatureOutput:
        assert context.candidate_id  # narrowed by validate_input

        path = _candidate_source_path(context)
        source = context.services.repository.read_text(path)

        dependency_ids = tuple(dependency.semantic_id for dependency in tool_input.dependencies)
        dependency_args = tuple(dependency.argument_name for dependency in tool_input.dependencies)

        signature_lines = [f"def {tool_input.function_name}(", "    params: ModelParams,"]
        signature_lines.extend(f"    {argument}," for argument in dependency_args)
        signature_lines.append("):")
        docstring_line = f'    """{tool_input.docstring}"""'
        indented_body = textwrap.indent(tool_input.function_body, "    ")
        function_source = "\n".join(signature_lines) + "\n" + docstring_line + "\n" + indented_body

        operation = AddCadFeature(
            semantic_id=tool_input.semantic_id,
            function_name=tool_input.function_name,
            role=tool_input.role,
            parameters=list(tool_input.parameters),
            depends_on=list(dependency_ids),
            search_keys=list(tool_input.search_keys),
            function_source=function_source,
        )
        block = _added_feature(operation)

        _, definition_insert, _, _, _ = _source_layout(source)
        candidate_source = source[:definition_insert] + block + source[definition_insert:]
        ast.parse(candidate_source)

        context.services.repository.write_text(path, candidate_source)

        return CreateFeatureOutput(
            status="created",
            semantic_id=tool_input.semantic_id,
            function_name=tool_input.function_name,
            candidate_id=context.candidate_id,
            content_hash=source_hash(candidate_source),
            source_path=path,
            parameters=tool_input.parameters,
            dependency_semantic_ids=dependency_ids,
            requires_assembly_wiring=True,
            summary=(
                f'Added semantic feature "{tool_input.semantic_id}" '
                f"({tool_input.function_name}) to the edit-scoped candidate. "
                "Assembly wiring into build_model is a separate step."
            ),
        )


class EditFeatureInput(StrictToolModel):
    """Strict, LLM-facing arguments for editing one existing semantic CAD feature.

    ``semantic_id`` and/or ``function_name`` identify the feature to edit --
    at least one is required, and neither is itself editable (renaming a
    feature is out of scope). Every other field is optional: a field left
    ``null`` keeps its current value; only the fields actually provided are
    changed. At least one edit field must be provided.
    """

    semantic_id: str | None = Field(default=None, min_length=1, max_length=200)
    function_name: str | None = Field(default=None, min_length=1, max_length=100)

    role: str | None = Field(default=None, min_length=1, max_length=200)
    parameters: tuple[str, ...] | None = Field(
        default=None,
        max_length=32,
        description=(
            "Exactly the ModelParams field names the resulting function_body "
            "reads as params.<name> -- no more, no fewer. A declared name the "
            "body never reads is rejected just as hard as a name the body "
            "reads but omits here. Each name must already exist as a "
            "ModelParams field; create any missing ones with create_parameter "
            "first."
        ),
    )
    dependencies: tuple[FeatureDependencyInput, ...] | None = Field(default=None, max_length=16)
    search_keys: tuple[str, ...] | None = Field(default=None, max_length=16)
    docstring: str | None = Field(default=None, min_length=1, max_length=500)
    function_body: str | None = Field(
        default=None,
        min_length=1,
        max_length=8_000,
        description=(
            "Only the function body statements, indented as they will appear "
            "inside `def function_name(params: ModelParams):`. Do not include "
            "the `def` line, the `@cad_part(...)` decorator, or a docstring."
        ),
    )


class EditFeatureOutput(StrictToolModel):
    """Structured result of a successful ``EditFeatureTool`` invocation."""

    status: Literal["edited"]
    semantic_id: str
    function_name: str
    candidate_id: str
    content_hash: str
    source_path: str
    updated_fields: tuple[str, ...]
    summary: str


_EDIT_FEATURE_FIELDS = ("role", "parameters", "dependencies", "search_keys", "docstring", "function_body")


class EditFeatureTool(AgentTool[EditFeatureInput, EditFeatureOutput]):
    """Change one or more properties of an existing semantic CAD feature.

    Identifies the feature by `semantic_id` and/or `function_name`. Only the
    fields actually provided (non-null) are changed; everything else is left
    byte-for-byte untouched. Declared parameters and dependencies must
    already exist, exactly like `create_feature`. Does not rename the
    feature, does not modify `build_model`, and modifies only the candidate
    identified by the execution context's `candidate_id`.
    """

    tool_id = "edit_feature"
    version = 1
    description = (
        "Change one or more properties (role, parameters, dependencies, "
        "search_keys, docstring, function_body) of an existing semantic CAD "
        "feature in the current edit-scoped candidate source, identified by "
        "semantic_id and/or function_name. Only the fields actually provided "
        "are changed. Parameters and dependencies must already exist. Does "
        "not rename the feature or modify build_model."
    )
    input_model = EditFeatureInput
    output_model = EditFeatureOutput
    batch_group = None

    async def normalize_input(self, tool_input: EditFeatureInput) -> EditFeatureInput:
        updates: dict[str, object] = {}
        if tool_input.semantic_id is not None:
            updates["semantic_id"] = tool_input.semantic_id.strip()
        if tool_input.function_name is not None:
            updates["function_name"] = tool_input.function_name.strip()
        if tool_input.role is not None:
            updates["role"] = tool_input.role.strip()
        if tool_input.parameters is not None:
            updates["parameters"] = tuple(parameter.strip() for parameter in tool_input.parameters)
        if tool_input.dependencies is not None:
            updates["dependencies"] = tuple(
                FeatureDependencyInput(
                    semantic_id=dependency.semantic_id.strip(),
                    argument_name=dependency.argument_name.strip(),
                )
                for dependency in tool_input.dependencies
            )
        if tool_input.search_keys is not None:
            updates["search_keys"] = _dedupe_preserve_order(
                [key.strip() for key in tool_input.search_keys]
            )
        if tool_input.docstring is not None:
            updates["docstring"] = tool_input.docstring.strip()
        if tool_input.function_body is not None:
            normalized_body = tool_input.function_body.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
            updates["function_body"] = textwrap.dedent(normalized_body).strip("\n")
        return tool_input.model_copy(update=updates) if updates else tool_input

    async def validate_input(
        self,
        tool_input: EditFeatureInput,
        context: ToolExecutionContext,
    ) -> None:
        if not context.candidate_id or not context.candidate_id.strip():
            raise ToolInputRejected(
                "An edit-scoped candidate is required to edit a feature.",
                details={"reason": "missing_candidate"},
            )

        if tool_input.semantic_id is None and tool_input.function_name is None:
            raise ToolInputRejected(
                "Either semantic_id or function_name is required to identify the feature.",
                details={"reason": "missing_identifier"},
            )

        if all(getattr(tool_input, field) is None for field in _EDIT_FEATURE_FIELDS):
            raise ToolInputRejected(
                "At least one field must be provided to edit.",
                details={"reason": "no_changes"},
            )

        if tool_input.parameters is not None:
            if len(set(tool_input.parameters)) != len(tool_input.parameters):
                raise ToolInputRejected(
                    "parameters must not contain duplicates.",
                    details={"field": "parameters"},
                )
            for parameter in tool_input.parameters:
                if not _is_public_identifier(parameter):
                    raise ToolInputRejected(
                        f'parameter "{parameter}" is not a valid ModelParams field name.',
                        details={"field": "parameters", "value": parameter},
                    )

        if tool_input.dependencies is not None:
            dependency_ids = [dependency.semantic_id for dependency in tool_input.dependencies]
            if len(set(dependency_ids)) != len(dependency_ids):
                raise ToolInputRejected(
                    "dependencies must not repeat the same semantic_id.",
                    details={"field": "dependencies"},
                )
            argument_names = [dependency.argument_name for dependency in tool_input.dependencies]
            if len(set(argument_names)) != len(argument_names):
                raise ToolInputRejected(
                    "dependencies must not repeat the same argument_name.",
                    details={"field": "dependencies"},
                )
            for dependency in tool_input.dependencies:
                if not _is_snake_case_semantic_id(dependency.semantic_id):
                    raise ToolInputRejected(
                        f'dependency semantic_id "{dependency.semantic_id}" is invalid.',
                        details={"field": "dependencies.semantic_id"},
                    )
                if (
                    not _is_public_identifier(dependency.argument_name)
                    or dependency.argument_name == "params"
                ):
                    raise ToolInputRejected(
                        f'dependency argument_name "{dependency.argument_name}" is invalid.',
                        details={"field": "dependencies.argument_name"},
                    )

        if tool_input.search_keys is not None and any(not key for key in tool_input.search_keys):
            raise ToolInputRejected(
                "search_keys must be non-empty strings.",
                details={"field": "search_keys"},
            )

        if tool_input.docstring is not None and '"""' in tool_input.docstring:
            raise ToolInputRejected(
                "docstring must not contain triple double quotes.",
                details={"field": "docstring"},
            )

        normalized_body = None
        if tool_input.function_body is not None:
            try:
                normalized_body, _ = _normalize_body_source(
                    tool_input.function_body,
                    label="function_body",
                )
            except WorkflowFailure as exc:
                raise ToolInputRejected(
                    str(exc),
                    details={"field": "function_body", "code": exc.code},
                ) from exc
            for marker in _PROVENANCE_MARKERS:
                if marker in normalized_body:
                    raise ToolInputRejected(
                        "function_body must not contain system-owned provenance markers.",
                        details={"field": "function_body", "marker": marker},
                    )
            try:
                function_node = _parse_body_function(normalized_body)
            except SyntaxError as exc:
                raise ToolInputRejected(
                    "function_body is invalid Python.",
                    details={"field": "function_body", "message": exc.msg},
                ) from exc
            _reject_forbidden_body_nodes(function_node)
            if not any(isinstance(node, ast.Return) for node in _walk_body(function_node)):
                raise ToolInputRejected(
                    "function_body must contain a return statement.",
                    details={"field": "function_body", "reason": "missing_return"},
                )

        try:
            source = context.services.repository.read_text(_candidate_source_path(context))
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                "The edit-scoped candidate source could not be read.",
                details={"reason": "candidate_unavailable", "code": exc.code},
            ) from exc

        location = _find_feature(
            source, semantic_id=tool_input.semantic_id, function_name=tool_input.function_name
        )
        if location is None:
            if tool_input.semantic_id is not None and tool_input.function_name is not None:
                by_semantic_id = _find_feature(source, semantic_id=tool_input.semantic_id)
                by_function_name = _find_feature(source, function_name=tool_input.function_name)
                if by_semantic_id is not None and by_function_name is not None:
                    raise ToolInputRejected(
                        "semantic_id and function_name identify different features.",
                        details={"reason": "conflicting_identifiers"},
                    )
            raise ToolInputRejected(
                "No feature matches the given semantic_id/function_name.",
                details={"reason": "not_found"},
            )

        try:
            _, _, _, existing_parameters, existing_semantic_ids = _source_layout(source)
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                "The edit-scoped candidate source is not in an editable state.",
                details={"reason": "invalid_candidate", "code": exc.code},
            ) from exc

        effective_parameters = (
            tool_input.parameters if tool_input.parameters is not None else location.parameters
        )
        unknown_parameters = sorted(set(effective_parameters) - existing_parameters)
        if unknown_parameters:
            raise ToolInputRejected(
                "parameters reference unknown ModelParams fields: " + ", ".join(unknown_parameters),
                details={"field": "parameters", "unknown": unknown_parameters},
            )

        if tool_input.dependencies is not None:
            effective_dependency_ids = tuple(
                dependency.semantic_id for dependency in tool_input.dependencies
            )
        else:
            effective_dependency_ids = location.depends_on
        if location.semantic_id in effective_dependency_ids:
            raise ToolInputRejected(
                "A feature cannot directly depend on itself.",
                details={"reason": "self_dependency"},
            )
        other_semantic_ids = existing_semantic_ids - {location.semantic_id}
        unknown_dependencies = sorted(set(effective_dependency_ids) - other_semantic_ids)
        if unknown_dependencies:
            raise ToolInputRejected(
                "dependencies reference unknown semantic IDs: " + ", ".join(unknown_dependencies),
                details={"field": "dependencies", "unknown": unknown_dependencies},
            )

        effective_body = normalized_body if normalized_body is not None else location.function_body
        effective_function_node = _parse_body_function(effective_body)
        referenced = _referenced_params(effective_function_node)
        if referenced != set(effective_parameters):
            raise ToolInputRejected(
                "Declared parameters must exactly match params.<field> references "
                "in function_body.",
                details={
                    "field": "parameters",
                    "missing_from_parameters": sorted(referenced - set(effective_parameters)),
                    "unused_parameters": sorted(set(effective_parameters) - referenced),
                },
            )

    async def execute(
        self,
        tool_input: EditFeatureInput,
        context: ToolExecutionContext,
    ) -> EditFeatureOutput:
        assert context.candidate_id  # narrowed by validate_input

        repository = context.services.repository
        path = _candidate_source_path(context)
        source = repository.read_text(path)

        location = _find_feature(
            source, semantic_id=tool_input.semantic_id, function_name=tool_input.function_name
        )

        updated_fields: list[str] = []
        spans: list[tuple[int, int, str]] = []

        metadata_changed = any(
            value is not None
            for value in (
                tool_input.role,
                tool_input.parameters,
                tool_input.dependencies,
                tool_input.search_keys,
            )
        )
        if metadata_changed:
            effective_role = tool_input.role if tool_input.role is not None else location.role
            effective_parameters = (
                tool_input.parameters if tool_input.parameters is not None else location.parameters
            )
            effective_depends_on = (
                tuple(dependency.semantic_id for dependency in tool_input.dependencies)
                if tool_input.dependencies is not None
                else location.depends_on
            )
            effective_search_keys = (
                tool_input.search_keys if tool_input.search_keys is not None else location.search_keys
            )
            decorator_block = _render_feature_decorator(
                location.decorator_indent,
                location.semantic_id,
                effective_role,
                effective_parameters,
                effective_depends_on,
                effective_search_keys,
            )
            spans.append((location.decorator_start, location.decorator_end, decorator_block))
            updated_fields.extend(
                field
                for field, value in (
                    ("role", tool_input.role),
                    ("parameters", tool_input.parameters),
                    ("dependencies", tool_input.dependencies),
                    ("search_keys", tool_input.search_keys),
                )
                if value is not None
            )

        definition_changed = any(
            value is not None
            for value in (tool_input.dependencies, tool_input.docstring, tool_input.function_body)
        )
        if definition_changed:
            effective_dependency_args = (
                tuple(dependency.argument_name for dependency in tool_input.dependencies)
                if tool_input.dependencies is not None
                else location.dependency_argument_names
            )
            effective_docstring = (
                tool_input.docstring if tool_input.docstring is not None else location.docstring
            )
            effective_function_body = (
                tool_input.function_body if tool_input.function_body is not None else location.function_body
            )
            definition_block = _render_feature_definition(
                location.function_name,
                effective_dependency_args,
                effective_docstring,
                effective_function_body,
            )
            spans.append((location.definition_start, location.definition_end, definition_block))
            for field in ("docstring", "function_body"):
                if getattr(tool_input, field) is not None and field not in updated_fields:
                    updated_fields.append(field)

        candidate_source = source
        for start, end, replacement in sorted(spans, key=lambda item: item[0], reverse=True):
            candidate_source = candidate_source[:start] + replacement + candidate_source[end:]
        ast.parse(candidate_source)

        repository.write_text(path, candidate_source)

        return EditFeatureOutput(
            status="edited",
            semantic_id=location.semantic_id,
            function_name=location.function_name,
            candidate_id=context.candidate_id,
            content_hash=source_hash(candidate_source),
            source_path=path,
            updated_fields=tuple(updated_fields),
            summary=(
                f'Edited semantic feature "{location.semantic_id}" '
                f"({location.function_name}): updated {', '.join(updated_fields)}."
            ),
        )


class DeleteFeatureInput(StrictToolModel):
    """Strict, LLM-facing arguments for deleting one existing semantic CAD feature.

    At least one of `semantic_id`/`function_name` is required to identify
    the feature.
    """

    semantic_id: str | None = Field(default=None, min_length=1, max_length=200)
    function_name: str | None = Field(default=None, min_length=1, max_length=100)


class DeleteFeatureOutput(StrictToolModel):
    """Structured result of a successful ``DeleteFeatureTool`` invocation."""

    status: Literal["deleted"]
    semantic_id: str
    function_name: str
    candidate_id: str
    content_hash: str
    source_path: str
    summary: str


class DeleteFeatureTool(AgentTool[DeleteFeatureInput, DeleteFeatureOutput]):
    """Delete one agent-created, unreferenced semantic CAD feature.

    Identifies the feature by `semantic_id` and/or `function_name`. Only
    works on features that were themselves created by an agent (wrapped in
    `# PART-START`/`# PART-END` provenance markers) -- hand-authored
    features cannot be deleted this way. Also refuses to delete a feature
    that is still referenced, either by another feature's declared
    `depends_on` or by a direct call anywhere (including `build_model`).
    Modifies only the candidate identified by the execution context's
    `candidate_id` and never touches accepted source.
    """

    tool_id = "delete_feature"
    version = 1
    description = (
        "Delete one existing, agent-created semantic CAD feature from the "
        "current edit-scoped candidate source, identified by semantic_id "
        "and/or function_name. Only agent-created (provenance-marked) "
        "features may be deleted, and only if no other feature or "
        "build_model still depends on or calls it -- remove that usage "
        "first."
    )
    input_model = DeleteFeatureInput
    output_model = DeleteFeatureOutput
    batch_group = None

    async def normalize_input(self, tool_input: DeleteFeatureInput) -> DeleteFeatureInput:
        return tool_input.model_copy(
            update={
                "semantic_id": tool_input.semantic_id.strip() if tool_input.semantic_id is not None else None,
                "function_name": (
                    tool_input.function_name.strip() if tool_input.function_name is not None else None
                ),
            }
        )

    async def validate_input(
        self,
        tool_input: DeleteFeatureInput,
        context: ToolExecutionContext,
    ) -> None:
        if not context.candidate_id or not context.candidate_id.strip():
            raise ToolInputRejected(
                "An edit-scoped candidate is required to delete a feature.",
                details={"reason": "missing_candidate"},
            )

        if tool_input.semantic_id is None and tool_input.function_name is None:
            raise ToolInputRejected(
                "Either semantic_id or function_name is required to identify the feature.",
                details={"reason": "missing_identifier"},
            )

        try:
            source = context.services.repository.read_text(_candidate_source_path(context))
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                "The edit-scoped candidate source could not be read.",
                details={"reason": "candidate_unavailable", "code": exc.code},
            ) from exc

        location = _find_feature(
            source, semantic_id=tool_input.semantic_id, function_name=tool_input.function_name
        )
        if location is None:
            if tool_input.semantic_id is not None and tool_input.function_name is not None:
                by_semantic_id = _find_feature(source, semantic_id=tool_input.semantic_id)
                by_function_name = _find_feature(source, function_name=tool_input.function_name)
                if by_semantic_id is not None and by_function_name is not None:
                    raise ToolInputRejected(
                        "semantic_id and function_name identify different features.",
                        details={"reason": "conflicting_identifiers"},
                    )
            raise ToolInputRejected(
                "No feature matches the given semantic_id/function_name.",
                details={"reason": "not_found"},
            )

        if location.marker_start is None:
            raise ToolInputRejected(
                f'Feature "{location.semantic_id}" was not created by an agent '
                "and cannot be deleted this way.",
                details={"field": "semantic_id", "reason": "not_owned"},
            )

        owners = _feature_usage_owners(
            source, semantic_id=location.semantic_id, function_name=location.function_name
        )
        if owners:
            raise ToolInputRejected(
                f'Feature "{location.semantic_id}" is still referenced and cannot be deleted.',
                details={
                    "field": "semantic_id",
                    "reason": "in_use",
                    "referenced_by": sorted(owners),
                },
            )

    async def execute(
        self,
        tool_input: DeleteFeatureInput,
        context: ToolExecutionContext,
    ) -> DeleteFeatureOutput:
        assert context.candidate_id  # narrowed by validate_input

        repository = context.services.repository
        path = _candidate_source_path(context)
        source = repository.read_text(path)

        location = _find_feature(
            source, semantic_id=tool_input.semantic_id, function_name=tool_input.function_name
        )

        candidate_source = source[: location.marker_start] + source[location.marker_end :]
        ast.parse(candidate_source)

        repository.write_text(path, candidate_source)

        return DeleteFeatureOutput(
            status="deleted",
            semantic_id=location.semantic_id,
            function_name=location.function_name,
            candidate_id=context.candidate_id,
            content_hash=source_hash(candidate_source),
            source_path=path,
            summary=f'Deleted semantic feature "{location.semantic_id}" ({location.function_name}) from the edit-scoped candidate.',
        )
