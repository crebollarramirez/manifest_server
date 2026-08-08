from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..base import AgentTool, StrictToolModel, ToolExecutionContext


class RequestStepCompletionInput(StrictToolModel):
    """Arguments for signaling that the active plan step is complete.

    ``summary`` is a concise, human-readable account of what was done to
    satisfy the active step's objective.
    """

    summary: str = Field(min_length=1, max_length=2_000)


class RequestStepCompletionOutput(StrictToolModel):
    """Acknowledgement that a step-completion request was received.

    This MVP tool performs no validation and does not itself advance or
    persist plan state -- it only echoes the request back. Marking the step
    completed and moving the plan forward is the orchestrator's
    responsibility once the future agent loop executes tool calls and acts
    on this tool's result.
    """

    status: Literal["ok"] = "ok"
    summary: str


class RequestStepCompletionTool(
    AgentTool[RequestStepCompletionInput, RequestStepCompletionOutput]
):
    """Signal that the active plan step is complete.

    MVP scope: this tool does not verify the completion claim and does not
    mutate or persist any plan state itself -- it only accepts a summary and
    acknowledges it. Advancing to the next step and marking the current one
    completed is left to the orchestrator once the future agent loop exists;
    today nothing executes this tool automatically.
    """

    tool_id = "request_step_completion"
    version = 1
    description = (
        "Signal that the active plan step is complete, with a brief summary "
        "of what was done. This MVP tool does not verify the claim or change "
        "any plan state itself."
    )
    input_model = RequestStepCompletionInput
    output_model = RequestStepCompletionOutput

    async def execute(
        self,
        tool_input: RequestStepCompletionInput,
        _context: ToolExecutionContext,
    ) -> RequestStepCompletionOutput:
        """Acknowledge the completion claim without validating or persisting it."""

        return RequestStepCompletionOutput(summary=tool_input.summary)
