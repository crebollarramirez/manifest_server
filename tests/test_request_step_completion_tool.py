from __future__ import annotations

import unittest

from workers.agent_3d.tools import (
    RequestStepCompletionInput,
    RequestStepCompletionTool,
    ToolExecutionContext,
    ToolFailure,
    ToolRegistry,
    ToolServices,
    ToolSuccess,
)


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "33333333-3333-4333-8333-333333333333"


def context() -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        part_id="",
        candidate_id=None,
        services=ToolServices(repository=object()),
    )


class RequestStepCompletionToolTests(unittest.IsolatedAsyncioTestCase):
    def test_registers_cleanly(self):
        registry = ToolRegistry()

        registry.register(RequestStepCompletionTool())

        self.assertEqual(registry.get("request_step_completion").tool_id, "request_step_completion")

    async def test_acknowledges_a_valid_summary(self):
        tool = RequestStepCompletionTool()

        result = await tool.run({"summary": "Added three drainage holes."}, context())

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.status, "ok")
        self.assertEqual(result.data.summary, "Added three drainage holes.")

    async def test_rejects_an_empty_summary(self):
        tool = RequestStepCompletionTool()

        result = await tool.run({"summary": ""}, context())

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_INPUT_INVALID")

    async def test_rejects_missing_or_extra_fields(self):
        tool = RequestStepCompletionTool()

        for arguments in ({}, {"summary": "ok", "extra": True}):
            with self.subTest(arguments=arguments):
                result = await tool.run(arguments, context())
                self.assertIsInstance(result, ToolFailure)
                self.assertEqual(result.error.code, "TOOL_INPUT_INVALID")

    def test_input_model_rejects_over_length_summary(self):
        with self.assertRaises(Exception):
            RequestStepCompletionInput(summary="x" * 2_001)


if __name__ == "__main__":
    unittest.main()
