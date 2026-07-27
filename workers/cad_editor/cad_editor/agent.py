from __future__ import annotations

import json
import os
from typing import Any, TypeVar

from pydantic import BaseModel

from .contracts import (
    EditContext,
    EditPlan,
    InitialCadDesignContext,
    InitialCadModel,
    InitialCadRepairContext,
    RepairContext,
    SearchQueryPlan,
    TargetSelection,
    WorkflowFailure,
)


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
ParsedModel = TypeVar("ParsedModel", bound=BaseModel)

SEARCH_PROMPT = """\
Extract one to three short CAD feature search phrases from the user's request.
Return concrete nouns such as "mounting holes" or "wall plate". Do not propose
code and do not include explanatory text.
"""

TARGET_PROMPT = """\
Select the CAD semantic features that the user intends to change from the
provided ranked metadata. Select exactly one database part_id. You may select
multiple semantic_ids only when they all belong to that part. Never invent an
identifier.
"""

EDIT_PROMPT = """\
You are the planning component of a CadQuery source editor. Return a structured
EditPlan only. You decide what existing symbols should change; a deterministic
server applies the operations.

Use only target_id values from allowed_targets. Supported operations are:
- replace_parameter_field: replace one existing annotated ModelParams field.
- update_cad_part_metadata: update role, parameters, depends_on, and search_keys
  for an existing semantic feature. semantic_id and library are immutable.
- replace_function_body: replace statements inside an existing function while
  preserving its decorator, name, and signature.
- add_model_parameter: add one annotated ModelParams field with a default.
- add_private_helper: add one undecorated private function whose name begins
  with one underscore.
- add_cad_feature: add one undecorated public feature function. Provide its
  semantic metadata separately; the server renders @cad_part and part markers.
- replace_build_model_body: replace only the statements inside build_model so
  newly added features participate in final assembly.

Preserve unrelated behavior. Never add imports, files, classes, runtime
infrastructure, or return a complete model.py. Use additive operations only
when the request needs new model structure. New function_source values must
contain one undecorated function definition and no imports. Keep CadQuery
operations deterministic and ensure build_model still returns a CadQuery
Workplane or Shape.
"""

INITIAL_DESIGN_PROMPT = """\
You are the initial-design component for a blank CadQuery part. Return an
InitialCadModel only. Write the complete AI-owned Python model body for the
user's request; this is not an edit of an existing design.

The system supplies exactly this import outside your body:
from cadquery_runtime import cad_part, cq, dataclass

Do not return imports, markdown fences, files, patches, or prose. Your
model_body must define a frozen ModelParams dataclass with at least one
annotated field, one or more @cad_part-decorated public feature functions, and
build_model(params: ModelParams). Follow the normal strict cad_part metadata
contract, use literal metadata, and make build_model return CadQuery geometry.
"""

INITIAL_REPAIR_PROMPT = """\
Repair a failed initial CadQuery design. Return an InitialCadModel only with a
complete replacement AI-owned model body. Preserve the user's intent, address
the supplied validator diagnostics, and do not return imports, markdown, or
any system/runtime code. The runtime import is system-owned.
"""

REPAIR_PROMPT = """\
Repair the latest failed CadQuery candidate while preserving the original user
request. Return a structured EditPlan only. Use only target_id values from the
repair context, address the structured diagnostics, and do not repeat a failed
operation unchanged. Accepted-source Getter data and failed-candidate source
are labeled separately. Do not edit outside the resolved database part.
"""


class CadEditAgent:
    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str | None = None,
    ):
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.client = client
        self.model: str = (
            model or os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        )

    def _parse(
        self,
        *,
        instructions: str,
        payload: dict[str, Any],
        output_type: type[ParsedModel],
    ) -> ParsedModel:
        response = self.client.responses.parse(
            model=self.model,
            instructions=instructions,
            input=[
                {
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                }
            ],
            text_format=output_type,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is not None:
            return parsed

        refusal = None
        for output in getattr(response, "output", []):
            for item in getattr(output, "content", []):
                if getattr(item, "type", None) == "refusal":
                    refusal = getattr(item, "refusal", None)
        if refusal:
            raise WorkflowFailure("AI_REFUSAL", str(refusal))
        raise WorkflowFailure(
            "AI_RESPONSE_INVALID",
            "OpenAI returned no parsed structured output.",
        )

    def extract_search_queries(self, request: str) -> SearchQueryPlan:
        return self._parse(
            instructions=SEARCH_PROMPT,
            payload={"request": request},
            output_type=SearchQueryPlan,
        )

    def select_targets(
        self,
        request: str,
        candidates: list[dict[str, Any]],
    ) -> TargetSelection:
        return self._parse(
            instructions=TARGET_PROMPT,
            payload={"request": request, "candidates": candidates},
            output_type=TargetSelection,
        )

    def create_edit_plan(self, context: EditContext) -> EditPlan:
        return self._parse(
            instructions=EDIT_PROMPT,
            payload=context.model_dump(mode="json"),
            output_type=EditPlan,
        )

    def create_initial_design(
        self,
        context: InitialCadDesignContext,
    ) -> InitialCadModel:
        return self._parse(
            instructions=INITIAL_DESIGN_PROMPT,
            payload=context.model_dump(mode="json"),
            output_type=InitialCadModel,
        )

    def create_initial_repair(
        self,
        context: InitialCadRepairContext,
    ) -> InitialCadModel:
        return self._parse(
            instructions=INITIAL_REPAIR_PROMPT,
            payload=context.model_dump(mode="json"),
            output_type=InitialCadModel,
        )

    def create_repair_plan(self, context: RepairContext) -> EditPlan:
        return self._parse(
            instructions=REPAIR_PROMPT,
            payload=context.model_dump(mode="json"),
            output_type=EditPlan,
        )
