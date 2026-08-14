from __future__ import annotations

import ast
from typing import Literal

from pydantic import Field

from workers.commons.storage import INITIAL_CAD_SOURCE, cad_source_storage_path

from ..hashing import source_hash
from ..base import AgentTool, StrictToolModel, ToolExecutionContext, ToolInputRejected


def _skeleton_source() -> str:
    """Generate a minimal, valid, empty CAD part source.

    Includes the required runtime import, an empty ``ModelParams`` dataclass
    (no placeholder field -- ``feature_generation._source_layout`` tolerates a
    zero-field ``ModelParams``), and a trivial but valid ``build_model``. This
    is enough for ``CreateFeatureTool`` to add features to immediately.
    """

    return (
        f"{INITIAL_CAD_SOURCE}\n"
        "@dataclass(frozen=True)\n"
        "class ModelParams:\n"
        "    pass\n"
        "\n"
        "\n"
        "def build_model(params: ModelParams):\n"
        '    return cq.Workplane("XY")\n'
    )


class CreateCadPartInput(StrictToolModel):
    """Strict, LLM-facing arguments for creating one new CAD part.

    Project identity comes from ``ToolExecutionContext.project_id``, never
    from this field. The new part's identity (``part_id``) is an output of
    the tool, not an input.
    """

    part_name: str = Field(min_length=1, max_length=200)


class CreateCadPartOutput(StrictToolModel):
    """Structured result of a successful ``CreateCadPartTool`` invocation."""

    status: Literal["created"]
    part_id: str
    project_id: str
    part_name: str
    part_type: Literal["cad"]
    source_path: str
    content_hash: str
    summary: str


class CreateCadPartTool(AgentTool[CreateCadPartInput, CreateCadPartOutput]):
    """Create one new CAD part with a minimal, valid, empty model.py skeleton.

    This tool creates exactly one new part row in the current project and
    writes it an initial ``model.py`` containing the required runtime import,
    an empty ``ModelParams`` dataclass, and a trivial but valid
    ``build_model``. It does not create model parameters or semantic
    features -- the skeleton is deliberately empty so ``CreateFeatureTool``
    (and, later, a parameter-creation tool) can build on it. It writes
    directly to the part's canonical source path: unlike ``CreateFeatureTool``,
    there is no existing accepted source to protect for a part that did not
    exist before this call.
    """

    tool_id = "create_cad_part"
    version = 1
    description = (
        "Create one new CAD part in the current project, with a minimal, "
        "valid, empty model.py (required imports, an empty ModelParams "
        "dataclass, and a trivial build_model). It does not create model "
        "parameters or semantic features -- use create_feature afterward to "
        "add features to the new part."
    )
    input_model = CreateCadPartInput
    output_model = CreateCadPartOutput
    batch_group = None

    async def normalize_input(self, tool_input: CreateCadPartInput) -> CreateCadPartInput:
        return tool_input.model_copy(update={"part_name": tool_input.part_name.strip()})

    async def validate_input(
        self,
        tool_input: CreateCadPartInput,
        context: ToolExecutionContext,
    ) -> None:
        if not tool_input.part_name:
            raise ToolInputRejected(
                "part_name must not be blank.",
                details={"field": "part_name"},
            )

        existing = context.services.repository.find_part_by_name(
            context.project_id, tool_input.part_name
        )
        if existing is not None:
            raise ToolInputRejected(
                f'A part named "{tool_input.part_name}" already exists in this project.',
                details={"field": "part_name", "reason": "duplicate"},
            )

    async def execute(
        self,
        tool_input: CreateCadPartInput,
        context: ToolExecutionContext,
    ) -> CreateCadPartOutput:
        repository = context.services.repository
        part = repository.create_part(context.project_id, tool_input.part_name, "cad")

        source = _skeleton_source()
        ast.parse(source)  # self-check: the fixed template must always be valid Python
        path = cad_source_storage_path(part["project_id"], part["id"])

        try:
            repository.write_text(path, source)
        except Exception:
            repository.delete_part(part["id"])
            raise

        return CreateCadPartOutput(
            status="created",
            part_id=part["id"],
            project_id=part["project_id"],
            part_name=part["part_name"],
            part_type="cad",
            source_path=path,
            content_hash=source_hash(source),
            summary=(
                f'Created part "{part["part_name"]}" with a blank CAD skeleton '
                "(ModelParams + build_model). Use create_feature to add "
                "features to it."
            ),
        )
