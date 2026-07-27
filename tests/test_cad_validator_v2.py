from __future__ import annotations

import hashlib
import unittest
from unittest.mock import patch

from tests.test_cad_editor_core import MODEL_SOURCE
from workers.cad_validator.cad_ast_validator import validate_cad_source
from workers.cad_validator.validate_cad_job import (
    validate_cad_job,
    validation_source_path,
)


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PART_ID = "22222222-2222-4222-8222-222222222222"
EDIT_JOB_ID = "33333333-3333-4333-8333-333333333333"
CANDIDATE_PATH = (
    f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/attempt-1/model.py"
)


class _FakeBucket:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.downloads: list[str] = []

    def download(self, path: str) -> bytes:
        self.downloads.append(path)
        return self.files[path]


class _FakeStorage:
    def __init__(self, bucket: _FakeBucket):
        self.bucket = bucket

    def from_(self, _name: str) -> _FakeBucket:
        return self.bucket


class _FakeSupabase:
    def __init__(self, files: dict[str, bytes]):
        self.bucket = _FakeBucket(files)
        self.storage = _FakeStorage(self.bucket)


class StagedDiagnosticTests(unittest.TestCase):
    def test_stops_at_syntax_with_normalized_diagnostic(self):
        report = validate_cad_source(
            MODEL_SOURCE.replace("def build_model", "def build_model("),
            file_path="candidate/model.py",
        )

        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stage"], "syntax")
        self.assertEqual(
            report["diagnostics"][0]["error_code"],
            "PYTHON_SYNTAX_ERROR",
        )
        self.assertEqual(
            report["diagnostics"][0]["file_path"],
            "candidate/model.py",
        )

    def test_unknown_params_access_fails_reference_stage(self):
        source = MODEL_SOURCE.replace(
            "return cq.Workplane",
            "missing = params.unknown_field\n" "    return cq.Workplane",
            1,
        )

        report = validate_cad_source(source, file_path="candidate/model.py")

        self.assertEqual(report["stage"], "reference_validation")
        diagnostic = report["diagnostics"][0]
        self.assertEqual(diagnostic["error_code"], "UNKNOWN_MODEL_PARAMETER")
        self.assertEqual(diagnostic["function_name"], "cut_mounting_holes")
        self.assertEqual(diagnostic["semantic_id"], "mount_holes")

    def test_security_stage_runs_before_runtime(self):
        source = MODEL_SOURCE.replace(
            'return cq.Workplane("XY").circle(params.hole_diameter / 2).extrude(2)',
            'open("/tmp/model-output", "w")\n'
            '    return cq.Workplane("XY").box(1, 1, 1)',
        )

        report = validate_cad_source(source, file_path="candidate/model.py")

        self.assertEqual(report["stage"], "security")
        self.assertFalse(report["safe_to_execute"])
        self.assertEqual(report["diagnostics"][0]["error_code"], "FORBIDDEN_CALL")


class CandidateValidationTests(unittest.TestCase):
    def test_downloads_and_hash_binds_the_candidate_instead_of_canonical_source(self):
        params_path = f"{PROJECT_ID}/parts/cad/{PART_ID}/params.json"
        source_bytes = MODEL_SOURCE.encode("utf-8")
        supabase = _FakeSupabase(
            {
                CANDIDATE_PATH: source_bytes,
                params_path: b"{}",
            }
        )
        job = {
            "id": "44444444-4444-4444-8444-444444444444",
            "project_id": PROJECT_ID,
            "part_id": PART_ID,
            "source_kind": "candidate",
            "source_storage_path": CANDIDATE_PATH,
            "edit_job_id": EDIT_JOB_ID,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        }
        runtime = {
            "passed": True,
            "skipped": False,
            "exit_code": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
            "errors": [],
            "validation_result": {
                "status": "passed",
                "stage": "completed",
                "repairable_hint": False,
                "diagnostics": [],
                "build_artifacts": {
                    "solid_count": 1,
                    "result_type": "cadquery.Workplane",
                },
            },
        }

        with patch(
            "workers.cad_validator.validate_cad_job.run_model",
            return_value=runtime,
        ):
            outcome = validate_cad_job(supabase, job)

        self.assertEqual(outcome["status"], "completed")
        self.assertEqual(outcome["report"]["status"], "passed")
        self.assertEqual(outcome["report"]["source_sha256"], job["source_sha256"])
        self.assertEqual(
            supabase.bucket.downloads,
            [CANDIDATE_PATH, params_path],
        )

    def test_rejects_candidate_paths_owned_by_another_edit(self):
        job = {
            "project_id": PROJECT_ID,
            "part_id": PART_ID,
            "source_kind": "candidate",
            "source_storage_path": (
                f"{PROJECT_ID}/candidates/cad/{PART_ID}/another-edit/"
                "attempt-1/model.py"
            ),
            "edit_job_id": EDIT_JOB_ID,
        }

        with self.assertRaisesRegex(ValueError, "outside its edit job"):
            validation_source_path(job)


if __name__ == "__main__":
    unittest.main()
