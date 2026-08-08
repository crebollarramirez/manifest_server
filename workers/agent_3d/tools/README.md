# CAD Agent Tools

This package defines the boundary between an AI agent and the CAD semantic-index
capabilities it can call. It contains the schema contract, concrete read-only
index tools, registration safeguards, and the execution entry point. It does not
contain agent decision-making or CAD editing logic.

For the `check_geometry` tool specifically (`geometry/geometry_tools.py`) and
the geometry-check job it queues, see
[`workers/cad_validator/GEOMETRY_CHECK.md`](../../cad_validator/GEOMETRY_CHECK.md).

## Recommended reading order

1. **`base.py`** — Start here to understand the universal tool contract. It
   defines strict Pydantic schemas, execution context and service dependencies,
   the success/failure envelope, and the lifecycle that every tool follows from
   raw arguments through validated output.
2. **`index_tools.py`** — Read this next for the concrete capabilities currently
   offered to an agent. It turns the semantic index into two read-only calls:
   discovery by descriptive text and exact feature-context lookup. It also owns
   the defensive parsing and matching behavior specific to that index format.
3. **`registry.py`** — Then see how tool instances become available at runtime.
   This module prevents malformed definitions, produces function-call schemas
   for a chosen set of tools, resolves tool IDs, and provides async and sync
   execution entry points with invocation logging.
4. **`__init__.py`** — Read last when integrating from another package. It is the
   deliberately small public import surface that re-exports supported schemas,
   tools, errors, and runtime helpers without requiring callers to know the
   module structure.

## How the pieces fit

Concrete tools inherit the shared contract in `base.py`. A `ToolRegistry` accepts
only valid instances, a `Toolbox` exposes their agent-facing function schemas,
and a `ToolExecutor` resolves an invoked tool and runs its common lifecycle.
`index_tools.py` is the current implementation layer within that framework.

## Strict schema contract

Every agent-facing input, successful output, and nested output value must inherit
from `StrictToolModel`. Its Pydantic configuration is deliberately restrictive:

- **No extra fields:** arguments not declared by the input model are rejected.
- **No type coercion:** a value must already have the declared type; for example,
  a string such as `"3"` is not accepted for an integer field.
- **Immutable values:** validated models cannot be modified after creation.

The registry enforces these requirements for both `input_model` and
`output_model`. It also requires every concrete tool to have a lowercase,
underscore-separated ID with a domain prefix (for example, `index_search`), a
positive version, a non-empty description, and an `execute` implementation.
Tools cannot override the shared `run` lifecycle. That lifecycle validates the
arguments, invokes optional domain validation, executes the tool, validates the
returned data, and wraps the result in either `ToolSuccess` or `ToolFailure`.

## Example tool structure

This abbreviated example shows the required shape of a new read-only tool. The
tool-specific `execute` method receives already-validated input and must return
an instance compatible with its declared output schema.

```python
from .base import AgentTool, StrictToolModel, ToolExecutionContext


class PartSummaryInput(StrictToolModel):
    include_feature_count: bool = False


class PartSummaryOutput(StrictToolModel):
    part_id: str
    part_name: str
    feature_count: int | None = None


class PartSummaryTool(AgentTool[PartSummaryInput, PartSummaryOutput]):
    tool_id = "part_summary"
    version = 1
    description = "Return summary information for the selected CAD part."
    input_model = PartSummaryInput
    output_model = PartSummaryOutput

    async def execute(
        self,
        tool_input: PartSummaryInput,
        context: ToolExecutionContext,
    ) -> PartSummaryOutput:
        return PartSummaryOutput(
            part_id=context.part_id,
            part_name="Bracket",
            feature_count=4 if tool_input.include_feature_count else None,
        )
```

`ToolExecutionContext` is supplied by the application, not the LLM. It carries
the run, project, and selected-part identifiers as well as approved services.
An LLM can provide only the fields in `PartSummaryInput`.

## Example LLM tool call

`Toolbox.get_tool_definitions()` converts `PartSummaryInput` into the strict
function schema sent to the LLM. When the LLM decides to call the tool, its
function-call payload should name the registered tool ID and provide arguments
that exactly match that input schema:

```json
{
  "type": "function",
  "name": "part_summary",
  "arguments": "{\"include_feature_count\": true}"
}
```

The calling application parses `arguments` into a dictionary, then invokes the
executor with application-owned context:

```python
result = await executor.execute(
    tool_id="part_summary",
    raw_arguments={"include_feature_count": True},
    context=context,
)
```

For the current `index_search` tool, a valid LLM function-call payload follows
the same form:

```json
{
  "type": "function",
  "name": "index_search",
  "arguments": "{\"query\": \"mounting hole\", \"limit\": 5}"
}
```

Unknown fields, missing required fields, and incorrectly typed values are not
silently corrected; they produce a structured `TOOL_INPUT_INVALID` failure.
