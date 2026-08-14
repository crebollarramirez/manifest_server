from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import Iterable
from typing import Any

from openai.lib._pydantic import to_strict_json_schema

from .base import (
    BATCH_GROUPS,
    AgentTool,
    StrictToolModel,
    ToolExecutionContext,
    ToolFailure,
    ToolResult,
    tool_failure,
)


# Distinguishes "declared batch_group = None" from "never declared one".
_UNDECLARED = object()


LOGGER = logging.getLogger(__name__)
TOOL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)+$")


class InvalidToolDefinitionError(ValueError):
    """Raised when a tool does not meet the required agent-tool contract."""

    pass


class DuplicateToolError(ValueError):
    """Raised when registration or selection attempts to reuse a tool ID."""

    pass


class UnknownToolError(LookupError):
    """Raised when a requested tool ID is absent from a registry."""

    pass


def _validate_tool(tool: AgentTool[Any, Any]) -> None:
    """Verify that a tool instance has safe metadata, strict schemas, and lifecycle.

    Registration accepts only concrete ``AgentTool`` subclasses with a
    descriptive identifier, positive version, non-empty description, immutable
    strict input/output models, an ``execute`` implementation, and the inherited
    final ``run`` method.
    """

    if not isinstance(tool, AgentTool):
        raise InvalidToolDefinitionError("Registered tools must inherit AgentTool.")
    tool_type = type(tool)
    tool_id = getattr(tool_type, "tool_id", None)
    if not isinstance(tool_id, str) or not TOOL_ID_PATTERN.fullmatch(tool_id):
        raise InvalidToolDefinitionError(
            "Tool IDs must be descriptive lowercase names with a domain prefix "
            "separated by underscores."
        )
    version = getattr(tool_type, "version", None)
    if type(version) is not int or version < 1:
        raise InvalidToolDefinitionError("Tool versions must be positive integers.")
    # A sentinel rather than a None default: None is a *legal declared value*
    # here (it means "never batches"), so a plain getattr default could not
    # tell an explicit None from a tool that forgot to declare one at all.
    batch_group = getattr(tool_type, "batch_group", _UNDECLARED)
    if batch_group is _UNDECLARED or not (
        batch_group is None or batch_group in BATCH_GROUPS
    ):
        raise InvalidToolDefinitionError(
            "Tool batch_group must be None or one of the declared batch "
            f"groups: {', '.join(sorted(BATCH_GROUPS))}."
        )
    description = getattr(tool_type, "description", None)
    if not isinstance(description, str) or not description.strip():
        raise InvalidToolDefinitionError("Tool descriptions must be non-empty.")
    for attribute in ("input_model", "output_model"):
        model = getattr(tool_type, attribute, None)
        if not isinstance(model, type) or not issubclass(model, StrictToolModel):
            raise InvalidToolDefinitionError(
                f"Tool {attribute} must inherit StrictToolModel."
            )
        config = model.model_config
        if (
            config.get("extra") != "forbid"
            or config.get("strict") is not True
            or config.get("frozen") is not True
        ):
            raise InvalidToolDefinitionError(
                f"Tool {attribute} must retain the strict tool-model configuration."
            )
    execute = getattr(tool_type, "execute", None)
    if inspect.isabstract(tool_type) or not callable(execute):
        raise InvalidToolDefinitionError("Tools must implement execute().")
    if tool_type.run is not AgentTool.run:
        raise InvalidToolDefinitionError("Tools cannot override final run().")


