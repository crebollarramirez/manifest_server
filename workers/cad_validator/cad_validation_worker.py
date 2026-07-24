import json
import os
import sys
import time
import traceback

from supabase import create_client

from validate_cad_job import validate_cad_job


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
POLL_INTERVAL_SECONDS = 2

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def claim_next_job():
    result = supabase.rpc(
        "claim_next_supported_generation_job",
        {"p_job_types": ["validate_cad"]},
    ).execute()
    return result.data[0] if result.data else None


def update_job(job_id: str, status: str, report: dict, error_message: str | None):
    supabase.table("generation_jobs").update({
        "status": status,
        "result": report,
        "error_message": error_message,
    }).eq("id", job_id).execute()


def complete_and_queue_export(job: dict, report: dict):
    return supabase.rpc(
        "complete_cad_validation_and_queue_export",
        {
            "p_validation_job_id": job["id"],
            "p_source_sha256": job["source_sha256"],
            "p_result": report,
        },
    ).execute()


def print_report(job_id: str, report: dict) -> None:
    for name, check in report.get("checks", {}).items():
        for error in check.get("errors", []):
            location = ""
            if error.get("line"):
                location = f" line {error['line']}:{error.get('column', 0)}"
            print(
                f"validation[{job_id}] {name}{location}: {error.get('message')}",
                file=sys.stderr,
                flush=True,
            )

    runtime = report.get("runtime", {})
    for error in runtime.get("errors", []):
        print(
            f"validation[{job_id}] runtime: {error.get('message')}",
            file=sys.stderr,
            flush=True,
        )
    stdout = runtime.get("stdout")
    stderr = runtime.get("stderr")
    if stdout:
        print(f"validation[{job_id}] stdout:\n{stdout}", flush=True)
    if stderr:
        print(
            f"validation[{job_id}] stderr:\n{stderr}",
            file=sys.stderr,
            flush=True,
        )
    print(
        f"validation[{job_id}] report={json.dumps(report, sort_keys=True)}",
        file=sys.stderr,
        flush=True,
    )


def process_job(job: dict) -> None:
    outcome = validate_cad_job(supabase, job)
    report = outcome["report"]
    print_report(job["id"], report)
    if outcome["status"] == "completed":
        complete_and_queue_export(job, report)
    else:
        update_job(
            job["id"],
            outcome["status"],
            report,
            outcome["error_message"],
        )


def mark_unexpected_failure(job_id: str, error: str) -> None:
    report = {
        "schema_version": 1,
        "valid": False,
        "checks": {},
        "runtime": {
            "passed": False,
            "skipped": True,
            "stdout": "",
            "stderr": error[-16_000:],
            "errors": [
                {
                    "code": "worker_error",
                    "message": "CAD validation worker failed unexpectedly.",
                }
            ],
        },
    }
    update_job(job_id, "failed", report, error[-4000:])


def main():
    while True:
        job = claim_next_job()
        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        try:
            process_job(job)
        except Exception:
            error = traceback.format_exc()
            print(error, file=sys.stderr, flush=True)
            mark_unexpected_failure(job["id"], error)


if __name__ == "__main__":
    main()
