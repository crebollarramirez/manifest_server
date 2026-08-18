from __future__ import annotations

import hashlib
import json
import unittest

from workers.cad_validator.geometry_check_job import _GEOMETRY_FIELDS, geometry_check_job
from workers.cad_validator.geometry_inspection import GEOMETRY_CHECKER_VERSION


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PART_ID = "22222222-2222-4222-8222-222222222222"
EDIT_JOB_ID = "33333333-3333-4333-8333-333333333333"
ORIGINAL_PATH = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/original/model.py"
CANDIDATE_PATH = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/model.py"
PARAMS_PATH = f"{PROJECT_ID}/parts/cad/{PART_ID}/params.json"

MODEL_A = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    width: float = 10.0

def build_model(params: ModelParams):
    return cq.Workplane("XY").box(params.width, params.width, params.width)
"""

MODEL_B = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    width: float = 10.0

def build_model(params: ModelParams):
    return cq.Workplane("XY").box(params.width * 2, params.width, params.width)
"""

HASH_A = hashlib.sha256(MODEL_A.encode()).hexdigest()
HASH_B = hashlib.sha256(MODEL_B.encode()).hexdigest()

# Imported rather than copied. A local duplicate silently went stale once
# already -- it was still missing `diagnostics` long after the worker started
# persisting it -- so a snapshot fixture built here would have kept passing
# while the real insert was crashing the validator.
GEOMETRY_FIELDS = _GEOMETRY_FIELDS


class _FakeBucket:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.downloads: list[str] = []
        self.uploads: list[str] = []

    def download(self, path: str) -> bytes:
        self.downloads.append(path)
        return self.files[path]

    def upload(self, *, path: str, file: bytes, file_options: dict) -> None:
        self.uploads.append(path)
        self.files[path] = bytes(file)


class _FakeStorage:
    def __init__(self, bucket: _FakeBucket):
        self.bucket = bucket

    def from_(self, _name: str) -> _FakeBucket:
        return self.bucket


class _FakeTable:
    def __init__(self, rows: dict[tuple, dict]):
        self.rows = rows
        self._filters: dict[str, object] = {}
        self._insert_payload: dict | None = None

    def select(self, _columns):
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def limit(self, _n):
        return self

    def insert(self, payload):
        self._insert_payload = payload
        return self

    def execute(self):
        if self._insert_payload is not None:
            key = (
                self._insert_payload["source_sha256"],
                self._insert_payload["geometry_checker_version"],
            )
            if key in self.rows:
                raise _DuplicateKeyError()
            row = dict(self._insert_payload)
            row.setdefault("id", f"row-{len(self.rows) + 1}")
            self.rows[key] = row
            return _Response([dict(row)])
        matches = [
            row
            for row in self.rows.values()
            if all(row.get(k) == v for k, v in self._filters.items())
        ]
        return _Response(matches)


class _DuplicateKeyError(Exception):
    code = "23505"


class _Response:
    def __init__(self, data):
        self.data = data


class _FakeSupabase:
    def __init__(self, files: dict[str, bytes]):
        self.storage = _FakeStorage(_FakeBucket(files))
        self.snapshot_rows: dict[tuple, dict] = {}
        self.artifact_rows: dict[tuple, dict] = {}

    def table(self, name: str) -> _FakeTable:
        # Both tables are keyed on (source_sha256, geometry_checker_version),
        # which is what pairs a snapshot with the artifact it observed.
        assert name in ("geometry_snapshots", "geometry_artifacts"), name
        rows = (
            self.snapshot_rows
            if name == "geometry_snapshots"
            else self.artifact_rows
        )
        return _FakeTable(rows)


def _job(**overrides) -> dict:
    base = {
        "id": "job-1",
        "project_id": PROJECT_ID,
        "part_id": PART_ID,
        "edit_job_id": EDIT_JOB_ID,
        "source_kind": "candidate",
        "source_storage_path": CANDIDATE_PATH,
        "source_sha256": HASH_B,
        "previous_source_storage_path": ORIGINAL_PATH,
        "previous_source_sha256": HASH_A,
    }
    base.update(overrides)
    return base


