from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Generic, Literal, Protocol, TypeVar, final

from pydantic import BaseModel, ConfigDict, ValidationError
LOGGER = logging.getLogger(__name__)


# Read-only lookups. Their results depend only on their arguments and on the
# persisted semantic index, which does not change while a job runs.
BATCH_GROUP_READ = "read"

# ModelParams field creation. Safe together because each call re-reads the
# candidate before splicing its own field, so calls apply cleanly in the order
# they were issued, and because distinct field names cannot conflict.
BATCH_GROUP_PARAMETER_CREATE = "parameter_create"

# Closed on purpose. Which tools may share a round is a global batching policy
# decision, not something an individual tool gets to invent -- and a closed set
# turns a typo'd group name into a registration error rather than a tool that
# silently never batches with anything.
BATCH_GROUPS = frozenset({BATCH_GROUP_READ, BATCH_GROUP_PARAMETER_CREATE})


class StrictToolModel(BaseModel):
    """Immutable base schema for every value accepted or returned by a tool.

    The configuration rejects undeclared fields, disables coercion, and prevents
    mutation after validation. Tool schemas therefore form a stable contract
    between an agent, the executor, and an individual tool implementation.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ToolRepository(Protocol):
    """Minimal repository capability made available to tools at execution time."""

    def read_text(self, path: str) -> str:
        """Read and return the text stored at a repository-relative path."""

        ...

    def write_text(self, path: str, content: str) -> None:
        """Write ``content`` to a repository-relative path, replacing any prior value.

        Tools that mutate candidate-scoped source use this narrow capability
        instead of a general-purpose filesystem API. Callers remain responsible
        for restricting ``path`` to the candidate scope they are allowed to write.
        """

        ...

    def find_part_by_name(self, project_id: str, part_name: str) -> dict[str, Any] | None:
        """Return the part row matching ``part_name`` in ``project_id``, if any.

        Case-insensitive, matching the database's own uniqueness rule. Used to
        give a duplicate part name a clean, specific rejection before attempting
        to create it.
        """

        ...

    def create_part(self, project_id: str, part_name: str, part_type: str) -> dict[str, Any]:
        """Create and return a new part row scoped to ``project_id``.

        Tools that create a part use this narrow capability instead of a
        general-purpose database API.
        """

        ...

    def delete_part(self, part_id: str) -> None:
        """Delete the part row identified by ``part_id``.

        Used only to compensate a part creation whose subsequent source write
        failed, so a storage error never leaves an orphaned part row.
        """

        ...

    def queue_geometry_check(self, edit_job_id: str, candidate_sha256: str) -> str:
        """Queue a geometry-check job for an edit job's current candidate.

        The server resolves the candidate's storage path and the immediately
        previous source version from ``edit_job_id`` alone; the caller supplies
        only the exact hash it read the candidate at, so a stale result can be
        detected by comparing it against what is actually persisted. Returns
        the queued ``generation_jobs`` row ID.
        """

        ...

    def generation_job(self, generation_job_id: str) -> dict[str, Any]:
        """Return the current row for a queued/running/terminal generation job.

        Used to poll a geometry-check (or other ``generation_jobs``) job to
        completion.
        """

        ...


@dataclass(frozen=True)
class ToolServices:
    """Container for external services that a tool may use during execution.

    Keeping services in one immutable object makes a tool's dependencies
    explicit and avoids coupling tool definitions to a concrete repository.
    """

    repository: ToolRepository


@dataclass(frozen=True)
class ToolExecutionContext:
    """Immutable identifiers and services that scope one tool invocation.

    The executor supplies this context rather than accepting it in agent-facing
    arguments. ``run_id`` supports tracing, while project, part, and candidate
    identifiers constrain the data the tool is allowed to address.
    """

    run_id: str
    project_id: str
    part_id: str
    candidate_id: str | None
    services: ToolServices

    def __post_init__(self) -> None:
        """Reject contexts missing a non-blank run or project identifier.

        ``part_id`` may be blank for project-scoped tools (for example, a
        tool that creates a new part) since no part identity exists yet at
        call time. Part-scoped tools still require a real ``part_id`` by
        caller discipline; that is not enforced here.
        """

        for label, value in (
            ("run_id", self.run_id),
            ("project_id", self.project_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Tool execution context {label} is required.")
        if not isinstance(self.part_id, str):
            raise ValueError("Tool execution context part_id is required.")


class ToolError(StrictToolModel):
    """Structured failure payload returned when a tool cannot produce data.

    ``code`` is a machine-readable category, ``message`` is safe for the agent,
    and ``retryable`` tells the caller whether retrying may be useful. ``details``
    optionally carries compact diagnostic data, such as validation errors.
    """

    code: str
    message: str
    retryable: bool
    details: dict[str, Any] | None = None


OutputT = TypeVar("OutputT", bound=StrictToolModel)
InputT = TypeVar("InputT", bound=StrictToolModel)


class ToolSuccess(StrictToolModel, Generic[OutputT]):
    """Successful tool result containing output validated against its schema."""

    ok: Literal[True] = True
    data: OutputT


class ToolFailure(StrictToolModel):
    """Unsuccessful tool result containing a standardized ``ToolError``."""

    ok: Literal[False] = False
    error: ToolError


ToolResult = ToolSuccess[OutputT] | ToolFailure


class ToolInputRejected(Exception):
    """Signals that otherwise well-formed input fails tool-specific validation.

    Tool implementations raise this from ``validate_input`` to return a
    controlled validation failure instead of exposing an implementation error.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        """Create a rejection with optional retry guidance and diagnostic details."""

        super().__init__(message)
        self.retryable = retryable
        self.details = details


