from __future__ import annotations

import json
import os
import sys
import time
import traceback
import uuid

from supabase import create_client

from cad_editor.agent import CadEditAgent
from cad_editor.orchestrator import EditWorkflowOrchestrator
from cad_editor.repository import SupabaseEditRepository


POLL_INTERVAL_SECONDS = float(
    os.environ.get("CAD_EDITOR_JOB_POLL_INTERVAL_SECONDS", "2")
)
LEASE_SECONDS = int(os.environ.get("CAD_EDITOR_LEASE_SECONDS", "300"))
WORKER_ID = os.environ.get("CAD_EDITOR_WORKER_ID") or f"cad-editor-{uuid.uuid4()}"

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)
repository = SupabaseEditRepository(supabase)
agent = CadEditAgent()
orchestrator = EditWorkflowOrchestrator(
    repository,
    agent,
    worker_id=WORKER_ID,
)


def claim_next_job() -> dict | None:
    result = supabase.rpc(
        "claim_next_edit_job",
        {
            "p_worker_id": WORKER_ID,
            "p_lease_seconds": LEASE_SECONDS,
        },
    ).execute()
    return result.data[0] if result.data else None


def main() -> None:
    print(f"cad-editor worker_id={WORKER_ID}", flush=True)
    while True:
        job = claim_next_job()
        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        job_id = str(job["id"])
        try:
            result = orchestrator.run(dict(job))
            stream = sys.stdout if result["status"] == "completed" else sys.stderr
            print(
                f"cad-editor[{job_id}] result=" f"{json.dumps(result, sort_keys=True)}",
                file=stream,
                flush=True,
            )
        except Exception:
            error = traceback.format_exc()
            print(
                f"cad-editor[{job_id}] unhandled failure:\n{error}",
                file=sys.stderr,
                flush=True,
            )


if __name__ == "__main__":
    main()
