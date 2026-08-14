from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .failures import ProjectPlanningFailure


PROJECT_PLAN_SCHEMA_VERSION = 1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- shared sub-objects -----------------------------------------------


class ProjectRequirement(StrictModel):
    ref: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1_000)


class PlannedPart(StrictModel):
    ref: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["new", "existing"]
    existing_part_id: str | None = None
    purpose: str = Field(min_length=1, max_length=1_000)
    responsibilities: list[str] = Field(max_length=32)
    addresses_requirements: list[str] = Field(max_length=64)
    # No validator here enforcing existing_part_id null/non-null shape or
    # roster membership -- that check needs the caller-supplied existing
    # parts roster (an external input this schema can't see) and must raise
    # PROJECT_PLAN_UNKNOWN_PART specifically, not a generic parse-failure
    # code. It lives in validator.py instead.


class InterfaceEndpoint(StrictModel):
    part_ref: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=200)


class InterfaceParameter(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=500)
    unit: str | None = Field(default=None, max_length=32)


class PlannedInterface(StrictModel):
    ref: str = Field(min_length=1, max_length=64)
    interface_type: Literal[
        "fixed",
        "rotational",
        "sliding",
        "fastened",
        "snap_fit",
        "press_fit",
        "alignment",
        "contact",
        "other",
    ]
    endpoint_a: InterfaceEndpoint
    endpoint_b: InterfaceEndpoint
    purpose: str = Field(min_length=1, max_length=1_000)
    parameters: list[InterfaceParameter] = Field(max_length=32)
    requirements: list[str] = Field(max_length=32)
    addresses_requirements: list[str] = Field(max_length=64)


class ExecutionDependency(StrictModel):
    prerequisite_part_ref: str = Field(min_length=1, max_length=64)
    dependent_part_ref: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1_000)


class ProjectClarification(StrictModel):
    required: bool
    question: str | None = Field(max_length=1_000)
    reason: str | None = Field(max_length=1_000)

    @model_validator(mode="after")
    def validate_required_fields(self) -> ProjectClarification:
        if self.required:
            if not self.question or not self.question.strip():
                raise ValueError(
                    "A non-empty clarification question is required."
                )
            if not self.reason or not self.reason.strip():
                raise ValueError(
                    "A non-empty clarification reason is required."
                )
        elif self.question is not None or self.reason is not None:
            raise ValueError(
                "Clarification question and reason must be null when not required."
            )
        return self


# --- LLM-facing draft & worker-finalized plan --------------------------


class ProjectPlanDraft(StrictModel):
    """Exactly what the model returns via responses.parse(text_format=...).

    No plan_id or schema_version -- there is nothing to echo back; the
    worker injects both after validating this draft.
    """

    summary: str = Field(min_length=1, max_length=2_000)
    design_mode: Literal["single_part", "multi_part"]
    requirements: list[ProjectRequirement] = Field(max_length=64)
    parts: list[PlannedPart] = Field(max_length=32)
    interfaces: list[PlannedInterface] = Field(max_length=64)
    execution_dependencies: list[ExecutionDependency] = Field(max_length=64)
    assumptions: list[str] = Field(max_length=64)
    clarification: ProjectClarification

    @model_validator(mode="after")
    def validate_parts_present(self) -> ProjectPlanDraft:
        if not self.clarification.required and not self.parts:
            raise ValueError(
                "parts may be empty only when clarification is required."
            )
        return self


class ProjectPlan(ProjectPlanDraft):
    plan_id: UUID
    schema_version: int = Field(gt=0)


def finalize_plan_draft(draft: ProjectPlanDraft) -> ProjectPlan:
    """The only place plan_id/schema_version are ever set. Call exactly
    once, only after validate_project_plan has returned zero violations --
    finalizing an unvalidated draft would produce a ProjectPlan that looks
    authoritative but was never checked."""
    payload = draft.model_dump(mode="json")
    payload.update(plan_id=str(uuid4()), schema_version=PROJECT_PLAN_SCHEMA_VERSION)
    try:
        return ProjectPlan.model_validate(payload)
    except Exception as exc:
        raise ProjectPlanningFailure(
            "PROJECT_PLAN_RESPONSE_INVALID",
            "The finalized project plan failed validation.",
        ) from exc


