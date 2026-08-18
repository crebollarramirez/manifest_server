from __future__ import annotations

import hashlib
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
        "surface_area_mm2": 600.0,
        "volume_mm3": 1000.0,
        "bounding_box": {"min": [-5, -5, -5], "max": [5, 5, 5], "size": [10, 10, 10]},
        "center_of_mass": [0, 0, 0],
        "solid_count": 1,
        "face_count": 6,
        "vertex_count": 8,
        "edge_count": 12,
        "planar_faces": [
            {
                "normal": [0.0, 0.0, 1.0],
                "angle_from_horizontal_deg": 0.0,
                "area_mm2": 100.0,
                "centroid": [0.0, 0.0, 5.0],
            },
            {
                "normal": [-0.9063, 0.0, 0.4226],
                "angle_from_horizontal_deg": 65.0,
                "area_mm2": 80.0,
                "centroid": [-2.0, 0.0, 0.0],
            },
        ],
        "non_planar_face_count": 0,
        "sharp_edge_count": 12,
    },
    "delta": {
        "volume_mm3": 500.0,
        "volume_percent": 100.0,
        "bbox_changed": True,
        "center_of_mass_distance_mm": 1.4,
        "solid_count": 0,
        "face_count": 0,
        "edge_count": 0,
        "sharp_edge_count": -4,
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

    async def test_the_face_census_survives_strict_schema_conversion(self):
        """The report's JSON lists must reach the agent as typed faces.

        ``StrictToolModel`` does no coercion, so a list where a tuple is
        declared is a validation error rather than a conversion. The census
        arrives from a JSON report as lists of lists, which is exactly the
        shape that has to be rebuilt by hand.
        """

        import hashlib

        content_hash = hashlib.sha256(MODEL_SOURCE.encode()).hexdigest()
        repository = FakeRepository(
            job_sequence=[
                {
                    "status": "completed",
                    "source_sha256": content_hash,
                    "result": COMPLETED_RESULT,
                }
            ]
        )
        tool = CheckGeometryTool(
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )

        result = await tool.run({}, make_context(repository))

        faces = result.data.geometry.planar_faces
        self.assertEqual(len(faces), 2)
        self.assertIsInstance(faces, tuple)
        self.assertIsInstance(faces[1].normal, tuple)
        self.assertAlmostEqual(faces[1].angle_from_horizontal_deg, 65.0)
        self.assertAlmostEqual(faces[1].area_mm2, 80.0)
        self.assertEqual(result.data.geometry.non_planar_face_count, 0)
        self.assertEqual(result.data.geometry.sharp_edge_count, 12)
        self.assertEqual(result.data.delta.sharp_edge_count, -4)

    async def test_a_report_without_a_census_yields_an_empty_one(self):
        # A snapshot measured before this vocabulary existed carries no census.
        # Absent must read as "nothing reported", not fail the whole result.
        import hashlib

        content_hash = hashlib.sha256(MODEL_SOURCE.encode()).hexdigest()
        legacy = {
            **COMPLETED_RESULT,
            "geometry": {
                key: value
                for key, value in COMPLETED_RESULT["geometry"].items()
                if key not in {"planar_faces", "non_planar_face_count", "sharp_edge_count"}
            },
        }
        repository = FakeRepository(
            job_sequence=[
                {"status": "completed", "source_sha256": content_hash, "result": legacy}
            ]
        )
        tool = CheckGeometryTool(
            timeout_seconds=10.0,
            poll_interval_seconds=0.01,
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )

        result = await tool.run({}, make_context(repository))

        self.assertTrue(result.ok)
        self.assertEqual(result.data.geometry.planar_faces, ())
        self.assertIsNone(result.data.geometry.sharp_edge_count)

    async def test_a_failed_check_job_is_reported_as_a_tool_failure(self):
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

        # A check that measured nothing is not evidence: it arrives as a
        # failure so the agent's "address the failure first" rule applies,
        # with the validator's reason intact rather than sanitized.
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "GEOMETRY_CHECK_FAILED")
        self.assertEqual(result.error.message, "CAD execution failed.")
        self.assertEqual(result.error.details["reason"], "job_failed")

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

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "GEOMETRY_CHECK_FAILED")
        self.assertEqual(result.error.details["reason"], "timeout")

    async def test_a_static_safety_rejection_reaches_the_agent_located(self):
        """The whole point of forwarding the AST report.

        A real run received "Source failed static safety checks and was not
        executed." six times and thrashed for twenty rounds, because the rule,
        the function, and the line were all discarded before it saw them.
        """

        import hashlib

        content_hash = hashlib.sha256(MODEL_SOURCE.encode()).hexdigest()
        repository = FakeRepository(
            job_sequence=[
                {
                    "status": "failed",
                    "source_sha256": content_hash,
                    "error_message": "Source failed static safety checks.",
                    "result": {
                        "error_message": "Source failed static safety checks.",
                        "geometry": {
                            "execution_ok": False,
                            "diagnostics": [
                                {
                                    "error_code": "INVALID_DEPENDENCY",
                                    "message": (
                                        "support_arm.depends_on must match direct "
                                        "build_model dataflow; declared="
                                        "['mounting_plate'], observed="
                                        "['mounting_holes']."
                                    ),
                                    "stage": "reference_validation",
                                    "line": 91,
                                    "function_name": "build_support_arm",
                                    "semantic_id": "support_arm",
                                }
                            ],
                        },
                    },
                }
            ]
        )
        tool = CheckGeometryTool()

        result = await tool.run({}, make_context(repository))

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "GEOMETRY_CHECK_FAILED")
        diagnostic = result.error.details["diagnostics"][0]
        self.assertEqual(diagnostic["error_code"], "INVALID_DEPENDENCY")
        self.assertEqual(diagnostic["line"], 91)
        self.assertEqual(diagnostic["function_name"], "build_support_arm")
        self.assertEqual(diagnostic["semantic_id"], "support_arm")
        # And the same detail is legible in the message the model reads.
        self.assertIn("INVALID_DEPENDENCY", result.error.message)
        self.assertIn("build_support_arm", result.error.message)
        self.assertIn("line 91", result.error.message)

    async def test_a_failure_with_nothing_locatable_still_reports_its_reason(self):
        repository = FakeRepository(
            job_sequence=[{"status": "queued", "source_sha256": "b" * 64, "result": None}]
        )
        clock = {"value": 0.0}
        tool = CheckGeometryTool(
            timeout_seconds=5.0,
            poll_interval_seconds=0.1,
            sleep=lambda _s: clock.__setitem__("value", clock["value"] + 1000.0),
            monotonic=lambda: clock["value"],
        )

        result = await tool.run({}, make_context(repository))

        self.assertFalse(result.ok)
        self.assertEqual(result.error.details["diagnostics"], [])
        self.assertIn("timeout", result.error.message.lower())

    async def test_a_source_hash_mismatch_is_reported_as_a_tool_failure(self):
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

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "GEOMETRY_CHECK_FAILED")
        self.assertEqual(result.error.details["reason"], "job_failed")

    async def test_missing_candidate_is_rejected_before_queuing_a_job(self):
        repository = FakeRepository()
        tool = CheckGeometryTool()

        result = await tool.run({}, make_context(repository, candidate_id=None))

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertEqual(repository.queue_calls, [])

    async def test_a_missing_candidate_source_is_reported_as_a_tool_failure(self):
        repository = FakeRepository(files={})
        tool = CheckGeometryTool()

        result = await tool.run({}, make_context(repository))

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "GEOMETRY_CHECK_FAILED")
        self.assertEqual(result.error.details["reason"], "source_not_found")
        self.assertEqual(repository.queue_calls, [])

    async def test_rejects_unexpected_arguments(self):
        repository = FakeRepository()
        tool = CheckGeometryTool()

        result = await tool.run({"unexpected": True}, make_context(repository))

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "TOOL_INPUT_INVALID")


