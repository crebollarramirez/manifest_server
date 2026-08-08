from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

from ..failures import WorkflowFailure
from ..tools import (
    ToolExecutionContext,
    ToolExecutor,
    ToolFailure,
    Toolbox,
)
from ..tools.base import tool_failure
from .agent_contracts import (
    CadGoal,
    CadPlanModelOutput,
)
from .prompt_loader import load_planning_prompt


MAX_TOOL_ROUNDS = 8
PLANNING_TOOL_IDS = ("index_search", "index_get_feature")
LOGGER = logging.getLogger(__name__)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _output(response: Any) -> list[Any]:
    value = _field(response, "output", [])
    return value if isinstance(value, list) else []


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
                    _field(
                        entry,
                        "refusal",
                        "The model refused the planning request.",
                    )
                )
    return None


def _semantic_ids_from_tool_data(value: Any) -> set[str]:
    semantic_ids: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        semantic_id = item.get("semantic_id")
        if isinstance(semantic_id, str) and semantic_id:
            semantic_ids.add(semantic_id)
        for child in item.values():
            visit(child)

    visit(value)
    return semantic_ids


class PlanningAgent:
    def __init__(
        self,
        toolbox: Toolbox,
        tool_executor: ToolExecutor,
        *,
        allowed_tools: tuple[str, ...] = PLANNING_TOOL_IDS,
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
        self.toolbox = toolbox
        self.tool_executor = tool_executor
        self.allowed_tools = tuple(allowed_tools)
        self.tools = toolbox.get_tool_definitions(self.allowed_tools)
        self.model = (
            model
            or os.environ.get("OPENAI_PLANNING_MODEL", "").strip()
            or os.environ.get("OPENAI_MODEL", "").strip()
            or "gpt-5.4-mini"
        )
        self.instructions = instructions or load_planning_prompt()

    def create_plan(
        self,
        *,
        goal: CadGoal,
        plan_id: str,
        tool_context: ToolExecutionContext,
        version: int = 1,
    ) -> CadPlanModelOutput:
        LOGGER.info(
            "planning request started plan_id=%s goal_id=%s project_id=%s "
            "part_id=%s version=%s model=%s",
            plan_id,
            goal.goal_id,
            tool_context.project_id,
            tool_context.part_id,
            version,
            self.model,
        )
        observed_semantic_ids: set[str] = set()
        selected_tool_ids = self.allowed_tools
        tools = self.tools

        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=self.instructions,
                input=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "plan_id": plan_id,
                                "goal_id": str(goal.goal_id),
                                "version": version,
                                "goal": goal.model_dump(mode="json"),
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
                tools=tools,
                text_format=CadPlanModelOutput,
            )
        except Exception:
            LOGGER.exception(
                "planning initial model request failed plan_id=%s goal_id=%s",
                plan_id,
                goal.goal_id,
            )
            raise

        for tool_round in range(1, MAX_TOOL_ROUNDS + 1):
            calls = [
                item
                for item in _output(response)
                if _field(item, "type") == "function_call"
            ]
            if not calls:
                break
            LOGGER.info(
                "planning tool round plan_id=%s round=%s call_count=%s",
                plan_id,
                tool_round,
                len(calls),
            )
            outputs: list[dict[str, str]] = []
            for call in calls:
                tool_id = str(_field(call, "name", ""))
                try:
                    parsed_arguments = json.loads(
                        str(_field(call, "arguments", "{}"))
                    )
                    if not isinstance(parsed_arguments, dict):
                        raise ValueError("Tool arguments must be a JSON object.")
                except (TypeError, ValueError, json.JSONDecodeError):
                    result = tool_failure(
                        "TOOL_INPUT_INVALID",
                        "Tool arguments are invalid.",
                    )
                else:
                    if tool_id not in selected_tool_ids:
                        result = tool_failure(
                            "TOOL_NOT_FOUND",
                            "The requested tool is unavailable.",
                        )
                    else:
                        LOGGER.info(
                            "planning tool call plan_id=%s round=%s tool_id=%s",
                            plan_id,
                            tool_round,
                            tool_id,
                        )
                        result = self.tool_executor.execute_sync(
                            tool_id=tool_id,
                            raw_arguments=parsed_arguments,
                            context=tool_context,
                        )
                result_payload = result.model_dump(mode="json")
                data = (
                    {}
                    if isinstance(result, ToolFailure)
                    else result.data.model_dump(mode="json")
                )
                observed_semantic_ids.update(_semantic_ids_from_tool_data(data))
                LOGGER.info(
                    "planning tool result plan_id=%s round=%s tool_id=%s ok=%s "
                    "status=%s "
                    "observed_semantic_id_count=%s",
                    plan_id,
                    tool_round,
                    tool_id,
                    result.ok,
                    data.get("status", "error"),
                    len(observed_semantic_ids),
                )
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(_field(call, "call_id", "")),
                        "output": json.dumps(result_payload, ensure_ascii=False),
                    }
                )
            try:
                response = self.client.responses.parse(
                    model=self.model,
                    instructions=self.instructions,
                    previous_response_id=str(_field(response, "id", "")),
                    input=outputs,
                    tools=tools,
                    text_format=CadPlanModelOutput,
                )
            except Exception:
                LOGGER.exception(
                    "planning follow-up model request failed plan_id=%s round=%s",
                    plan_id,
                    tool_round,
                )
                raise

        if any(
            _field(item, "type") == "function_call" for item in _output(response)
        ):
            LOGGER.warning(
                "planning tool-call limit exceeded plan_id=%s max_rounds=%s",
                plan_id,
                MAX_TOOL_ROUNDS,
            )
            raise WorkflowFailure(
                "PLANNING_TOOL_LIMIT",
                "The planning agent exceeded the read-only tool-call limit.",
            )

        parsed_value = _field(response, "output_parsed")
        if parsed_value is None:
            refusal = _refusal_from(_output(response))
            if refusal:
                LOGGER.info("planning model refused plan_id=%s", plan_id)
                raise WorkflowFailure("AI_REFUSAL", refusal)
            LOGGER.warning("planning model returned no parsed plan plan_id=%s", plan_id)
            raise WorkflowFailure(
                "PLAN_RESPONSE_INVALID",
                "The planning model returned no structured plan.",
            )
        try:
            parsed = (
                parsed_value
                if isinstance(parsed_value, CadPlanModelOutput)
                else CadPlanModelOutput.model_validate(parsed_value)
            )
        except Exception as exc:
            LOGGER.warning(
                "planning model returned invalid plan plan_id=%s error_type=%s",
                plan_id,
                type(exc).__name__,
            )
            raise WorkflowFailure(
                "PLAN_RESPONSE_INVALID",
                "The planning model returned an invalid structured plan.",
            ) from exc

        if any(step.status != "pending" for step in parsed.steps):
            raise WorkflowFailure(
                "PLAN_RESPONSE_INVALID",
                "Every newly generated planning step must be pending.",
            )
        criterion_ids = {
            criterion.criterion_id for criterion in goal.completion_criteria
        }
        referenced_criteria = {
            criterion_id
            for step in parsed.steps
            for criterion_id in step.addresses_criteria
        }
        if any(
            criterion_id not in criterion_ids
            for criterion_id in referenced_criteria
        ):
            raise WorkflowFailure(
                "PLAN_RESPONSE_INVALID",
                "The plan references a completion criterion outside the supplied goal.",
            )
        if any(
            criterion_id not in referenced_criteria
            for criterion_id in criterion_ids
        ):
            raise WorkflowFailure(
                "PLAN_RESPONSE_INVALID",
                "The plan does not collectively address every goal criterion.",
            )
        filtered_bindings = [
            binding
            for binding in parsed.target_bindings
            if binding.semantic_id in observed_semantic_ids
        ]
        LOGGER.info(
            "planning request completed plan_id=%s step_count=%s "
            "target_binding_count=%s retained_target_binding_count=%s "
            "observed_semantic_id_count=%s",
            plan_id,
            len(parsed.steps),
            len(parsed.target_bindings),
            len(filtered_bindings),
            len(observed_semantic_ids),
        )
        return parsed.model_copy(
            update={
                "target_bindings": filtered_bindings
            }
        )