# --- deterministic validation output ------------------------------------


class PlanViolation(StrictModel):
    """One deterministic-validation problem found in a ProjectPlanDraft.

    `code` is one of the six stable top-level validator codes. Several of
    those codes can fire for more than one structurally distinct reason;
    `details["condition"]` disambiguates which one with a stable string
    (e.g. "self_reference", "unknown_existing_part_id") rather than
    requiring a caller to parse `message`. Checks that only ever fail one
    way omit "condition" from `details`.

    `retryable` is True for every violation validator.py produces today --
    every current check represents a fixable structural mistake -- but the
    field exists so a repair loop can branch on it rather than assume it,
    and so a future non-retryable check has somewhere to say so.
    """

    code: str
    message: str
    details: dict[str, Any]
    retryable: bool = True


# --- planner input -------------------------------------------------------


class PartInventoryItem(StrictModel):
    part_id: str
    part_name: str
    part_type: Literal["cad"]
    features: list[dict] = Field(max_length=256)
    parameters: list[str] = Field(max_length=256)


class ProjectPlanningLimits(StrictModel):
    max_parts: int = Field(gt=0)
    max_interfaces: int = Field(ge=0)


class ProjectPlanningInput(StrictModel):
    project_id: str
    request_text: str = Field(min_length=1, max_length=20_000)
    existing_parts: list[PartInventoryItem]
    planning_limits: ProjectPlanningLimits


# --- AssemblySpec (deterministically derived, never LLM output) --------


class PartBinding(StrictModel):
    mode: Literal["existing", "create"]
    part_id: str | None = None


class AssemblyNode(StrictModel):
    node_id: str
    semantic_ref: str
    name: str
    binding: PartBinding
    purpose: str
    responsibilities: tuple[str, ...]
    addresses_requirements: tuple[str, ...]


class AssemblyEndpoint(StrictModel):
    node_id: str
    role: str


class InterfaceContract(StrictModel):
    interface_id: str
    semantic_ref: str
    interface_type: str
    endpoint_a: AssemblyEndpoint
    endpoint_b: AssemblyEndpoint
    purpose: str
    parameters: tuple[InterfaceParameter, ...]
    requirements: tuple[str, ...]


class AssemblyDependency(StrictModel):
    prerequisite_node_id: str
    dependent_node_id: str
    prerequisite_ref: str
    dependent_ref: str
    reason: str


class AssemblyRequirement(StrictModel):
    ref: str
    description: str


class AssemblySpec(StrictModel):
    spec_id: str
    schema_version: int
    project_id: str
    summary: str
    requirements: tuple[AssemblyRequirement, ...]
    nodes: tuple[AssemblyNode, ...]
    interfaces: tuple[InterfaceContract, ...]
    execution_dependencies: tuple[AssemblyDependency, ...]
    assumptions: tuple[str, ...]


# --- AssemblyRevision (Phase 2: persisted, immutable wrapper) -----------


class AssemblyRevision(StrictModel):
    """The persisted, immutable wrapper around one published AssemblySpec.
    Unlike AssemblySpec (pure content, never remixed with identity), the
    identity/provenance fields that only make sense once something is
    actually published -- which assembly this belongs to, which numbered
    revision it is, what it was published from -- live here, on the
    wrapper, not on AssemblySpec itself."""

    revision_id: str
    assembly_id: str
    revision: int = Field(ge=1)
    parent_revision: int | None = Field(default=None, ge=1)
    design_request_id: str
    schema_version: int = Field(gt=0)
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    definition: AssemblySpec
    created_at: str

    @model_validator(mode="after")
    def validate_parent_revision(self) -> AssemblyRevision:
        if (self.revision == 1) != (self.parent_revision is None):
            raise ValueError(
                "Revision 1 must have no parent_revision; revision > 1 "
                "must have one."
            )
        if self.parent_revision is not None and self.parent_revision >= self.revision:
            raise ValueError("parent_revision must be strictly less than revision.")
        return self