class GeometryCheckJobTests(unittest.TestCase):
    def _supabase(self, **files) -> _FakeSupabase:
        merged = {
            CANDIDATE_PATH: MODEL_B.encode(),
            ORIGINAL_PATH: MODEL_A.encode(),
            PARAMS_PATH: b"{}",
        }
        merged.update(files)
        return _FakeSupabase(merged)

    def test_computes_current_and_previous_and_persists_both_snapshots(self):
        supabase = self._supabase()

        outcome = geometry_check_job(supabase, _job())

        self.assertEqual(outcome["status"], "completed")
        report = outcome["report"]
        self.assertEqual(report["source_sha256"], HASH_B)
        self.assertEqual(report["previous_source_sha256"], HASH_A)
        self.assertAlmostEqual(report["geometry"]["volume_mm3"], 2000.0, places=6)
        self.assertAlmostEqual(report["delta"]["volume_mm3"], 1000.0, places=6)
        self.assertEqual(len(supabase.snapshot_rows), 2)

    def test_snapshot_is_bound_to_its_exact_source_hash(self):
        supabase = self._supabase()
        geometry_check_job(supabase, _job())

        row_a = supabase.snapshot_rows[(HASH_A, GEOMETRY_CHECKER_VERSION)]
        row_b = supabase.snapshot_rows[(HASH_B, GEOMETRY_CHECKER_VERSION)]

        self.assertEqual(row_a["source_sha256"], HASH_A)
        self.assertEqual(row_b["source_sha256"], HASH_B)
        self.assertNotEqual(row_a["volume_mm3"], row_b["volume_mm3"])

    def test_snapshot_for_hash_a_is_never_returned_for_hash_b(self):
        supabase = self._supabase()
        supabase.snapshot_rows[(HASH_A, GEOMETRY_CHECKER_VERSION)] = {
            "source_sha256": HASH_A,
            "geometry_checker_version": GEOMETRY_CHECKER_VERSION,
            **{field: None for field in GEOMETRY_FIELDS},
            "execution_ok": True,
            "geometry_valid": True,
            "volume_mm3": 999999.0,  # sentinel: must never leak into hash B's result
        }

        outcome = geometry_check_job(supabase, _job())

        self.assertNotEqual(outcome["report"]["geometry"]["volume_mm3"], 999999.0)

    def test_existing_compatible_snapshot_is_reused_without_recomputing(self):
        supabase = self._supabase()
        supabase.snapshot_rows[(HASH_B, GEOMETRY_CHECKER_VERSION)] = {
            "source_sha256": HASH_B,
            "geometry_checker_version": GEOMETRY_CHECKER_VERSION,
            "execution_ok": True,
            "geometry_valid": True,
            "error_message": None,
            "volume_mm3": 12345.0,
            "bounding_box": {"min": [0, 0, 0], "max": [1, 1, 1], "size": [1, 1, 1]},
            "center_of_mass": [0.5, 0.5, 0.5],
            "solid_count": 1,
            "face_count": 6,
            "edge_count": 12,
        }
        supabase.snapshot_rows[(HASH_A, GEOMETRY_CHECKER_VERSION)] = {
            "source_sha256": HASH_A,
            "geometry_checker_version": GEOMETRY_CHECKER_VERSION,
            "execution_ok": True,
            "geometry_valid": True,
            "error_message": None,
            "volume_mm3": 1000.0,
            "bounding_box": {"min": [-5, -5, -5], "max": [5, 5, 5], "size": [10, 10, 10]},
            "center_of_mass": [0, 0, 0],
            "solid_count": 1,
            "face_count": 6,
            "edge_count": 12,
        }

        outcome = geometry_check_job(supabase, _job())

        self.assertEqual(outcome["report"]["geometry"]["volume_mm3"], 12345.0)
        # Only the current candidate's model.py was downloaded, to verify it
        # has not gone stale since the job was queued -- both current and
        # previous snapshots were cache hits, so params.json was never
        # downloaded and CadQuery never re-executed either source.
        self.assertEqual(supabase.storage.bucket.downloads, [CANDIDATE_PATH])

    def test_incompatible_checker_version_snapshot_is_not_reused(self):
        supabase = self._supabase()
        supabase.snapshot_rows[(HASH_B, GEOMETRY_CHECKER_VERSION + 1)] = {
            "source_sha256": HASH_B,
            "geometry_checker_version": GEOMETRY_CHECKER_VERSION + 1,
            "execution_ok": True,
            "geometry_valid": True,
            "volume_mm3": 99999.0,
        }

        outcome = geometry_check_job(supabase, _job())

        # A row measured under a different checker version is a different
        # cache identity; the current version is (re)computed for real rather
        # than trusting a row whose measurements may mean something else.
        self.assertNotEqual(outcome["report"]["geometry"]["volume_mm3"], 99999.0)
        self.assertAlmostEqual(outcome["report"]["geometry"]["volume_mm3"], 2000.0, places=6)

    def test_a_to_b_compares_a_against_b(self):
        supabase = self._supabase()

        outcome = geometry_check_job(supabase, _job())

        self.assertEqual(outcome["report"]["previous_source_sha256"], HASH_A)

    def test_after_another_mutation_b_to_c_compares_b_against_c_not_a_against_c(self):
        model_c = MODEL_B.replace("params.width * 2", "params.width * 3")
        hash_c = hashlib.sha256(model_c.encode()).hexdigest()
        supabase = self._supabase()
        # First check: A -> B, persists B's snapshot.
        geometry_check_job(supabase, _job())

        candidate_path_c = CANDIDATE_PATH
        supabase.storage.bucket.files[candidate_path_c] = model_c.encode()
        job_bc = _job(
            id="job-2",
            source_sha256=hash_c,
            previous_source_storage_path=None,
            previous_source_sha256=HASH_B,
        )

        outcome = geometry_check_job(supabase, job_bc)

        self.assertEqual(outcome["report"]["previous_source_sha256"], HASH_B)
        self.assertNotEqual(outcome["report"]["previous_source_sha256"], HASH_A)

    def test_first_candidate_with_no_predecessor_returns_no_fabricated_delta(self):
        supabase = self._supabase()
        job = _job(previous_source_storage_path=None, previous_source_sha256=None)

        outcome = geometry_check_job(supabase, job)

        self.assertIsNone(outcome["report"]["delta"])
        self.assertIsNone(outcome["report"]["previous_source_sha256"])
        self.assertEqual(outcome["report"]["warnings"], [])

    def test_stale_candidate_hash_mismatch_is_cancelled_not_returned_as_evidence(self):
        supabase = self._supabase()
        job = _job(source_sha256=HASH_A)  # candidate storage actually holds MODEL_B

        outcome = geometry_check_job(supabase, job)

        self.assertEqual(outcome["status"], "cancelled")
        self.assertTrue(outcome["report"]["superseded"])
        self.assertIsNone(outcome["report"]["geometry"])

    def test_cad_execution_failure_marks_job_failed_not_completed(self):
        broken_model = MODEL_B.replace("cq.Workplane", "cq.NotARealThing")
        broken_hash = hashlib.sha256(broken_model.encode()).hexdigest()
        supabase = self._supabase(**{CANDIDATE_PATH: broken_model.encode()})
        job = _job(source_sha256=broken_hash)

        outcome = geometry_check_job(supabase, job)

        self.assertEqual(outcome["status"], "failed")
        self.assertFalse(outcome["report"]["geometry"]["execution_ok"])
        self.assertIsNotNone(outcome["error_message"])

    def test_execution_failure_message_names_the_responsible_function_and_line(self):
        # A helper function two calls deep raises -- the message must point at
        # that helper, not at build_model or the runner's own internals, so
        # an agent reading it can fix the actual broken feature.
        model = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    width: float = 10.0

