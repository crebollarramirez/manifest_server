from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid

from supabase import create_client

from cad_editor.contracts import WorkflowFailure
from cad_editor.repository import SupabaseEditRepository
from cad_editor.tool_executor import CadToolExecutor


POLL_INTERVAL_SECONDS = float(
    os.environ.get("CAD_TOOL_JOB_POLL_INTERVAL_SECONDS", "2")
)
LEASE_SECONDS = int(os.environ.get("CAD_TOOL_LEASE_SECONDS", "300"))
WORKER_ID = os.environ.get("CAD_TOOL_WORKER_ID") or f"cad-tool-{uuid.uuid4()}"

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)
repository = SupabaseEditRepository(supabase)
executor = CadToolExecutor(repository)


def claim_next_job() -> dict | None:
    response = supabase.rpc(
        "claim_next_cad_tool_job",
        {
            "p_worker_id": WORKER_ID,
            "p_lease_seconds": LEASE_SECONDS,
        },
    ).execute()
    return dict(response.data[0]) if response.data else None


def complete(job_id: str, result: dict) -> None:
    supabase.rpc(
        "complete_cad_tool_job",
        {
            "p_tool_job_id": job_id,
            "p_worker_id": WORKER_ID,
            "p_result": result,
        },
    ).execute()


def heartbeat(job_id: str) -> None:
    response = supabase.rpc(
        "heartbeat_cad_tool_job",
        {
            "p_tool_job_id": job_id,
            "p_worker_id": WORKER_ID,
            "p_lease_seconds": LEASE_SECONDS,
        },
    ).execute()
    if response.data is not True:
        raise WorkflowFailure(
            "CAD_TOOL_LEASE_LOST",
            "The CAD tool worker no longer owns this job.",
        )


def fail(job_id: str, code: str, message: str) -> None:
    supabase.rpc(
        "fail_cad_tool_job",
        {
            "p_tool_job_id": job_id,
            "p_worker_id": WORKER_ID,
            "p_error_code": code,
            "p_error_message": message[:1000],
        },
    ).execute()


def execute_with_heartbeat(job: dict) -> dict:
    job_id = str(job["id"])
    stop = threading.Event()
    lease_errors: list[Exception] = []

    def maintain_lease() -> None:
        interval = max(1.0, LEASE_SECONDS / 3)
        while not stop.wait(interval):
            try:
                heartbeat(job_id)
            except Exception as error:
                lease_errors.append(error)
                stop.set()

    lease_thread = threading.Thread(
        target=maintain_lease,
        name=f"cad-tool-heartbeat-{job_id}",
        daemon=True,
    )
    lease_thread.start()
    try:
        result = executor.execute(job)
        if lease_errors:
            raise lease_errors[0]
        heartbeat(job_id)
        return result
    finally:
        stop.set()
        lease_thread.join(timeout=1)


def record_failure(job_id: str, code: str, message: str) -> None:
    try:
        fail(job_id, code, message)
    except Exception as error:
        print(
            f"cad-tool[{job_id}] could not record failure: {error}",
            file=sys.stderr,
            flush=True,
        )


def main() -> None:
    print(f"cad-tool worker_id={WORKER_ID}", flush=True)
    while True:
        job = claim_next_job()
        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        job_id = str(job["id"])
        try:
            result = execute_with_heartbeat(job)
            complete(job_id, result)
            print(
                f"cad-tool[{job_id}] result={json.dumps(result, sort_keys=True)}",
                flush=True,
            )
        except WorkflowFailure as error:
            record_failure(job_id, error.code, str(error))
            print(
                f"cad-tool[{job_id}] failed code={error.code}: {error}",
                file=sys.stderr,
                flush=True,
            )
        except Exception:
            detail = traceback.format_exc()
            record_failure(
                job_id,
                "CAD_TOOL_INTERNAL_ERROR",
                "CAD tool execution failed unexpectedly.",
            )
            print(
                f"cad-tool[{job_id}] unhandled failure:\n{detail}",
                file=sys.stderr,
                flush=True,
            )


if __name__ == "__main__":
    main()
