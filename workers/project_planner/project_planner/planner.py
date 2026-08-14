from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from openai import OpenAI

from .contracts import PlanViolation, ProjectPlanDraft, ProjectPlanningInput
from .failures import ProjectPlanningFailure


LOGGER = logging.getLogger(__name__)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _refusal_from(output: Any) -> str | None:
    if not isinstance(output, list):
        return None
    for item in output:
        content = _field(item, "content", [])
        if not isinstance(content, list):
            continue
        for entry in content:
            if _field(entry, "type") == "refusal":
                return str(
                    _field(entry, "refusal", "The model refused the request.")
                )
    return None


@lru_cache(maxsize=None)
def _load_project_planning_prompt() -> str:
    path = Path(__file__).resolve().parent / "prompts" / "project-planning.md"
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Prompt file is empty: {path}")
    return text


class ProjectPlanner:
    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        instructions: str | None = None,
    ):
        if client is None:
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is required.")
            client = OpenAI(api_key=api_key)
        self.client = client
        self.model = (
            model
            or os.environ.get("OPENAI_PROJECT_PLANNING_MODEL", "").strip()
            or os.environ.get("OPENAI_MODEL", "").strip()
            or "gpt-5.4-mini"
        )
        self.instructions = instructions or _load_project_planning_prompt()

    def _request_plan_draft(self, input_items: list[dict]) -> ProjectPlanDraft:
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=self.instructions,
                input=input_items,
                text_format=ProjectPlanDraft,
                # Mirrors Agent3D's _reasoning_config(): a structural
                # decomposition mistake is expensive to leave uncaught, so
                # the model gets real deliberation on both the initial
                # draft and every repair attempt. Unlike Agent3D there is
                # no multi-turn chain here, so no "context" setting is
                # needed alongside effort.
                reasoning={"effort": "medium"},
            )
            parsed = _field(response, "output_parsed")
            if parsed is None:
                refusal = _refusal_from(_field(response, "output", []))
                if refusal:
                    raise ProjectPlanningFailure("AI_REFUSAL", refusal)
                raise ProjectPlanningFailure(
                    "PROJECT_PLAN_RESPONSE_INVALID",
                    "The project-planning model returned no structured plan.",
                )
            if isinstance(parsed, ProjectPlanDraft):
                draft = parsed
            else:
                draft = ProjectPlanDraft.model_validate(parsed)
            LOGGER.info(
                "project planning model response model=%s part_count=%s "
                "interface_count=%s clarification_required=%s",
                self.model,
                len(draft.parts),
                len(draft.interfaces),
                draft.clarification.required,
            )
            LOGGER.debug(
                "project planning raw draft=%s", draft.model_dump(mode="json")
            )
            return draft
        except ProjectPlanningFailure:
            raise
        except Exception as exc:
            LOGGER.exception(
                "project planning model request failed model=%s error_type=%s",
                self.model,
                type(exc).__name__,
            )
            raise ProjectPlanningFailure(
                "PROJECT_PLANNING_FAILED",
                "The project plan could not be created.",
            ) from exc

    def create_plan_draft(
        self, planning_input: ProjectPlanningInput
    ) -> ProjectPlanDraft:
        return self._request_plan_draft(
            [
                {
                    "role": "user",
                    "content": json.dumps(
                        planning_input.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                }
            ]
        )

    def repair_plan_draft(
        self,
        planning_input: ProjectPlanningInput,
        *,
        previous_draft: ProjectPlanDraft,
        violations: list[PlanViolation],
    ) -> ProjectPlanDraft:
        return self._request_plan_draft(
            [
                {
                    "role": "user",
                    "content": json.dumps(
                        planning_input.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        previous_draft.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "repair_request": True,
                            "violations": [
                                violation.model_dump(mode="json")
                                for violation in violations
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        )