@cad_part(
    semantic_id="broken_helper",
    role="broken_helper_role",
    library="cadquery",
    parameters=("width",),
    depends_on=(),
    search_keys=("broken helper",),
)
def build_broken_helper(params: ModelParams):
    profile = cq.Workplane("XY").rect(params.width, params.width)
    return cq.Workplane("XY").placeSketch(profile).extrude(params.width)

def build_model(params: ModelParams):
    return build_broken_helper(params)
"""
        model_hash = hashlib.sha256(model.encode()).hexdigest()
        supabase = self._supabase(**{CANDIDATE_PATH: model.encode()})
        job = _job(source_sha256=model_hash)

        outcome = geometry_check_job(supabase, job)

        message = outcome["report"]["geometry"]["error_message"]
        self.assertIn("build_broken_helper", message)
        self.assertIn("model.py:", message)
        self.assertNotIn("build_model", message.split("(in ", 1)[1])

    def test_a_static_safety_rejection_forwards_the_validator_diagnostics(self):
        # A feature whose declared depends_on contradicts build_model's actual
        # dataflow -- the exact defect that once stalled a real run for twenty
        # rounds because this report was discarded and replaced with a fixed
        # string naming neither the rule, the function, nor the line.
        model = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    width: float = 10.0

@cad_part(
    semantic_id="base_plate",
    role="base_plate_role",
    library="cadquery",
    parameters=("width",),
    depends_on=(),
    search_keys=("base plate",),
)
def build_base_plate(params: ModelParams):
    return cq.Workplane("XY").box(params.width, params.width, 2)

@cad_part(
    semantic_id="riser",
    role="riser_role",
    library="cadquery",
    parameters=("width",),
    depends_on=("nonexistent_feature",),
    search_keys=("riser",),
)
def build_riser(params: ModelParams, base_plate):
    return base_plate.faces(">Z").workplane().box(2, 2, params.width)

def build_model(params: ModelParams):
    base_plate = build_base_plate(params)
    return build_riser(params, base_plate)
"""
        model_hash = hashlib.sha256(model.encode()).hexdigest()
        supabase = self._supabase(**{CANDIDATE_PATH: model.encode()})
        job = _job(source_sha256=model_hash)

        outcome = geometry_check_job(supabase, job)

        self.assertEqual(outcome["status"], "failed")
        geometry = outcome["report"]["geometry"]
        diagnostics = geometry["diagnostics"]
        self.assertTrue(diagnostics, "the AST report must not be discarded")
        located = [d for d in diagnostics if d.get("function_name")]
        self.assertTrue(located, "a forwarded diagnostic must locate the defect")
        # The reason is legible in the message too, not only in the structure.
        self.assertNotEqual(
            geometry["error_message"],
            "Source failed static safety checks and was not executed.",
        )


