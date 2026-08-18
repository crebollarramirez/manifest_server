"""Candidate validation produces geometry, not just a verdict.

The acceptance point this file exists for: an artifact must come into being
because a candidate was *built*, not because the agent asked a question about
it. Validation runs on every mutation, so this is where a candidate's geometry
becomes real -- and it is why a later ``check_geometry`` for the same source
hash reuses this instead of executing the candidate a second time.
"""

from __future__ import annotations

import hashlib
import json
import unittest

from workers.cad_validator.validate_cad_job import validate_cad_job
from workers.cad_validator.geometry import GEOMETRY_CHECKER_VERSION

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PART_ID = "22222222-2222-4222-8222-222222222222"
EDIT_JOB_ID = "33333333-3333-4333-8333-333333333333"
CANDIDATE_PATH = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/model.py"
PARAMS_PATH = f"{PROJECT_ID}/parts/cad/{PART_ID}/params.json"

MODEL = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    width: float = 10.0

def build_model(params: ModelParams):
    return cq.Workplane("XY").box(params.width, params.width, 4.0)
"""
MODEL_HASH = hashlib.sha256(MODEL.encode()).hexdigest()


class _Bucket:
    def __init__(self, files):
        self.files = files
        self.uploads: list[str] = []

    def download(self, path):
        return self.files[path]

    def upload(self, *, path, file, file_options):
        self.uploads.append(path)
        self.files[path] = bytes(file)


class _Storage:
    def __init__(self, bucket):
        self.bucket = bucket

    def from_(self, _name):
        return self.bucket


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self._filters = {}
        self._payload = None

    def select(self, _c):
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def limit(self, _n):
        return self

    def insert(self, payload):
        self._payload = payload
        return self

    def execute(self):
        if self._payload is not None:
            key = (self._payload["source_sha256"], self._payload["geometry_checker_version"])
            row = dict(self._payload)
            row.setdefault("id", f"row-{len(self.rows) + 1}")
            self.rows[key] = row
            return _Response([dict(row)])
        return _Response(
            [r for r in self.rows.values() if all(r.get(k) == v for k, v in self._filters.items())]
        )


class _Response:
    def __init__(self, data):
        self.data = data


class _Supabase:
    def __init__(self, files):
        self.storage = _Storage(_Bucket(files))
        self.snapshots: dict = {}
        self.artifacts: dict = {}

    def table(self, name):
        return _Table(self.snapshots if name == "geometry_snapshots" else self.artifacts)


def _job(**overrides):
    base = {
        "id": "job-1",
        "project_id": PROJECT_ID,
        "part_id": PART_ID,
        "edit_job_id": EDIT_JOB_ID,
        "source_kind": "candidate",
        "source_storage_path": CANDIDATE_PATH,
        "source_sha256": MODEL_HASH,
    }
    base.update(overrides)
    return base


class ValidationProducesGeometryTests(unittest.TestCase):
    def _supabase(self, **files):
        merged = {CANDIDATE_PATH: MODEL.encode(), PARAMS_PATH: b"{}"}
        merged.update(files)
        return _Supabase(merged)

    def test_a_passing_validation_records_a_candidate_bound_artifact(self):
        supabase = self._supabase()

        result = validate_cad_job(supabase, _job())

        self.assertEqual(result["status"], "completed")
        artifact = supabase.artifacts[(MODEL_HASH, GEOMETRY_CHECKER_VERSION)]
        self.assertEqual(artifact["edit_job_id"], EDIT_JOB_ID)
        self.assertEqual(artifact["source_sha256"], MODEL_HASH)
        self.assertEqual(artifact["artifact_format"], "brep")
        self.assertEqual(len(artifact["artifact_digest"]), 64)

    def test_a_passing_validation_records_the_derived_snapshot(self):
        supabase = self._supabase()

        validate_cad_job(supabase, _job())

        snapshot = supabase.snapshots[(MODEL_HASH, GEOMETRY_CHECKER_VERSION)]
        self.assertAlmostEqual(snapshot["volume_mm3"], 400.0, places=6)
        self.assertEqual(snapshot["solid_count"], 1)
        self.assertEqual(snapshot["vertex_count"], 8)
        self.assertEqual(
            snapshot["geometry_artifact_id"],
            supabase.artifacts[(MODEL_HASH, GEOMETRY_CHECKER_VERSION)]["id"],
        )

    def test_the_validation_report_shape_is_unchanged(self):
        """Internal representation moved; what the orchestrator reads did not.

        ``build_artifacts`` is projected from the same snapshot now, but its key
        set is exactly what ``orchestrator._geometry_summary`` already reads,
        and the snapshot itself is deliberately not left in the report.
        """

        supabase = self._supabase()

        report = validate_cad_job(supabase, _job())["report"]

        artifacts = report["build_artifacts"]
        self.assertEqual(
            set(artifacts),
            {
                "solid_count",
                "result_type",
                "volume_mm3",
                "bounding_box",
                "center_of_mass",
                "face_count",
                "edge_count",
                "planar_faces",
                "non_planar_face_count",
                "sharp_edge_count",
            },
        )
        self.assertEqual(artifacts["result_type"], "cadquery.Workplane")
        self.assertNotIn("geometry", report)
        self.assertNotIn("geometry_artifact", report)

    def test_raw_brep_topology_never_reaches_the_validation_report(self):
        supabase = self._supabase()

        report = validate_cad_job(supabase, _job())["report"]

        serialized = json.dumps(report)
        self.assertNotIn("DBRep", serialized)
        self.assertNotIn("CASCADE Topology", serialized)

    def test_geometry_persistence_failure_never_fails_a_valid_candidate(self):
        """A source that validated must not be reported invalid because a
        bucket was unreachable. Geometry persistence is a side effect."""

        supabase = self._supabase()

        def refuse(*_args, **_kwargs):
            raise RuntimeError("bucket unavailable")

        supabase.storage.bucket.upload = refuse

        result = validate_cad_job(supabase, _job())

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["report"]["valid"])

    def test_snapshot_insert_failure_never_fails_a_valid_candidate(self):
        supabase = self._supabase()

        def explode(_name):
            raise RuntimeError("PGRST204")

        supabase.table = explode

        result = validate_cad_job(supabase, _job())

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["report"]["valid"])

    def test_a_build_that_never_produces_geometry_records_no_artifact(self):
        broken = MODEL.replace(
            'return cq.Workplane("XY").box(params.width, params.width, 4.0)',
            "raise ValueError('boom')",
        )
        broken_hash = hashlib.sha256(broken.encode()).hexdigest()
        supabase = self._supabase(**{CANDIDATE_PATH: broken.encode()})

        result = validate_cad_job(supabase, _job(source_sha256=broken_hash))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(supabase.artifacts, {})
        self.assertEqual(supabase.snapshots, {})


if __name__ == "__main__":
    unittest.main()
