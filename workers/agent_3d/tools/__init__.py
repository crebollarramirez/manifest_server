"""Public import surface for CAD-agent tool schemas, implementations, and runtime helpers.

Import from this package when consumers need the supported tool contract without
depending on its internal module layout.
"""

from .base import (
    AgentTool,
    StrictToolModel,
    ToolError,
    ToolExecutionContext,
    ToolFailure,
    ToolInputRejected,
    ToolResult,
    ToolServices,
    ToolSuccess,
)
from .build_model.build_model_tools import (
    EditCadBuildModelInput,
    EditCadBuildModelOutput,
    EditCadBuildModelTool,
)
from .feature.feature_tools import (
    CreateFeatureInput,
    CreateFeatureOutput,
    CreateFeatureTool,
    DeleteFeatureInput,
    DeleteFeatureOutput,
    DeleteFeatureTool,
    EditFeatureInput,
    EditFeatureOutput,
    EditFeatureTool,
    FeatureDependencyInput,
)
from .geometry.geometry_tools import (
    CheckGeometryInput,
    CheckGeometryOutput,
    CheckGeometryTool,
    GeometryBoundingBox,
    GeometryDelta,
    GeometryFacts,
)
from .index.index_tools import IndexGetFeatureTool, IndexSearchTool
from .parameter.parameter_tools import (
    CreateParameterInput,
    CreateParameterOutput,
    CreateParameterTool,
    DeleteParameterInput,
    DeleteParameterOutput,
    DeleteParameterTool,
    EditParameterInput,
    EditParameterOutput,
    EditParameterTool,
)
from .part.part_tools import CreateCadPartInput, CreateCadPartOutput, CreateCadPartTool
from .step.step_tools import (
    RequestStepCompletionInput,
    RequestStepCompletionOutput,
    RequestStepCompletionTool,
)
from .registry import (
    DuplicateToolError,
    InvalidToolDefinitionError,
    ToolExecutor,
    Toolbox,
    ToolRegistry,
    UnknownToolError,
)

__all__ = [
    "AgentTool",
    "CheckGeometryInput",
    "CheckGeometryOutput",
    "CheckGeometryTool",
    "CreateCadPartInput",
    "CreateCadPartOutput",
    "CreateCadPartTool",
    "CreateFeatureInput",
    "CreateFeatureOutput",
    "CreateFeatureTool",
    "CreateParameterInput",
    "CreateParameterOutput",
    "CreateParameterTool",
    "DeleteFeatureInput",
    "DeleteFeatureOutput",
    "DeleteFeatureTool",
    "DeleteParameterInput",
    "DeleteParameterOutput",
    "DeleteParameterTool",
    "DuplicateToolError",
    "EditCadBuildModelInput",
    "EditCadBuildModelOutput",
    "EditCadBuildModelTool",
    "EditFeatureInput",
    "EditFeatureOutput",
    "EditFeatureTool",
    "EditParameterInput",
    "EditParameterOutput",
    "EditParameterTool",
    "FeatureDependencyInput",
    "GeometryBoundingBox",
    "GeometryDelta",
    "GeometryFacts",
    "IndexGetFeatureTool",
    "IndexSearchTool",
    "InvalidToolDefinitionError",
    "RequestStepCompletionInput",
    "RequestStepCompletionOutput",
    "RequestStepCompletionTool",
    "StrictToolModel",
    "ToolError",
    "ToolExecutionContext",
    "ToolExecutor",
    "ToolFailure",
    "ToolInputRejected",
    "ToolRegistry",
    "ToolResult",
    "ToolServices",
    "ToolSuccess",
    "Toolbox",
    "UnknownToolError",
]
