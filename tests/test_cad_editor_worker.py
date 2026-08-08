from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from workers.agent_3d import edit_worker


class FakeRepository:
    def __init__(self, job=None):
        self.job = job
        self.claims = []

    def claim_next_edit_job(self, worker_id: str, lease_seconds: int):
        self.claims.append((worker_id, lease_seconds))
        job, self.job = self.job, None
        return job


class FakeOrchestrator:
    def __init__(self, result=None):
        self.result = result or {"status": "completed"}
        self.jobs = []

    def run(self, job: dict):
        self.jobs.append(job)
        return self.result


class CadEditorWorkerTests(unittest.TestCase):
    def test_idle_poll_claims_edit_jobs_without_calling_orchestrator(self):
        repository = FakeRepository()
        orchestrator = FakeOrchestrator()

        with (
            patch.object(edit_worker, "WORKER_ID", "editor-a"),
            patch.object(edit_worker, "LEASE_SECONDS", 90),
        ):
            result = edit_worker.run_once(repository, orchestrator)

        self.assertIsNone(result)
        self.assertEqual(repository.claims, [("editor-a", 90)])
        self.assertEqual(orchestrator.jobs, [])

    def test_claimed_edit_job_is_handed_directly_to_python_orchestrator(self):
        claimed = {
            "id": "edit-a",
            "status": "running",
            "state": "received",
            "worker_id": "editor-a",
        }
        repository = FakeRepository(claimed)
        orchestrator = FakeOrchestrator(
            {"status": "completed", "message": "done"}
        )

        with (
            patch.object(edit_worker, "WORKER_ID", "editor-a"),
            patch.object(edit_worker, "LEASE_SECONDS", 120),
        ):
            processed = edit_worker.run_once(repository, orchestrator)

        self.assertEqual(
            processed,
            (claimed, {"status": "completed", "message": "done"}),
        )
        self.assertEqual(repository.claims, [("editor-a", 120)])
        self.assertEqual(orchestrator.jobs, [claimed])

    def test_terminal_results_log_to_the_matching_stream(self):
        job = {"id": "edit-a"}
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            edit_worker._log_result(job, {"status": "completed", "message": "ok"})
            edit_worker._log_result(job, {"status": "failed", "message": "bad"})

        self.assertIn('"message": "ok"', stdout.getvalue())
        self.assertNotIn('"message": "bad"', stdout.getvalue())
        self.assertIn('"message": "bad"', stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