class GeometryArtifactPersistenceTests(unittest.TestCase):
    """The B-rep artifact is produced by the job, not by agent reasoning."""

    def _supabase(self, **files) -> _FakeSupabase:
        merged = {
            CANDIDATE_PATH: MODEL_B.encode(),
            ORIGINAL_PATH: MODEL_A.encode(),
            PARAMS_PATH: b"{}",
        }
        merged.update(files)
        return _FakeSupabase(merged)

    def test_a_checked_candidate_leaves_a_brep_artifact_bound_to_its_source(self):
        supabase = self._supabase()

        geometry_check_job(supabase, _job())

        artifacts = list(supabase.artifact_rows.values())
        self.assertEqual(len(artifacts), 2, "current and previous both persist")
        current = [a for a in artifacts if a["source_sha256"] == HASH_B][0]
        self.assertEqual(current["project_id"], PROJECT_ID)
        self.assertEqual(current["part_id"], PART_ID)
        self.assertEqual(current["edit_job_id"], EDIT_JOB_ID)
        self.assertEqual(current["artifact_format"], "brep")
        self.assertEqual(len(current["artifact_digest"]), 64)
        self.assertGreater(current["artifact_bytes"], 0)

    def test_the_artifact_bytes_land_in_storage_beside_their_source(self):
        supabase = self._supabase()

        geometry_check_job(supabase, _job())

        expected = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/geometry/{HASH_B}.brep"
        self.assertIn(expected, supabase.storage.bucket.uploads)
        # A real B-rep, not an empty placeholder.
        self.assertTrue(supabase.storage.bucket.files[expected].startswith(b"DBRep"))

    def test_the_digest_matches_the_bytes_that_were_stored(self):
        supabase = self._supabase()

        geometry_check_job(supabase, _job())

        current = [a for a in supabase.artifact_rows.values() if a["source_sha256"] == HASH_B][0]
        stored = supabase.storage.bucket.files[current["artifact_storage_path"]]
        self.assertEqual(
            current["artifact_digest"], hashlib.sha256(stored).hexdigest()
        )
        # Source hash and artifact digest identify different things.
        self.assertNotEqual(current["artifact_digest"], current["source_sha256"])

    def test_the_snapshot_names_the_artifact_it_was_derived_from(self):
        supabase = self._supabase()

        geometry_check_job(supabase, _job())

        snapshot = [s for s in supabase.snapshot_rows.values() if s["source_sha256"] == HASH_B][0]
        artifact = [a for a in supabase.artifact_rows.values() if a["source_sha256"] == HASH_B][0]
        self.assertIsNotNone(snapshot["geometry_artifact_id"])
        self.assertEqual(snapshot["geometry_artifact_id"], artifact["id"])

    def test_the_artifact_records_the_runtime_that_produced_it(self):
        supabase = self._supabase()

        geometry_check_job(supabase, _job())

        current = [a for a in supabase.artifact_rows.values() if a["source_sha256"] == HASH_B][0]
        runtime = current["geometry_runtime"]
        self.assertIn("cadquery", runtime)
        self.assertIn("geometry_checker_version", runtime)

    def test_source_that_never_executes_persists_no_artifact(self):
        """A snapshot can exist without geometry; an artifact cannot."""

        broken = "def build_model(params):\n    raise ValueError('boom')\n"
        broken_hash = hashlib.sha256(broken.encode()).hexdigest()
        supabase = self._supabase(**{CANDIDATE_PATH: broken.encode()})

        result = geometry_check_job(supabase, _job(source_sha256=broken_hash))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            [a for a in supabase.artifact_rows.values() if a["source_sha256"] == broken_hash],
            [],
        )
        snapshot = [s for s in supabase.snapshot_rows.values() if s["source_sha256"] == broken_hash][0]
        self.assertIsNone(snapshot["geometry_artifact_id"])

    def test_raw_brep_topology_never_reaches_the_report(self):
        """The report is what Agent3D reads. It carries numbers, never a shape.

        The artifact is referenced by storage path and digest; the bytes stay
        in the bucket.
        """

        supabase = self._supabase()

        result = geometry_check_job(supabase, _job())

        serialized = json.dumps(result["report"])
        self.assertNotIn("DBRep", serialized)
        self.assertNotIn("CASCADE Topology", serialized)
        self.assertNotIn(".brep", serialized)


if __name__ == "__main__":
    unittest.main()
