from __future__ import annotations

import ast
import textwrap
from typing import Literal

from pydantic import Field

from ...failures import WorkflowFailure
from ..hashing import source_hash
from ..base import AgentTool, StrictToolModel, ToolExecutionContext, ToolInputRejected
from ..feature.feature_generation import (
    _build_model_body_span,
    _candidate_source_path,
    _parse_body_function,
    _reject_forbidden_body_nodes,
    _walk_body,
    _PROVENANCE_MARKERS,
)


def _defined_function_names(source: str) -> set[str]:
    """Return every top-level function name defined in CAD source.

    Covers both `@cad_part` features and private helpers -- anything
    callable by a bare name from `build_model`.
    """

    tree = ast.parse(source)
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class EditCadBuildModelInput(StrictToolModel):
    """Strict, LLM-facing arguments for replacing build_model's body.

    ``function_body`` contains only statements (no `def build_model(...):`
    line, no decorator) -- the same convention as `create_feature`'s
    `function_body`.
    """

    function_body: str = Field(
        min_length=1,
        max_length=8_000,
        description=(
            "Only the function body statements, indented as they will appear "
            "inside `def build_model(params: ModelParams):`. Do not include "
            "the `def` line."
        ),
    )


class EditCadBuildModelOutput(StrictToolModel):
    """Structured result of a successful ``EditCadBuildModelTool`` invocation."""

    status: Literal["edited"]
    candidate_id: str
    content_hash: str
    source_path: str
    summary: str


class EditCadBuildModelTool(AgentTool[EditCadBuildModelInput, EditCadBuildModelOutput]):
    """Replace the body of `build_model` in the edit-scoped candidate.

    This is the assembly-wiring step every other tool in this package
    explicitly defers: `create_feature`/`edit_feature`/`delete_feature` never
    touch `build_model`, so calling one of those alone does not affect the
    final model. This tool is what actually wires (or rewires) feature calls
    together. It replaces the entire body -- not append-only -- and applies
    the same function-body safety checks as `create_feature` (parses as
    valid Python, no imports/eval/exec/forbidden modules, must return a
    value). It modifies only the candidate identified by the execution
    context's `candidate_id` and never touches accepted source.
    """

    tool_id = "edit_cad_build_model"
    version = 1
    description = (
        "Replace the entire body of build_model in the current edit-scoped "
        "candidate source -- the assembly-wiring step that actually calls "
        "feature functions together. create_feature/edit_feature/"
        "delete_feature never touch build_model; use this tool afterward to "
        "wire a new or changed feature into the final model. Provide the "
        "complete new body (not just the parts that changed)."
    )
    input_model = EditCadBuildModelInput
    output_model = EditCadBuildModelOutput

    async def normalize_input(self, tool_input: EditCadBuildModelInput) -> EditCadBuildModelInput:
        normalized_body = tool_input.function_body.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
        normalized_body = textwrap.dedent(normalized_body).strip("\n")
        return tool_input.model_copy(update={"function_body": normalized_body})

    async def validate_input(
        self,
        tool_input: EditCadBuildModelInput,
        context: ToolExecutionContext,
    ) -> None:
        if not context.candidate_id or not context.candidate_id.strip():
            raise ToolInputRejected(
                "An edit-scoped candidate is required to edit build_model.",
                details={"reason": "missing_candidate"},
            )

        for marker in _PROVENANCE_MARKERS:
            if marker in tool_input.function_body:
                raise ToolInputRejected(
                    "function_body must not contain system-owned provenance markers.",
                    details={"field": "function_body", "marker": marker},
                )

        try:
            function_node = _parse_body_function(tool_input.function_body)
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

        try:
            _build_model_body_span(source)
        except WorkflowFailure as exc:
            raise ToolInputRejected(
                "The edit-scoped candidate source is not in an editable state.",
                details={"reason": "invalid_candidate", "code": exc.code},
            ) from exc

        called_names = {
            node.func.id
            for node in _walk_body(function_node)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        missing = sorted(called_names - _defined_function_names(source))
        if missing:
            raise ToolInputRejected(
                "build_model calls undefined function(s): "
                + ", ".join(missing)
                + ". Create them with create_feature before wiring them in.",
                details={
                    "field": "function_body",
                    "reason": "undefined_function_call",
                    "missing": missing,
                },
            )

    async def execute(
        self,
        tool_input: EditCadBuildModelInput,
        context: ToolExecutionContext,
    ) -> EditCadBuildModelOutput:
        assert context.candidate_id  # narrowed by validate_input

        repository = context.services.repository
        path = _candidate_source_path(context)
        source = repository.read_text(path)

        start, end, indent = _build_model_body_span(source)
        replacement = textwrap.indent(tool_input.function_body, " " * indent) + "\n"
        candidate_source = source[:start] + replacement + source[end:]
        ast.parse(candidate_source)

        repository.write_text(path, candidate_source)

        return EditCadBuildModelOutput(
            status="edited",
            candidate_id=context.candidate_id,
            content_hash=source_hash(candidate_source),
            source_path=path,
            summary="Replaced build_model's body in the edit-scoped candidate.",
        )
