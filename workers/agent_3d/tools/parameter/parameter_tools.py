from __future__ import annotations

import ast
import keyword
import textwrap
from typing import Literal

from pydantic import Field

from ...failures import WorkflowFailure
from ..base import (
    BATCH_GROUP_PARAMETER_CREATE,
    AgentTool,
    StrictToolModel,
    ToolExecutionContext,
    ToolInputRejected,
)
from ..hashing import source_hash
from ..feature.feature_generation import _candidate_source_path, _source_layout
from .parameter_generation import (
    _added_parameter_block,
    _find_parameter_span,
    _owned_parameter_marker_span,
    _parameter_insertion_span,
    _parameter_usage_owners,
    _replacement_field_source,
)


def _is_public_identifier(value: str) -> bool:
    return value.isidentifier() and not value.startswith("_") and not keyword.iskeyword(value)


def _literal_default(field_text: str) -> float | None:
    """Best-effort extraction of a field declaration's numeric default.

    Returns ``None`` if the default isn't a simple numeric literal (every
    fixture in this codebase uses one, but a hand-authored field could in
    principle use an expression).
    """

    try:
        tree = ast.parse(textwrap.dedent(field_text).strip())
    except SyntaxError:
        return None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.AnnAssign):
        return None
    value_node = tree.body[0].value
    if value_node is None:
        return None
    try:
        value = ast.literal_eval(value_node)
    except (ValueError, TypeError):
        return None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class CreateParameterInput(StrictToolModel):
    """Strict, LLM-facing arguments for adding one new ModelParams field."""

    parameter_name: str = Field(min_length=1, max_length=200)
    value: float = Field(gt=0)


class CreateParameterOutput(StrictToolModel):
    """Structured result of a successful ``CreateParameterTool`` invocation."""

    status: Literal["created"]
    parameter_name: str
    value: float
    candidate_id: str
    source_path: str
    content_hash: str
    summary: str


class EditParameterInput(StrictToolModel):
    """Strict, LLM-facing arguments for changing an existing field's default.

    ``parameter_name`` identifies the existing field and is not renameable --
    only its default value changes.
    """

    parameter_name: str = Field(min_length=1, max_length=200)
    value: float = Field(gt=0)


class EditParameterOutput(StrictToolModel):
    """Structured result of a successful ``EditParameterTool`` invocation."""

    status: Literal["edited"]
    parameter_name: str
    previous_value: float | None
    value: float
    candidate_id: str
    source_path: str
    content_hash: str
    summary: str


class DeleteParameterInput(StrictToolModel):
    """Strict, LLM-facing arguments for deleting an existing ModelParams field."""

    parameter_name: str = Field(min_length=1, max_length=200)


class DeleteParameterOutput(StrictToolModel):
    """Structured result of a successful ``DeleteParameterTool`` invocation."""

    status: Literal["deleted"]
    parameter_name: str
    removed_value: float | None
    candidate_id: str
    source_path: str
    content_hash: str
    summary: str


