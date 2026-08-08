from __future__ import annotations

import unittest

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.tools import (
    CheckGeometryTool,
    ToolExecutionContext,
    ToolServices,
)


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PART_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
CANDIDATE_ID = "candidate-1"
CANDIDATE_PATH = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{CANDIDATE_ID}/model.py"
MODEL_SOURCE = "from cadquery_runtime import cad_part, cq, dataclass\n"

COMPLETED_RESULT = {
    "previous_source_sha256": "a" * 64,
    "geometry": {
        "execution_ok": True,
        "geometry_valid": True,
        "volume_mm3": 1000.0,
        "bounding_box": {"min": [-5, -5, -5], "max": [5, 5, 5], "size": [10, 10, 10]},
        "center_of_mass": [0, 0, 0],
        "solid_count": 1,
        "face_count": 6,
        "edge_count": 12,
    },
    "delta": {
        "volume_mm3": 500.0,
        "volume_percent": 100.0,
        "bbox_changed": True,
        "center_of_mass_distance_mm": 1.4,
        "solid_count": 0,
        "face_count": 0,
        "edge_count": 0,
        "validity_changed": False,
    },
    "warnings": [],
}


class FakeRepository:
    def __init__(self, files=None, job_sequence=None):
        default_files = {CANDIDATE_PATH: MODEL_SOURCE}
        self.files = dict(default_files if files is None else files)
        self.queue_calls: list[tuple[str, str]] = []
        self.job_sequence = list(job_sequence or [])
        self.poll_count = 0
        self.job_id_returned = "generated-job-1"

    def read_text(self, path: str) -> str:
        try:
            return self.files[path]
        except KeyError as exc:
            raise WorkflowFailure("SOURCE_MISSING", f"{path} missing") from exc

    def queue_geometry_check(self, edit_job_id: str, candidate_sha256: str) -> str:
        self.queue_calls.append((edit_job_id, candidate_sha256))
        return self.job_id_returned

    def generation_job(self, job_id: str) -> dict:
        index = min(self.poll_count, len(self.job_sequence) - 1)
        self.poll_count += 1
        return self.job_sequence[index]


def make_context(repository: FakeRepository, *, candidate_id=CANDIDATE_ID):
    return ToolExecutionContext(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        part_id=PART_ID,
        candidate_id=candidate_id,
        services=ToolServices(repository=repository),
    )


class CheckGeometryToolUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_the_expected_geometry_check_job(self):
        import hashlib

        repository = FakeRepository(
            job_sequence=[
                {
                    "status": "completed",
                    "source_sha256": hashlib.sha256(MODEL_SOURCE.encode()).hexdigest(),
                    "result": COMPLETED_RESULT,
                }
            ]
        )
        tool = CheckGeometryTool()

        await tool.run({}, make_context(repository))

        self.assertEqual(len(repository.queue_calls), 1)
        edit_job_id, candidate_sha256 = repository.queue_calls[0]
        self.assertEqual(edit_job_id, CANDIDATE_ID)
        self.assertEqual(
            candidate_sha256, hashlib.sha256(MODEL_SOURCE.encode()).hexdigest()
        )

    async def test_waits_across_multiple_polls_and_returns_the_completed_result(self):
        import hashlib

        content_hash = hashlib.sha256(MODEL_SOURCE.encode()).hexdigest()
        repository = FakeRepository(
            job_sequence=[
                {"status": "queued", "source_sha256": content_hash, "result": None},
                {"status": "running", "source_sha256": content_hash, "result": None},
                {
                    "status": "completed",
                    "source_sha256": content_hash,
                    "result": COMPLETED_RESULT,
                },
            ]
        )
        sleeps: list[float] = []
        tool = CheckGeometryTool(
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            sleep=sleeps.append,
            monotonic=lambda: 0.0,
        )

        result = await tool.run({}, make_context(repository))

        self.assertTrue(result.ok)
        self.assertEqual(result.data.status, "completed")
        self.assertEqual(result.data.source_hash, content_hash)
        self.assertEqual(result.data.previous_source_hash, "a" * 64)
        self.assertAlmostEqual(result.data.geometry.volume_mm3, 1000.0)
        self.assertAlmostEqual(result.data.delta.volume_mm3, 500.0)
        self.assertEqual(len(sleeps), 2)  # polled twice before completion

    async def test_failed_job_becomes_a_structured_job_failed_status(self):
        import hashlib

        content_hash = hashlib.sha256(MODEL_SOURCE.encode()).hexdigest()
        repository = FakeRepository(
            job_sequence=[
                {
                    "status": "failed",
                    "source_sha256": content_hash,
                    "error_message": "CAD execution failed.",
                    "result": {"error_message": "CAD execution failed."},
                }
            ]
        )
        tool = CheckGeometryTool()

        result = await tool.run({}, make_context(repository))

        self.assertTrue(result.ok)
        self.assertEqual(result.data.status, "job_failed")
        self.assertIsNotNone(result.data.message)

    async def test_timeout_is_bounded_and_deterministic(self):
        content_hash = "b" * 64
        repository = FakeRepository(
            job_sequence=[{"status": "queued", "source_sha256": content_hash, "result": None}]
        )
        clock = {"value": 0.0}

        def monotonic():
            return clock["value"]

        def sleep(seconds):
            clock["value"] += 1000.0  # jump well past the timeout on first poll

        tool = CheckGeometryTool(
            timeout_seconds=5.0,
            poll_interval_seconds=0.1,
            sleep=sleep,
            monotonic=monotonic,
        )

        result = await tool.run({}, make_context(repository))

        self.assertTrue(result.ok)
        self.assertEqual(result.data.status, "timeout")

    async def test_source_hash_mismatch_is_rejected_as_job_failed(self):
        repository = FakeRepository(
            job_sequence=[
                {
                    "status": "completed",
                    "source_sha256": "not-the-requested-hash",
                    "result": COMPLETED_RESULT,
                }
            ]
        )
        tool = CheckGeometryTool()

        result = await tool.run({}, make_context(repository))

        self.assertTrue(result.ok)
        self.assertEqual(result.data.status, "job_failed")

    async def test_missing_candidate_is_rejected_before_queuing_a_job(self):
        repository = FakeRepository()
        tool = CheckGeometryTool()

        result = await tool.run({}, make_context(repository, candidate_id=None))

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertEqual(repository.queue_calls, [])

    async def test_missing_candidate_source_is_reported_as_source_not_found(self):
        repository = FakeRepository(files={})
        tool = CheckGeometryTool()

        result = await tool.run({}, make_context(repository))

        self.assertTrue(result.ok)
        self.assertEqual(result.data.status, "source_not_found")
        self.assertEqual(repository.queue_calls, [])

    async def test_rejects_unexpected_arguments(self):
        repository = FakeRepository()
        tool = CheckGeometryTool()

        result = await tool.run({"unexpected": True}, make_context(repository))

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "TOOL_INPUT_INVALID")


if __name__ == "__main__":
    unittest.main()
