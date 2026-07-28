from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplaceParameterField(StrictModel):
    operation: Literal["replace_parameter_field"]
    target_id: str
    replacement_source: str


class UpdateCadPartMetadata(StrictModel):
    operation: Literal["update_cad_part_metadata"]
    target_id: str
    role: str
    parameters: list[str]
    depends_on: list[str]
    search_keys: list[str] = Field(min_length=1)


class ReplaceFunctionBody(StrictModel):
    operation: Literal["replace_function_body"]
    target_id: str
    replacement_source: str


class AddModelParameter(StrictModel):
    operation: Literal["add_model_parameter"]
    name: str
    field_source: str


class AddPrivateHelper(StrictModel):
    operation: Literal["add_private_helper"]
    function_name: str
    function_source: str


class AddCadFeature(StrictModel):
    operation: Literal["add_cad_feature"]
    semantic_id: str
    function_name: str
    role: str
    parameters: list[str]
    depends_on: list[str]
    search_keys: list[str] = Field(min_length=1)
    function_source: str


class ReplaceBuildModelBody(StrictModel):
    operation: Literal["replace_build_model_body"]
    target_id: str
    replacement_source: str


EditOperation = (
    ReplaceParameterField
    | UpdateCadPartMetadata
    | ReplaceFunctionBody
    | AddModelParameter
    | AddPrivateHelper
    | AddCadFeature
    | ReplaceBuildModelBody
)


class EditPlan(StrictModel):
    summary: str
    target_semantic_ids: list[str]
    operations: list[EditOperation] = Field(min_length=1, max_length=12)


class AllowedTarget(StrictModel):
    target_id: str
    kind: Literal[
        "model_parameter",
        "cad_part_metadata",
        "function_body",
        "build_model_body",
    ]
    part_id: str
    name: str
    semantic_id: str | None = None
    source: str
    line_start: int
    line_end: int
    details: dict[str, Any] = Field(default_factory=dict)


class EditContext(StrictModel):
    request: str
    conversation: list[dict[str, str]]
    part_id: str
    part_name: str
    file_path: str
    file_hash: str
    semantic_ids: list[str]
    target_parts: list[dict[str, Any]]
    source_chunks: list[dict[str, Any]]
    parameters: list[dict[str, Any]]
    dependencies: list[dict[str, Any]]
    allowed_targets: list[AllowedTarget]


@dataclass(frozen=True)
class ResolvedEditTarget:
    part_id: str
    part_name: str
    semantic_ids: list[str]
    confidence: float
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateSource:
    content: str
    content_hash: str
    base_hash: str
    changed_symbols: list[str]
    applied_operations: list[dict[str, Any]]
    normalization_notes: list[str] = field(default_factory=list)
class WorkflowFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or {}
