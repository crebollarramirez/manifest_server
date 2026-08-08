from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping, Sequence

from .failures import WorkflowFailure
from .planning.agent_contracts import CadGoal, CadPlan, PlanStep
from .planning.agent_trace import AgentTraceContext, AgentTraceWriter, content_sha256
from .planning.prompt_loader import load_agent_reasoning_prompt, load_cad_system_prompt


LOGGER = logging.getLogger(__name__)


def _default_instructions() -> str:
    return f"{load_agent_reasoning_prompt()}\n\n{load_cad_system_prompt()}"


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read one field from a model response item that may be a dict or object."""

    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _reasoning_summary(response: Any) -> Any:
    """Return the API-provided reasoning summary item, if the response has one.

    This is a model-provided summary, never private chain-of-thought -- the
    Responses API does not expose reasoning tokens themselves.
    """

    output = _field(response, "output", [])
    if not isinstance(output, list):
        return None
    for item in output:
        if _field(item, "type") == "reasoning":
            return _field(item, "summary")
    return None


class Agent3D:
    """Model-driven decision component of the future agent loop.

    Agent3D turns one workflow-state snapshot -- goal, plan, active step,
    bounded recent conversation, and current-step tool observations -- into
    one next-action model decision through :meth:`decide`. It does not
    execute tools, mutate goals/plans, select plan steps, or drive workflow
    control; that stays with the orchestrator and the tool executor. It also
    does not loop: a future orchestrator loop is expected to call ``decide``
    repeatedly, once per turn.
    """

    def __init__(
        self,
        *,
        model_client: Any,
        tool_catalog: Sequence[dict[str, Any]],
        model: str | None = None,
        instructions: str | None = None,
        trace_writer: Any | None = None,
    ):
        self._model_client = model_client
        self._tool_catalog = tuple(tool_catalog)
        self.model = (
            model
            or os.environ.get("OPENAI_AGENT_MODEL", "").strip()
            or os.environ.get("OPENAI_MODEL", "").strip()
            or "gpt-5.4-mini"
        )
        if instructions is None:
            reasoning_prompt = load_agent_reasoning_prompt()
            cad_system_prompt = load_cad_system_prompt()
            self.instructions = f"{reasoning_prompt}\n\n{cad_system_prompt}"
            self._reasoning_instructions_sha256 = content_sha256(reasoning_prompt)
            self._cad_system_prompt_sha256 = content_sha256(cad_system_prompt)
        else:
            self.instructions = instructions
            self._reasoning_instructions_sha256 = None
            self._cad_system_prompt_sha256 = None
        self._instructions_sha256 = content_sha256(self.instructions)
        self._tool_catalog_sha256 = content_sha256(
            json.dumps(list(self._tool_catalog), sort_keys=True, default=str)
        )
        self.trace_writer = trace_writer or AgentTraceWriter()

    @property
    def model_client(self) -> Any:
        """Return the injected model client used for reasoning calls."""

        return self._model_client

    @property
    def tool_catalog(self) -> tuple[dict[str, Any], ...]:
        """Return the immutable, allowlisted tool catalog this agent may call."""

        return self._tool_catalog

    def decide(
        self,
        *,
        goal: CadGoal,
        plan: CadPlan,
        active_step: PlanStep,
        trace_context: AgentTraceContext,
        recent_messages: Sequence[Mapping[str, Any]] = (),
        observations: Sequence[Mapping[str, Any]] = (),
    ) -> Any:
        """Make one next-action decision for the active plan step.

        Returns the raw model-client response object unchanged -- its
        ``function_call``/message output items already represent the
        decision, so no parallel decision type is introduced. Neither
        ``goal`` nor ``plan`` is mutated. ``trace_context`` supplies the
        correlation IDs (edit job, turn counters) the orchestrator owns and
        this method cannot derive on its own; it never affects the request
        sent to the model.
        """

        execution_context = {
            "workflow_state": {
                "goal": goal.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "active_step": active_step.model_dump(mode="json"),
            },
            "step_observations": list(observations),
            "recent_conversation": list(recent_messages),
        }

        reasoning_input = [
            {
                "role": "user",
                "content": json.dumps(
                    execution_context,
                    ensure_ascii=False,
                ),
            }
        ]

        # This is the exact payload handed to the model client below -- the
        # trace logs this literal dict rather than a separately reconstructed
        # approximation, so the two can never diverge.
        request = {
            "model": self.model,
            "instructions": self.instructions,
            "input": reasoning_input,
            "tools": list(self._tool_catalog),
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "reasoning": {
                "effort": "medium",
            },
        }

        correlation = {
            "edit_job_id": trace_context.edit_job_id,
            "goal_id": str(goal.goal_id),
            "plan_id": str(plan.plan_id),
            "step_id": active_step.step_id,
            "agent_turn": trace_context.agent_turn,
            "step_turn": trace_context.step_turn,
        }

        try:
            self.trace_writer.log(
                "llm.request",
                **correlation,
                request=request,
                instructions_sha256=self._instructions_sha256,
                reasoning_instructions_sha256=self._reasoning_instructions_sha256,
                cad_system_prompt_sha256=self._cad_system_prompt_sha256,
                tool_catalog_sha256=self._tool_catalog_sha256,
            )
        except Exception as exc:
            raise WorkflowFailure(
                "AGENT_PROMPT_LOG_WRITE_FAILED",
                "The Agent3D reasoning trace could not be written.",
                details={"exception_type": type(exc).__name__},
            ) from exc

        try:
            response = self._model_client.responses.create(**request)
        except Exception as exc:
            LOGGER.exception(
                "agent reasoning request failed goal_id=%s step_id=%s",
                goal.goal_id,
                active_step.step_id,
            )
            raise WorkflowFailure(
                "AGENT_DECISION_FAILED",
                "Agent3D could not produce a next-action decision.",
            ) from exc

        try:
            usage = _field(response, "usage")
            output_token_details = _field(usage, "output_tokens_details")
            self.trace_writer.log(
                "llm.response",
                **correlation,
                response=response,
                response_id=_field(response, "id"),
                status=_field(response, "status"),
                input_tokens=_field(usage, "input_tokens"),
                output_tokens=_field(usage, "output_tokens"),
                reasoning_tokens=_field(output_token_details, "reasoning_tokens"),
                reasoning_summary=_reasoning_summary(response),
            )
        except Exception:
            # The model call already succeeded; losing this debug artifact
            # must not discard that (possibly billed) work.
            LOGGER.warning(
                "agent reasoning response trace could not be written "
                "goal_id=%s step_id=%s",
                goal.goal_id,
                active_step.step_id,
                exc_info=True,
            )

        return response
