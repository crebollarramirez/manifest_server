import os
import sys
import time
import traceback
from pathlib import Path

from supabase import create_client

from run_export_job import SupersededJob, run_export_job


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def claim_next_job():
    result = supabase.rpc(
        "claim_next_supported_generation_job",
        {"p_job_types": ["export_cad", "export_mesh"]},
    ).execute()

    if not result.data:
        return None

    return result.data[0]


def mark_completed(job_id: str):
    supabase.table("generation_jobs").update({
        "status": "completed",
    }).eq("id", job_id).execute()


def mark_failed(job_id: str, error: str):
    supabase.table("generation_jobs").update({
        "status": "failed",
        "error_message": error[:4000],
    }).eq("id", job_id).execute()


def mark_cancelled(job_id: str, message: str):
    supabase.table("generation_jobs").update({
        "status": "cancelled",
        "error_message": message,
        "result": {
            "schema_version": 1,
            "cancelled": True,
            "reason": "source_superseded",
        },
    }).eq("id", job_id).execute()


def print_job_error(job_id: str, error: str, *, cancelled: bool = False) -> None:
    outcome = "cancelled" if cancelled else "failed"
    print(
        f"export[{job_id}] {outcome}:\n{error}",
        file=sys.stderr,
        flush=True,
    )


def main():
    while True:
        job = claim_next_job()

        if job is None:
            time.sleep(2)
            continue

        try:
            run_export_job(supabase, job)
            mark_completed(job["id"])
        except SupersededJob as exc:
            error = str(exc)
            print_job_error(job["id"], error, cancelled=True)
            mark_cancelled(job["id"], error)
        except Exception:
            error = traceback.format_exc()
            print_job_error(job["id"], error)
            mark_failed(job["id"], error)


if __name__ == "__main__":
    main()
