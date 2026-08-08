from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompletionCriterionDefinition(StrictModel):
    description: str = Field(min_length=1, max_length=1_000)
    type: Literal["required", "preserve"]


class CompletionCriterion(CompletionCriterionDefinition):
    criterion_id: str = Field(pattern=r"^GC-[1-9][0-9]*$")


class Clarification(StrictModel):
    required: bool
    question: str | None = Field(max_length=1_000)
    reason: str | None = Field(max_length=1_000)

    @model_validator(mode="after")
    def validate_required_fields(self) -> Clarification:
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


class CadGoalDefinition(StrictModel):
    description: str = Field(min_length=1, max_length=1_000)
    completion_criteria: list[CompletionCriterionDefinition] = Field(max_length=64)
    constraints: list[str] = Field(max_length=64)
    assumptions: list[str] = Field(max_length=64)
    clarification: Clarification

    @model_validator(mode="after")
    def validate_completion_criteria(self) -> CadGoalDefinition:
        if not self.clarification.required and not self.completion_criteria:
            raise ValueError(
                "Completion criteria may be empty only when clarification is required."
            )
        for label, values in (
            ("constraints", self.constraints),
            ("assumptions", self.assumptions),
        ):
            if any(not value.strip() or len(value) > 1_000 for value in values):
                raise ValueError(f"{label} entries must contain 1 to 1000 characters.")
        return self


class CadGoal(CadGoalDefinition):
    goal_id: UUID
    raw_request: str = Field(min_length=1, max_length=20_000)
    completion_criteria: list[CompletionCriterion] = Field(max_length=64)

    @model_validator(mode="after")
    def validate_criterion_ids(self) -> CadGoal:
        for index, criterion in enumerate(self.completion_criteria, start=1):
            if criterion.criterion_id != f"GC-{index}":
                raise ValueError(f"Criterion ID must be GC-{index}.")
        return self


class PlanTargetBinding(StrictModel):
    semantic_id: str = Field(min_length=1, max_length=500)
    relationship: Literal["primary", "related"]
    reason: str = Field(min_length=1, max_length=1_000)


class PlanStep(StrictModel):
    step_id: str = Field(pattern=r"^PS-[1-9][0-9]*$")
    sequence: int = Field(gt=0)
    objective: str = Field(min_length=1, max_length=1_000)
    depends_on: list[str] = Field(max_length=64)
    addresses_criteria: list[str] = Field(max_length=64)
    status: Literal["pending", "in_progress", "completed", "blocked", "superseded"]


class CadPlanContent(StrictModel):
    summary: str = Field(min_length=1, max_length=2_000)
    target_bindings: list[PlanTargetBinding] = Field(max_length=64)
    steps: list[PlanStep] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_step_sequence(self) -> CadPlanContent:
        seen: set[str] = set()
        for index, step in enumerate(self.steps, start=1):
            if step.step_id != f"PS-{index}":
                raise ValueError(f"Step ID must be PS-{index}.")
            if step.sequence != index:
                raise ValueError(f"Step sequence must be {index}.")
            if any(dependency not in seen for dependency in step.depends_on):
                raise ValueError("Step dependencies must refer only to earlier steps.")
            seen.add(step.step_id)
        return self


class CadPlanModelOutput(CadPlanContent):
    # The model echoes these values, but the worker replaces them with its own.
    plan_id: str
    goal_id: str
    version: int


class CadPlan(CadPlanContent):
    plan_id: UUID
    goal_id: UUID
    version: int = Field(gt=0)
