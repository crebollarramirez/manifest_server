from __future__ import annotations

import unittest
from pathlib import Path

from workers.agent_3d.tools import CheckGeometryTool, ToolRegistry


ROOT = Path(__file__).resolve().parents[1]
EDIT_WORKER_SOURCE = (
    ROOT / "workers" / "agent_3d" / "edit_worker.py"
).read_text(encoding="utf-8")

# The full agent-loop tool set as registered in edit_worker.py before this
# feature, verified unchanged below so registering check_geometry cannot be
# mistaken for having modified another tool's registration.
EXISTING_MUTATION_TOOLS = (
    "IndexSearchTool",
    "IndexGetFeatureTool",
    "CreateFeatureTool",
    "EditFeatureTool",
    "DeleteFeatureTool",
    "CreateCadPartTool",
    "CreateParameterTool",
    "EditParameterTool",
    "DeleteParameterTool",
    "EditCadBuildModelTool",
    "RequestStepCompletionTool",
)


class CheckGeometryCatalogTests(unittest.TestCase):
    def test_check_geometry_tool_is_registered_in_edit_worker(self):
        self.assertIn("CheckGeometryTool", EDIT_WORKER_SOURCE)
        self.assertIn("CheckGeometryTool()", EDIT_WORKER_SOURCE)

    def test_no_existing_tool_registration_was_removed(self):
        for tool_name in EXISTING_MUTATION_TOOLS:
            self.assertIn(
                tool_name,
                EDIT_WORKER_SOURCE,
                f"{tool_name} registration appears to have been removed.",
            )

    def test_check_geometry_registers_cleanly_alongside_every_other_tool(self):
        from workers.agent_3d.tools import (
            CreateCadPartTool,
            CreateFeatureTool,
            CreateParameterTool,
            DeleteFeatureTool,
            DeleteParameterTool,
            EditCadBuildModelTool,
            EditFeatureTool,
            EditParameterTool,
            IndexGetFeatureTool,
            IndexSearchTool,
            RequestStepCompletionTool,
        )

        registry = ToolRegistry()
        tools = [
            IndexSearchTool(),
            IndexGetFeatureTool(),
            CreateFeatureTool(),
            EditFeatureTool(),
            DeleteFeatureTool(),
            CreateCadPartTool(),
            CreateParameterTool(),
            EditParameterTool(),
            DeleteParameterTool(),
            EditCadBuildModelTool(),
            CheckGeometryTool(),
            RequestStepCompletionTool(),
        ]
        registry.register_many(tools)

        catalog = registry.get_definitions(
            allowed_tools=[tool.tool_id for tool in tools]
        )

        tool_ids = [definition["name"] for definition in catalog]
        self.assertIn("check_geometry", tool_ids)
        self.assertEqual(len(tool_ids), len(set(tool_ids)))

    def test_check_geometry_schema_takes_no_llm_provided_fields(self):
        registry = ToolRegistry()
        registry.register(CheckGeometryTool())

        definition = registry.get_definitions(allowed_tools=["check_geometry"])[0]

        self.assertEqual(definition["parameters"]["properties"], {})
        self.assertEqual(definition["parameters"]["required"], [])


if __name__ == "__main__":
    unittest.main()