class CreateParameterTool(AgentTool[CreateParameterInput, CreateParameterOutput]):
    """Add one new ModelParams field to the current edit-scoped candidate.

    This tool creates exactly one new, agent-owned `ModelParams` field
    (wrapped in `# CAD-AGENT-START`/`# CAD-AGENT-END` provenance markers) and
    inserts it into the current candidate source. It modifies only the
    candidate identified by the execution context's `candidate_id` and never
    touches accepted source. It does not create or modify semantic features
    -- use `create_feature` to declare and use the new parameter.
    """

    tool_id = "create_parameter"
    version = 1
    description = (
        "Add one new ModelParams field (name and numeric default) to the "
        "current edit-scoped candidate source. The field name must not "
        "already exist. This tool does not create or modify semantic "
        "features -- use create_feature to declare and use the new "
        "parameter afterward."
    )
    input_model = CreateParameterInput
    output_model = CreateParameterOutput
    # Several fields may be created in one round. Each call re-reads the
    # candidate before splicing its own marker-wrapped field, and the
    # insertion span is marker-aware, so batched calls append cleanly in the
    # order they were issued. Distinct field names cannot conflict, and a
    # repeated one is caught by the duplicate check, which also reads live
    # source and therefore sees an earlier call in the same batch.
    batch_group = BATCH_GROUP_PARAMETER_CREATE

    async def normalize_input(self, tool_input: CreateParameterInput) -> CreateParameterInput:
        return tool_input.model_copy(update={"parameter_name": tool_input.parameter_name.strip()})

    async def validate_input(
        self,
        tool_input: CreateParameterInput,
        context: ToolExecutionContext,
    ) -> None:
        if not context.candidate_id or not context.candidate_id.strip():
            raise ToolInputRejected(
                "An edit-scoped candidate is required to create a parameter.",
                details={"reason": "missing_candidate"},
            )

        if not _is_public_identifier(tool_input.parameter_name):
            raise ToolInputRejected(
                "parameter_name must be a public Python identifier.",
                details={"field": "parameter_name"},
            )

        try:
            source = context.services.repository.read_text(_candidate_source_path(context))
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                "The edit-scoped candidate source could not be read.",
                details={"reason": "candidate_unavailable", "code": exc.code},
            ) from exc

        try:
            _, _, _, existing_parameters, _ = _source_layout(source)
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                "The edit-scoped candidate source is not in an editable state.",
                details={"reason": "invalid_candidate", "code": exc.code},
            ) from exc

        if tool_input.parameter_name in existing_parameters:
            raise ToolInputRejected(
                f'parameter_name "{tool_input.parameter_name}" already exists.',
                details={"field": "parameter_name", "reason": "duplicate"},
            )

    async def execute(
        self,
        tool_input: CreateParameterInput,
        context: ToolExecutionContext,
    ) -> CreateParameterOutput:
        assert context.candidate_id  # narrowed by validate_input

        repository = context.services.repository
        path = _candidate_source_path(context)
        source = repository.read_text(path)

        field_source = f"{tool_input.parameter_name}: float = {tool_input.value!r}"
        block = _added_parameter_block(tool_input.parameter_name, field_source)

        insert_start, insert_end = _parameter_insertion_span(source)
        candidate_source = source[:insert_start] + block + source[insert_end:]
        ast.parse(candidate_source)

        repository.write_text(path, candidate_source)

        return CreateParameterOutput(
            status="created",
            parameter_name=tool_input.parameter_name,
            value=tool_input.value,
            candidate_id=context.candidate_id,
            source_path=path,
            content_hash=source_hash(candidate_source),
            summary=(
                f'Added ModelParams field "{tool_input.parameter_name}" = '
                f"{tool_input.value} to the edit-scoped candidate."
            ),
        )


class EditParameterTool(AgentTool[EditParameterInput, EditParameterOutput]):
    """Change an existing ModelParams field's default value.

    This tool edits exactly one existing `ModelParams` field's default. It
    works on any existing field -- agent-created or hand-authored -- and
    never renames the field or changes any other source. It modifies only
    the candidate identified by the execution context's `candidate_id` and
    never touches accepted source.
    """

    tool_id = "edit_parameter"
    version = 1
    description = (
        "Change an existing ModelParams field's default numeric value in "
        "the current edit-scoped candidate source. The field must already "
        "exist; it is not renamed, only its default changes."
    )
    input_model = EditParameterInput
    output_model = EditParameterOutput
    batch_group = None

    async def normalize_input(self, tool_input: EditParameterInput) -> EditParameterInput:
        return tool_input.model_copy(update={"parameter_name": tool_input.parameter_name.strip()})

    async def validate_input(
        self,
        tool_input: EditParameterInput,
        context: ToolExecutionContext,
    ) -> None:
        if not context.candidate_id or not context.candidate_id.strip():
            raise ToolInputRejected(
                "An edit-scoped candidate is required to edit a parameter.",
                details={"reason": "missing_candidate"},
            )

        if not _is_public_identifier(tool_input.parameter_name):
            raise ToolInputRejected(
                "parameter_name must be a public Python identifier.",
                details={"field": "parameter_name"},
            )

        try:
            source = context.services.repository.read_text(_candidate_source_path(context))
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                "The edit-scoped candidate source could not be read.",
                details={"reason": "candidate_unavailable", "code": exc.code},
            ) from exc

        if _find_parameter_span(source, tool_input.parameter_name) is None:
            raise ToolInputRejected(
                f'parameter_name "{tool_input.parameter_name}" does not exist.',
                details={"field": "parameter_name", "reason": "not_found"},
            )

    async def execute(
        self,
        tool_input: EditParameterInput,
        context: ToolExecutionContext,
    ) -> EditParameterOutput:
        assert context.candidate_id  # narrowed by validate_input

        repository = context.services.repository
        path = _candidate_source_path(context)
        source = repository.read_text(path)

        start, end, indent = _find_parameter_span(source, tool_input.parameter_name)
        previous_value = _literal_default(source[start:end])

        field_source = f"{tool_input.parameter_name}: float = {tool_input.value!r}"
        replacement = _replacement_field_source(tool_input.parameter_name, field_source, indent)

        candidate_source = source[:start] + replacement + source[end:]
        ast.parse(candidate_source)

        repository.write_text(path, candidate_source)

        return EditParameterOutput(
            status="edited",
            parameter_name=tool_input.parameter_name,
            previous_value=previous_value,
            value=tool_input.value,
            candidate_id=context.candidate_id,
            source_path=path,
            content_hash=source_hash(candidate_source),
            summary=(
                f'Edited ModelParams field "{tool_input.parameter_name}" to '
                f"{tool_input.value} in the edit-scoped candidate."
            ),
        )