class ToolRegistry:
    """Validated in-memory catalog of tool instances addressable by tool ID."""

    def __init__(self) -> None:
        """Create an empty registry."""

        self._tools: dict[str, AgentTool[Any, Any]] = {}

    def register(self, tool: AgentTool[Any, Any]) -> None:
        """Validate and add one tool, rejecting invalid definitions and duplicate IDs."""

        _validate_tool(tool)
        if tool.tool_id in self._tools:
            raise DuplicateToolError(f'Tool ID "{tool.tool_id}" is already registered.')
        self._tools[tool.tool_id] = tool

    def register_many(self, tools: Iterable[AgentTool[Any, Any]]) -> None:
        """Register each tool in iteration order using the same checks as ``register``."""

        for tool in tools:
            self.register(tool)

    def get(self, tool_id: str) -> AgentTool[Any, Any]:
        """Return the registered tool for ``tool_id`` or raise ``UnknownToolError``."""

        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise UnknownToolError(f'Unknown tool ID "{tool_id}".') from exc

    def get_definitions(self, allowed_tools: Iterable[str]) -> list[dict[str, Any]]:
        """Build strict function-call schemas for an explicit tool allowlist.

        ``allowed_tools`` is required and is the sole source of which registered
        tools are exposed; a tool absent from it never appears in the result.
        Each definition uses the tool ID as its function name and serializes its
        input Pydantic model as the function parameters schema, in the order
        supplied by ``allowed_tools``.

        The schema is rewritten with ``to_strict_json_schema`` -- OpenAI's own
        conversion, also used internally by its SDK -- so every property is
        listed in ``required`` with optionality expressed as a nullable type,
        satisfying strict mode regardless of which fields a tool's Pydantic
        input model gives a default. Plain ``model_json_schema()`` only
        includes defaulted fields in ``required`` when they lack a default,
        which OpenAI's strict mode rejects.
        """

        selected = tuple(allowed_tools)
        for tool_id in selected:
            if not isinstance(tool_id, str) or not tool_id:
                raise InvalidToolDefinitionError(
                    "Allowed tool IDs must be non-empty strings."
                )
        if len(selected) != len(set(selected)):
            raise DuplicateToolError("allowed_tools cannot repeat a tool ID.")
        return [
            {
                "type": "function",
                "name": tool.tool_id,
                "description": tool.description,
                "parameters": to_strict_json_schema(tool.input_model),
                "strict": True,
            }
            for tool_id in selected
            for tool in [self.get(tool_id)]
        ]


class Toolbox:
    """Read-only facade that exposes a selected set of tool call definitions."""

    def __init__(self, registry: ToolRegistry):
        """Bind the toolbox to the registry that owns available tool definitions."""

        self._registry = registry

    def get_tool_definitions(self, tool_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Return definitions for a unique requested tool selection.

        The iterable is materialized to detect duplicate IDs before any schema is
        returned; unknown IDs are reported by the underlying registry.
        """

        selected = tuple(tool_ids)
        if len(selected) != len(set(selected)):
            raise DuplicateToolError("A toolbox selection cannot repeat a tool ID.")
        return self._registry.get_definitions(selected)


class ToolExecutor:
    """Dispatch registered tools and provide consistent invocation-level logging."""

    def __init__(self, registry: ToolRegistry):
        """Bind the executor to the registry used to resolve requested tool IDs."""

        self._registry = registry

    async def execute(
        self,
        *,
        tool_id: str,
        raw_arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult[Any]:
        """Resolve and run one tool asynchronously with raw arguments and context.

        An unknown tool becomes a ``TOOL_NOT_FOUND`` result. All other lifecycle
        validation and failure translation is delegated to ``AgentTool.run``.
        """

        try:
            tool = self._registry.get(tool_id)
        except UnknownToolError:
            return tool_failure(
                "TOOL_NOT_FOUND",
                "The requested tool is unavailable.",
            )
        LOGGER.info(
            "tool execution started tool_id=%s version=%s run_id=%s",
            tool.tool_id,
            tool.version,
            context.run_id,
        )
        result = await tool.run(raw_arguments, context)
        LOGGER.info(
            "tool execution finished tool_id=%s version=%s run_id=%s ok=%s",
            tool.tool_id,
            tool.version,
            context.run_id,
            result.ok,
        )
        return result

    def execute_sync(
        self,
        *,
        tool_id: str,
        raw_arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult[Any]:
        """Run ``execute`` from synchronous code when no event loop is active.

        Calling this method from asynchronous code would create a nested event
        loop, so it raises ``RuntimeError`` and callers must await ``execute``.
        """

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.execute(
                    tool_id=tool_id,
                    raw_arguments=raw_arguments,
                    context=context,
                )
            )
        raise RuntimeError(
            "ToolExecutor.execute_sync() cannot run inside an active event loop."
        )

    def batch_group(self, tool_id: str) -> str | None:
        """Return the batch group ``tool_id`` may share a multi-call batch with.

        ``None`` means the tool must be the only call in its round. Fails
        closed (``None``) for an unknown tool ID -- defensive only, since
        callers reach this only after the tool ID has already passed an
        allowlist check.
        """

        try:
            tool = self._registry.get(tool_id)
        except UnknownToolError:
            return None
        return tool.batch_group
