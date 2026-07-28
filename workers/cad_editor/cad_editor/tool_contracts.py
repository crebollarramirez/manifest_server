from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetedTool(StrictModel):
    target_id: str = Field(min_length=1)
    target_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class WriteInitialModel(StrictModel):
    tool: Literal["write_initial_model"]
    model_body: str = Field(min_length=1)


class ReplaceParameterField(TargetedTool):
    tool: Literal["replace_parameter_field"]
    replacement_source: str = Field(min_length=1)


class UpdateCadPartMetadata(TargetedTool):
    tool: Literal["update_cad_part_metadata"]
    role: str = Field(min_length=1)
    parameters: list[str]
    depends_on: list[str]
    search_keys: list[str] = Field(min_length=1)


class ReplaceFunctionBody(TargetedTool):
    tool: Literal["replace_function_body"]
    replacement_source: str = Field(min_length=1)


class ReplaceCadFeatureBody(StrictModel):
    """Replace a public CAD feature by stable semantic identity."""

    tool: Literal["replace_cad_feature_body"]
    semantic_id: str = Field(min_length=1)
    target_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    replacement_source: str = Field(min_length=1)


class AddModelParameter(StrictModel):
    tool: Literal["add_model_parameter"]
    name: str = Field(min_length=1)
    field_source: str = Field(min_length=1)


class AddPrivateHelper(StrictModel):
    tool: Literal["add_private_helper"]
    function_name: str = Field(min_length=1)
    function_source: str = Field(min_length=1)


class AddCadFeature(StrictModel):
    tool: Literal["add_cad_feature"]
    semantic_id: str = Field(min_length=1)
    function_name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    parameters: list[str]
    depends_on: list[str]
    search_keys: list[str] = Field(min_length=1)
    function_source: str = Field(min_length=1)


class ReplaceBuildModelBody(TargetedTool):
    tool: Literal["replace_build_model_body"]
    replacement_source: str = Field(min_length=1)


class DeleteModelParameter(TargetedTool):
    tool: Literal["delete_model_parameter"]


class DeletePrivateHelper(TargetedTool):
    tool: Literal["delete_private_helper"]


class DeleteCadFeature(TargetedTool):
    tool: Literal["delete_cad_feature"]


ToolOperation = Annotated[
    WriteInitialModel
    | ReplaceParameterField
    | UpdateCadPartMetadata
    | ReplaceFunctionBody
    | ReplaceCadFeatureBody
    | AddModelParameter
    | AddPrivateHelper
    | AddCadFeature
    | ReplaceBuildModelBody
    | DeleteModelParameter
    | DeletePrivateHelper
    | DeleteCadFeature,
    Field(discriminator="tool"),
]


class ImpactReview(StrictModel):
    semantic_id: str = Field(min_length=1)
    decision: Literal["modified", "verified_compatible"]
    reason: str = Field(min_length=1, max_length=500)


class ToolPlan(StrictModel):
    schema_version: Literal[1, 2]
    summary: str = Field(min_length=1, max_length=500)
    target_part_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    base_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operations: list[ToolOperation] = Field(min_length=1, max_length=12)
    impact_review: list[ImpactReview] | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_versioned_impact_review(self) -> ToolPlan:
        if self.schema_version == 2 and self.impact_review is None:
            raise ValueError("ToolPlan schema version 2 requires impact_review.")
        if self.schema_version == 1 and self.impact_review is not None:
            raise ValueError("ToolPlan schema version 1 cannot contain impact_review.")
        return self