class DerivedMetricSurfacingTests(unittest.IsolatedAsyncioTestCase):
    """The two metrics added when snapshots became B-rep-derived reach the agent."""

    async def test_surface_area_and_vertex_count_are_surfaced(self):
        content_hash = hashlib.sha256(MODEL_SOURCE.encode()).hexdigest()
        repository = FakeRepository(
            job_sequence=[
                {
                    "id": "job-1",
                    "status": "completed",
                    "source_sha256": content_hash,
                    "result": COMPLETED_RESULT,
                }
            ],
        )

        result = await CheckGeometryTool().run({}, make_context(repository))

        self.assertTrue(result.ok)
        self.assertEqual(result.data.geometry.surface_area_mm2, 600.0)
        self.assertEqual(result.data.geometry.vertex_count, 8)

    async def test_a_snapshot_missing_the_new_metrics_reports_absence(self):
        """A cached pre-B-rep row is unreachable, but absence must not crash."""

        content_hash = hashlib.sha256(MODEL_SOURCE.encode()).hexdigest()
        geometry = {k: v for k, v in COMPLETED_RESULT["geometry"].items()
                    if k not in ("surface_area_mm2", "vertex_count")}
        repository = FakeRepository(
            job_sequence=[
                {
                    "id": "job-1",
                    "status": "completed",
                    "source_sha256": content_hash,
                    "result": {**COMPLETED_RESULT, "geometry": geometry},
                }
            ],
        )

        result = await CheckGeometryTool().run({}, make_context(repository))

        self.assertTrue(result.ok)
        self.assertIsNone(result.data.geometry.surface_area_mm2)
        self.assertIsNone(result.data.geometry.vertex_count)


if __name__ == "__main__":
    unittest.main()