class ToolExecutionRejected(Exception):
    """Signals an expected execution outcome that is not a success.

    The counterpart of :class:`ToolInputRejected` for the execute stage. An
    unexpected exception out of ``execute`` has its message sanitized before
    it reaches the model, which is right for a bug and wrong for a reportable
    outcome -- so a tool that ran correctly but could not produce the result
    it exists to produce raises this instead, and its ``code`` and message
    are what the agent sees.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        """Create a reportable execution failure with an agent-facing code."""

        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details


def _validation_details(error: ValidationError) -> dict[str, Any]:
    """Convert a Pydantic validation error into a stable, agent-safe payload.

    Input values and Pydantic documentation URLs are deliberately omitted so
    callers receive only the failing location, category, and explanation.
    """

    return {
        "errors": [
            {
                "location": [str(value) for value in item.get("loc", ())],
                "type": str(item.get("type") or "validation_error"),
                "message": str(item.get("msg") or "Invalid value."),
            }
            for item in error.errors(include_url=False, include_input=False)
        ]
    }


def tool_failure(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> ToolFailure:
    """Build a standardized failure result for executor and tool error paths.

    Args:
        code: Stable machine-readable failure category.
        message: Concise explanation suitable for the calling agent.
        retryable: Whether retrying the request may succeed.
        details: Optional structured diagnostics safe to return to the caller.
    """

    return ToolFailure(
        error=ToolError(
            code=code,
            message=message,
            retryable=retryable,
            details=details,
        )
    )


class AgentTool(ABC, Generic[InputT, OutputT]):
    """Base contract for an agent-callable tool with strict I/O schemas.

    Subclasses declare metadata and schema types, then implement ``execute``.
    The final ``run`` method owns the common lifecycle: parse arguments,
    optionally apply domain validation, execute, validate output, and translate
    expected failures into the ``ToolResult`` envelope.
    """

    tool_id: ClassVar[str]
    version: ClassVar[int] = 1
    description: ClassVar[str]
    input_model: ClassVar[type[InputT]]
    output_model: ClassVar[type[OutputT]]
    # Which multi-call batch this tool may join, or None when it must be the
    # only call in its round.
    #
    # A batch is dispatched strictly sequentially, never concurrently, so a
    # group claims exactly two things: the model can decide every call in it
    # without seeing any of their results, and a call cannot be invalidated
    # by another call of the same group running immediately before it. It is
    # a permission for these *tools* to share a round -- it does not assert
    # that any particular set of arguments is independent, which is the
    # reasoning policy's job.
    batch_group: ClassVar[str | None]

    @final
    async def run(
        self,
        raw_arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult[OutputT]:
        """Run the complete validated tool lifecycle for raw agent arguments.

        This method never exposes validation or execution exceptions to the
        caller. Instead, it logs unexpected failures and returns the appropriate
        structured ``ToolFailure``; successful values are revalidated against
        ``output_model`` before being returned.
        """

        try:
            # StrictToolModel is strict=True: validating an already-parsed dict
            # rejects list values for tuple[...] fields (e.g. CreateFeatureInput's
            # `parameters`), even though a JSON array is the natural encoding of
            # a tuple. model_validate_json parses in JSON mode, which does
            # accept it, so a normal agent-supplied JSON array "just works"
            # instead of failing TOOL_INPUT_INVALID on every tuple-typed field.
            tool_input = self.input_model.model_validate_json(
                json.dumps(raw_arguments)
            )
        except (ValidationError, TypeError) as exc:
            return tool_failure(
                "TOOL_INPUT_INVALID",
                "Tool arguments are invalid.",
                details=_validation_details(exc)
                if isinstance(exc, ValidationError)
                else None,
            )

        try:
            normalized_input = await self.normalize_input(tool_input)
            await self.validate_input(normalized_input, context)
            normalized_input = self.input_model.model_validate(normalized_input)
        except ToolInputRejected as exc:
            return tool_failure(
                "TOOL_VALIDATION_FAILED",
                str(exc) or "Tool input failed domain validation.",
                retryable=exc.retryable,
                details=exc.details,
            )
        except Exception as exc:
            LOGGER.exception(
                "tool input validation failed tool_id=%s version=%s run_id=%s "
                "error_type=%s",
                self.tool_id,
                self.version,
                context.run_id,
                type(exc).__name__,
            )
            return tool_failure(
                "TOOL_VALIDATION_FAILED",
                "Tool input failed domain validation.",
            )

        try:
            raw_output = await self.execute(normalized_input, context)
        except ToolExecutionRejected as exc:
            return tool_failure(
                exc.code,
                str(exc) or "Tool execution could not produce a result.",
                retryable=exc.retryable,
                details=exc.details,
            )
        except Exception as exc:
            LOGGER.exception(
                "tool execution failed tool_id=%s version=%s run_id=%s "
                "error_type=%s",
                self.tool_id,
                self.version,
                context.run_id,
                type(exc).__name__,
            )
            return tool_failure(
                "TOOL_EXECUTION_FAILED",
                "Tool execution failed.",
            )

        try:
            output = self.output_model.model_validate(raw_output)
        except ValidationError as exc:
            LOGGER.error(
                "tool output validation failed tool_id=%s version=%s run_id=%s",
                self.tool_id,
                self.version,
                context.run_id,
            )
            return tool_failure(
                "TOOL_OUTPUT_INVALID",
                "Tool returned an invalid result.",
                details=_validation_details(exc),
            )
        return ToolSuccess[OutputT](data=output)

    async def validate_input(
        self,
        tool_input: InputT,
        context: ToolExecutionContext,
    ) -> None:
        """Apply domain-specific validation to the normalized input.

        The default implementation accepts the input as-is. Subclasses may
        raise ``ToolInputRejected`` to reject invalid business state while
        keeping the failure shape consistent and agent-safe.

        Raises:
            ToolInputRejected: If the input fails business-rule validation.
        """

    async def normalize_input(
        self,
        tool_input: InputT,
    ) -> InputT:
        """Return a normalized version of the validated input model.

        Implementations may canonicalize values, fill defaults, or otherwise
        reshape the input before domain validation and execution. The default
        implementation leaves the input unchanged.
        """
        return tool_input

    @abstractmethod
    async def execute(
        self,
        tool_input: InputT,
        context: ToolExecutionContext,
    ) -> OutputT:
        """Perform the tool-specific operation using validated input and context.

        Implementations return data conforming to ``output_model``. The base
        lifecycle validates that returned data before exposing it to the caller.
        """

        raise NotImplementedError