class DeleteParameterTool(AgentTool[DeleteParameterInput, DeleteParameterOutput]):
    """Delete one agent-created, unreferenced ModelParams field.

    This tool deletes exactly one existing `ModelParams` field. Unlike
    `edit_parameter`, it only works on fields that were themselves created by
    an agent (wrapped in `# CAD-AGENT-START`/`# CAD-AGENT-END` provenance
    markers) -- hand-authored fields cannot be deleted this way. It also
    refuses to delete a field that is still referenced, either by another
    feature's declared `parameters` or by `params.<field>` usage anywhere in
    the source. It modifies only the candidate identified by the execution
    context's `candidate_id` and never touches accepted source.
    """

    tool_id = "delete_parameter"
    version = 1
    description = (
        "Delete one existing, agent-created ModelParams field from the "
        "current edit-scoped candidate source. Only fields created by an "
        "agent (provenance-marked) may be deleted, and only if no feature "
        "still declares or uses the field -- remove that usage first."
    )
    input_model = DeleteParameterInput
    output_model = DeleteParameterOutput
    batch_group = None

    async def normalize_input(self, tool_input: DeleteParameterInput) -> DeleteParameterInput:
        return tool_input.model_copy(update={"parameter_name": tool_input.parameter_name.strip()})

    async def validate_input(
        self,
        tool_input: DeleteParameterInput,
        context: ToolExecutionContext,
    ) -> None:
        if not context.candidate_id or not context.candidate_id.strip():
            raise ToolInputRejected(
                "An edit-scoped candidate is required to delete a parameter.",
                details={"reason": "missing_candidate"},
            )

        if not _is_public_identifier(tool_input.parameter_name):
            raise ToolInputRejected(
                "parameter_name must be a public Python identifier.",
                details={"field": "parameter_name"},
            )

        try:
            source = context.services.repository.read_text(_candidate_source_path(context))
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                "The edit-scoped candidate source could not be read.",
                details={"reason": "candidate_unavailable", "code": exc.code},
            ) from exc

        if _find_parameter_span(source, tool_input.parameter_name) is None:
            raise ToolInputRejected(
                f'parameter_name "{tool_input.parameter_name}" does not exist.',
                details={"field": "parameter_name", "reason": "not_found"},
            )

        if _owned_parameter_marker_span(source, tool_input.parameter_name) is None:
            raise ToolInputRejected(
                f'parameter_name "{tool_input.parameter_name}" was not created by an '
                "agent and cannot be deleted this way.",
                details={"field": "parameter_name", "reason": "not_owned"},
            )

        owners = _parameter_usage_owners(source, tool_input.parameter_name)
        if owners:
            raise ToolInputRejected(
                f'parameter_name "{tool_input.parameter_name}" is still referenced '
                "and cannot be deleted.",
                details={
                    "field": "parameter_name",
                    "reason": "in_use",
                    "referenced_by": sorted(owners),
                },
            )

    async def execute(
        self,
        tool_input: DeleteParameterInput,
        context: ToolExecutionContext,
    ) -> DeleteParameterOutput:
        assert context.candidate_id  # narrowed by validate_input

        repository = context.services.repository
        path = _candidate_source_path(context)
        source = repository.read_text(path)

        start, end, _ = _find_parameter_span(source, tool_input.parameter_name)
        removed_value = _literal_default(source[start:end])

        marker_start, marker_end = _owned_parameter_marker_span(source, tool_input.parameter_name)

        tree = ast.parse(source)
        model_params = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ModelParams"
        )
        is_last_field = len(model_params.body) == 1

        replacement = "    pass\n" if is_last_field else ""
        candidate_source = source[:marker_start] + replacement + source[marker_end:]
        ast.parse(candidate_source)

        repository.write_text(path, candidate_source)

        return DeleteParameterOutput(
            status="deleted",
            parameter_name=tool_input.parameter_name,
            removed_value=removed_value,
            candidate_id=context.candidate_id,
            source_path=path,
            content_hash=source_hash(candidate_source),
            summary=f'Deleted ModelParams field "{tool_input.parameter_name}" from the edit-scoped candidate.',
        )
