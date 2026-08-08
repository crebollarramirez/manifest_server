from __future__ import annotations

import json
import logging
import os
from typing import Any
from uuid import uuid4

from openai import OpenAI

from ..failures import WorkflowFailure
from .agent_contracts import CadGoal, CadGoalDefinition
from .prompt_loader import load_goal_creation_prompt


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


class GoalCreator:
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
            or os.environ.get("OPENAI_GOAL_MODEL", "").strip()
            or os.environ.get("OPENAI_MODEL", "").strip()
            or "gpt-5.4-mini"
        )
        self.instructions = instructions or load_goal_creation_prompt()

    def create_goal_definition(self, raw_request: str) -> CadGoalDefinition:
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=self.instructions,
                input=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"request": raw_request}, ensure_ascii=False
                        ),
                    }
                ],
                text_format=CadGoalDefinition,
            )
            parsed = _field(response, "output_parsed")
            if parsed is None:
                refusal = _refusal_from(_field(response, "output", []))
                if refusal:
                    raise WorkflowFailure("AI_REFUSAL", refusal)
                raise WorkflowFailure(
                    "GOAL_RESPONSE_INVALID",
                    "The goal-creation model returned no structured goal.",
                )
            if isinstance(parsed, CadGoalDefinition):
                return parsed
            return CadGoalDefinition.model_validate(parsed)
        except WorkflowFailure:
            raise
        except Exception as exc:
            LOGGER.exception(
                "goal creation model request failed model=%s error_type=%s",
                self.model,
                type(exc).__name__,
            )
            raise WorkflowFailure(
                "GOAL_CREATION_FAILED",
                "The CAD goal could not be created.",
            ) from exc

    def create_goal(self, raw_request: str) -> CadGoal:
        if not isinstance(raw_request, str) or not raw_request.strip():
            raise WorkflowFailure(
                "INVALID_REQUEST", "A non-empty CAD request is required."
            )
        if len(raw_request) > 20_000:
            raise WorkflowFailure(
                "INVALID_REQUEST", "The CAD request exceeds 20000 characters."
            )
        definition = self.create_goal_definition(raw_request)
        payload = definition.model_dump(mode="json")
        payload["completion_criteria"] = [
            {**criterion, "criterion_id": f"GC-{index}"}
            for index, criterion in enumerate(
                payload["completion_criteria"], start=1
            )
        ]
        payload.update(goal_id=str(uuid4()), raw_request=raw_request)
        return CadGoal.model_validate(payload)
