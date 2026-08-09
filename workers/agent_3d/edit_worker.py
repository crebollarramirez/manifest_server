from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any


# Script execution adds only ``workers/agent_3d`` to sys.path. Add ``workers/``
# so ``agent_3d`` itself is importable, and the workspace root so shared
# ``workers.indexer`` imports work outside Docker too.
WORKERS_ROOT = str(Path(__file__).resolve().parents[1])
WORKSPACE_ROOT = str(Path(__file__).resolve().parents[2])
for _root in (WORKERS_ROOT, WORKSPACE_ROOT):
    if _root not in sys.path:
        sys.path.insert(0, _root)


POLL_INTERVAL_SECONDS = float(
    os.environ.get("CAD_EDITOR_JOB_POLL_INTERVAL_SECONDS", "2")
)
LEASE_SECONDS = int(os.environ.get("CAD_EDITOR_LEASE_SECONDS", "300"))
WORKER_ID = os.environ.get("CAD_EDITOR_WORKER_ID") or f"cad-editor-{uuid.uuid4()}"
LOG_LEVEL = os.environ.get("CAD_EDITOR_LOG_LEVEL", "INFO").upper()


def configure_logging() -> None:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def build_runtime() -> tuple[Any, Any]:
    """Construct one long-lived editor runtime and its shared API client."""

    from openai import OpenAI
    from supabase import create_client

    from workers.agent_3d.agent_3d import Agent3D
    from workers.agent_3d.orchestrator import EditWorkflowOrchestrator
    from workers.agent_3d.planning.agent_trace import AgentTraceWriter
    from workers.agent_3d.planning.goal_creator import GoalCreator
    from workers.agent_3d.planning.planning_agent import PLANNING_TOOL_IDS, PlanningAgent
    from workers.agent_3d.repository import SupabaseEditRepository
    from workers.agent_3d.tools import (
        CheckGeometryTool,
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
        ToolExecutor,
        Toolbox,
        ToolRegistry,
    )

    supabase = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    repository = SupabaseEditRepository(supabase)
    openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    goal_creator = GoalCreator(client=openai)
    registered_tools = [
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
    tool_registry = ToolRegistry()
    tool_registry.register_many(registered_tools)
    toolbox = Toolbox(tool_registry)
    agent_tool_executor = ToolExecutor(tool_registry)
    planning_agent = PlanningAgent(
        toolbox,
        agent_tool_executor,
        allowed_tools=PLANNING_TOOL_IDS,
        client=openai,
    )
    agent_tool_catalog = tool_registry.get_definitions(
        allowed_tools=[tool.tool_id for tool in registered_tools],
    )
    # Shared so Agent3D's llm.request/llm.response events and the
    # orchestrator's tool/step events land in the same per-job trace file.
    trace_writer = AgentTraceWriter()
    agent_3d = Agent3D(
        model_client=openai,
        tool_catalog=agent_tool_catalog,
        trace_writer=trace_writer,
    )
    orchestrator = EditWorkflowOrchestrator(
        repository,
        goal_creator,
        planning_agent,
        agent_3d,
        agent_tool_executor,
        worker_id=WORKER_ID,
        trace_writer=trace_writer,
    )
    return repository, orchestrator


def claim_next_job(repository: Any) -> dict[str, Any] | None:
    return repository.claim_next_edit_job(WORKER_ID, LEASE_SECONDS)


def run_once(
    repository: Any,
    orchestrator: Any,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Claim and process at most one durable edit job."""

    job = claim_next_job(repository)
    if job is None:
        return None
    claimed = dict(job)
    return claimed, orchestrator.run(claimed)


def _log_result(job: dict[str, Any], result: dict[str, Any]) -> None:
    stream = sys.stdout if result.get("status") == "completed" else sys.stderr
    print(
        f"cad-editor[{job['id']}] result={json.dumps(result, sort_keys=True)}",
        file=stream,
        flush=True,
    )


def main() -> None:
    configure_logging()
    repository, orchestrator = build_runtime()
    print(f"cad-editor worker_id={WORKER_ID} mode=agent-loop", flush=True)
    while True:
        try:
            processed = run_once(repository, orchestrator)
            if processed is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            job, result = processed
            _log_result(job, result)
        except Exception:
            # EditWorkflowOrchestrator records ordinary workflow failures. This
            # boundary is for lease loss and infrastructure failures where it is
            # unsafe for this process to publish another terminal write.
            print(
                f"cad-editor unhandled failure:\n{traceback.format_exc()}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
