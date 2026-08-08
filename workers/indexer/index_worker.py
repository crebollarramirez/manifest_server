from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path


# Script execution adds only workers/indexer to sys.path. Add workers/ so
# indexer itself is importable, and the workspace root so shared
# workers.commons imports work outside Docker too.
WORKERS_ROOT = str(Path(__file__).resolve().parents[1])
WORKSPACE_ROOT = str(Path(__file__).resolve().parents[2])
for _root in (WORKERS_ROOT, WORKSPACE_ROOT):
    if _root not in sys.path:
        sys.path.insert(0, _root)

from supabase import create_client

from indexer.repository import SupabaseProjectRepository
from indexer.service import process_index_job


POLL_INTERVAL_SECONDS = float(os.environ.get("INDEX_POLL_INTERVAL_SECONDS", "2"))
supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)
repository = SupabaseProjectRepository(supabase)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def claim_next_job() -> dict | None:
    result = supabase.rpc("claim_next_index_job").execute()
    return result.data[0] if result.data else None


def complete_job(job_id: str, result: dict) -> None:
    (
        supabase.table("index_jobs")
        .update({
            "status": "completed",
            "result": result,
            "error_message": None,
            "completed_at": _now(),
        })
        .eq("id", job_id)
        .execute()
    )


def fail_job(job_id: str, error: str) -> None:
    (
        supabase.table("index_jobs")
        .update({
            "status": "failed",
            "error_message": error[-4000:],
            "completed_at": _now(),
        })
        .eq("id", job_id)
        .execute()
    )


def main() -> None:
    while True:
        job = claim_next_job()
        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        job_id = str(job["id"])
        try:
            result = process_index_job(repository, job)
            complete_job(job_id, result)
            print(
                f"indexer[{job_id}] result={json.dumps(result, sort_keys=True)}",
                flush=True,
            )
        except Exception:
            error = traceback.format_exc()
            print(f"indexer[{job_id}] failed:\n{error}", file=sys.stderr, flush=True)
            fail_job(job_id, error)


if __name__ == "__main__":
    main()
