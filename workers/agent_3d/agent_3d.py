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


# Model families known to support the Responses API's `reasoning.context`
# parameter (persisted reasoning across `previous_response_id` turns). Only
# GPT-5.6 and later support it today; sending it to a model that doesn't
# support it risks an API rejection, so this stays an explicit allowlist
# rather than a version comparison -- extending it is a one-line addition.
_REASONING_CONTEXT_MODEL_PREFIXES = ("gpt-5.6",)


def _supports_reasoning_context(model: str) -> bool:
    return (model or "").strip().lower().startswith(_REASONING_CONTEXT_MODEL_PREFIXES)


class Agent3D:
    """Model-driven reasoning component of the agent loop.

    Agent3D reasons about one active plan step across a single continuous
    Responses API chain: :meth:`start_step` opens the chain with the full
    workflow-state snapshot (goal, plan, active step, bounded recent
    conversation, current-step tool observations), and :meth:`continue_step`
    carries that same chain forward with each round's tool results via
    ``previous_response_id``. It does not execute tools, mutate goals/plans,
    select plan steps, or drive workflow control; that stays with the
    orchestrator and the tool executor. It also does not loop on its own --
    the orchestrator alone decides when to call ``start_step`` vs
    ``continue_step``, and when to stop calling either.
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

    def start_step(
        self,
        *,
        goal: CadGoal,
        plan: CadPlan,
        active_step: PlanStep,
        trace_context: AgentTraceContext,
        recent_messages: Sequence[Mapping[str, Any]] = (),
        observations: Sequence[Mapping[str, Any]] = (),
        project_inventory: Mapping[str, Any] | None = None,
        validation_feedback: Mapping[str, Any] | None = None,
    ) -> Any:
        """Start a new reasoning chain for ``active_step``.

        Sends the full workflow-state snapshot -- goal, plan, active step,
        the project inventory roster, current-step observations, and recent
        conversation -- as a single input item, with no
        ``previous_response_id``. This is the only point at which that
        snapshot is sent for a given step's chain; every subsequent round for
        this step goes through :meth:`continue_step` instead. Returns the raw
        model-client response object unchanged. Neither ``goal`` nor ``plan``
        is mutated.

        ``project_inventory`` is what parts/features already exist -- the
        orchestrator assembles it (other parts from the project's semantic
        index, the part being edited from a live scan of its candidate
        source) so the model doesn't need a tool round to discover it. When
        omitted, an empty roster is sent instead of the field being absent,
        so the model always sees the same shape.

        ``validation_feedback`` carries the diagnostics from a validation the
        candidate already failed -- either this step's own earlier attempt
        after a restart, or, for a repair step, the whole-plan failure that
        caused the step to exist. It is a separate field rather than a
        synthetic observation because observations mean "tool calls you
        made", and a validator verdict has a different provenance the model
        should not be misled about. Empty dict when absent, so the shape is
        stable.
        """

        execution_context = {
            "workflow_state": {
                "goal": goal.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "active_step": active_step.model_dump(mode="json"),
                "project_inventory": (
                    dict(project_inventory)
                    if project_inventory is not None
                    # Keep every key the orchestrator's real inventory emits,
                    # so a caller that omits the inventory hands the model the
                    # same shape with empty values rather than a different one.
                    else {
                        "current_part": {
                            "part_id": "",
                            "part_name": "",
                            "features": [],
                            "parameters": [],
                            "build_model": "",
                            "geometry": {},
                        },
                        "other_parts": [],
                    }
                ),
                "validation_feedback": (
                    dict(validation_feedback)
                    if validation_feedback is not None
                    else {}
                ),
            },
            "step_observations": list(observations),
            "recent_conversation": list(recent_messages),
        }
        input_items = [
            {
                "role": "user",
                "content": json.dumps(execution_context, ensure_ascii=False),
            }
        ]
        return self._send_reasoning_request(
            goal=goal,
            plan=plan,
            active_step=active_step,
            trace_context=trace_context,
            input_items=input_items,
            previous_response_id=None,
        )

    def continue_step(
        self,
        *,
        goal: CadGoal,
        plan: CadPlan,
        active_step: PlanStep,
        trace_context: AgentTraceContext,
        previous_response_id: str,
        tool_outputs: Sequence[Mapping[str, Any]],
    ) -> Any:
        """Continue ``active_step``'s reasoning chain with this round's tool
        results.

        ``tool_outputs`` must already be a sequence of Responses API
        ``function_call_output`` items (``{"type": "function_call_output",
        "call_id": ..., "output": <json string>}``) -- this method does not
        know about ``ToolResult``/``ToolFailure`` shapes; the orchestrator
        builds these. ``goal``/``plan``/``active_step`` are used only for
        trace correlation, not re-serialized into the request -- the chain's
        own server-side history already carries that context forward.
        Returns the raw model-client response object unchanged.
        """

        return self._send_reasoning_request(
            goal=goal,
            plan=plan,
            active_step=active_step,
            trace_context=trace_context,
            input_items=list(tool_outputs),
            previous_response_id=previous_response_id,
        )

    def _reasoning_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {"effort": "medium"}
        if _supports_reasoning_context(self.model):
            config["context"] = "all_turns"
        return config

    def _send_reasoning_request(
        self,
        *,
        goal: CadGoal,
        plan: CadPlan,
        active_step: PlanStep,
        trace_context: AgentTraceContext,
        input_items: list[dict[str, Any]],
        previous_response_id: str | None,
    ) -> Any:
        """Build, trace, and send one Responses API request, and trace and
        return the raw response unchanged.

        This is the single place request-building, tracing, and error
        handling live -- :meth:`start_step` and :meth:`continue_step` differ
        only in what ``input_items``/``previous_response_id`` they supply.
        """

        # This is the exact payload handed to the model client below -- the
        # trace logs this literal dict rather than a separately reconstructed
        # approximation, so the two can never diverge.
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": self.instructions,
            "input": input_items,
            "tools": list(self._tool_catalog),
            "tool_choice": "required",
            # Load-bearing: this is what permits multi-call rounds at all.
            # The orchestrator's batch groups decide which calls may actually
            # share a round; turning this off would silently stop all
            # batching, raising turn consumption with no error anywhere.
            "parallel_tool_calls": True,
            "reasoning": self._reasoning_config(),
        }
        if previous_response_id is not None:
            request["previous_response_id"] = previous_response_id

        correlation = {
            "edit_job_id": trace_context.edit_job_id,
            "goal_id": str(goal.goal_id),
            "plan_id": str(plan.plan_id),
            "step_id": active_step.step_id,
            "agent_turn": trace_context.agent_turn,
            "step_attempt": trace_context.step_attempt,
            "reasoning_round": trace_context.reasoning_round,
            "previous_response_id": previous_response_id,
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
