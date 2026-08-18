"""The geometry service boundary: reuse, isolation, comparison, and loading."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import cadquery as cq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers" / "cad_validator"))

from geometry import (  # noqa: E402
    GEOMETRY_ARTIFACT_UNAVAILABLE,
    CadGeometryExtractor,
    CandidateSourceRef,
    GeometryAnalyzer,
    GeometryArtifactError,
    GeometryEngine,
)
from geometry.artifact import serialize_root  # noqa: E402
from geometry.runtime import GEOMETRY_CHECKER_VERSION  # noqa: E402

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PART_ID = "22222222-2222-4222-8222-222222222222"
CANDIDATE_A = "33333333-3333-4333-8333-333333333333"
CANDIDATE_B = "44444444-4444-4444-8444-444444444444"

EXTRACTOR = CadGeometryExtractor()
ANALYZER = GeometryAnalyzer()


class _Bucket:
    def __init__(self):
        self.files: dict[str, bytes] = {}
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
    def __init__(self, rows, log):
        self.rows = rows
        self.log = log
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
        matches = [
            row
            for row in self.rows.values()
            if all(row.get(k) == v for k, v in self._filters.items())
        ]
        self.log.append((self._filters.get("source_sha256"), len(matches)))
        return _Response(matches)


class _Response:
    def __init__(self, data):
        self.data = data


class _Supabase:
    def __init__(self):
        self.storage = _Storage(_Bucket())
        self.snapshots: dict[tuple, dict] = {}
        self.artifacts: dict[tuple, dict] = {}
        self.queries: list = []

    def table(self, name):
        rows = self.snapshots if name == "geometry_snapshots" else self.artifacts
        return _Table(rows, self.queries)


class _RecordingEngine(GeometryEngine):
    """Engine whose measurement step is observable and does not spawn a sandbox.

    Execution is exercised end-to-end in ``tests/test_cad_geometry_check_job.py``
    against real source. What these tests are about is what the engine does
    around that step: what it reuses, what it refuses, and what it links.
    """

    def __init__(self, supabase, models: dict[str, object]):
        super().__init__(supabase)
        self._models = models
        self.measured: list[str] = []
        self.workdir = Path(tempfile.mkdtemp())

    def measure_source(self, ref, *, source_bytes, workdir, timeout_seconds):
        self.measured.append(ref.source_sha256)
        model = self._models[source_bytes.decode("utf-8")]
        extracted = EXTRACTOR.extract(model)
        brep_path = self.workdir / f"{ref.source_sha256}.brep"
        digest, size = serialize_root(extracted.root, brep_path)
        snapshot = ANALYZER.analyze(extracted.root)
        return (
            snapshot,
            {"format": "brep", "digest": digest, "bytes": size, "runtime": {"cadquery": "test"}},
            brep_path,
        )


SOURCE_A = "model-a"
SOURCE_B = "model-b"
HASH_A = hashlib.sha256(SOURCE_A.encode()).hexdigest()
HASH_B = hashlib.sha256(SOURCE_B.encode()).hexdigest()
PATH_A = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{CANDIDATE_A}/model.py"
PATH_B = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{CANDIDATE_B}/model.py"

MODELS = {
    SOURCE_A: cq.Workplane("XY").box(10, 10, 10),
    SOURCE_B: cq.Workplane("XY").box(10, 10, 10).faces(">Z").workplane().circle(3).cutBlind(-4),
}


def ref_a():
    return CandidateSourceRef(PROJECT_ID, PART_ID, CANDIDATE_A, PATH_A, HASH_A)


def ref_b():
    return CandidateSourceRef(PROJECT_ID, PART_ID, CANDIDATE_B, PATH_B, HASH_B)


class _EngineCase(unittest.TestCase):
    def setUp(self):
        self.supabase = _Supabase()
        self.supabase.storage.bucket.files[PATH_A] = SOURCE_A.encode()
        self.supabase.storage.bucket.files[PATH_B] = SOURCE_B.encode()
        self.engine = _RecordingEngine(self.supabase, MODELS)
        self.workdir = Path(tempfile.mkdtemp())

    def derive(self, ref, source):
        return self.engine.snapshot_for(
            ref,
            source_bytes=source.encode("utf-8"),
            workdir=self.workdir / ref.source_sha256[:8],
            timeout_seconds=30,
        )


class SnapshotDerivationTests(_EngineCase):
    def test_a_derived_snapshot_is_linked_to_the_artifact_it_came_from(self):
        result = self.derive(ref_a(), SOURCE_A)

        self.assertIsNotNone(result.artifact)
        self.assertEqual(result.artifact.candidate_id, CANDIDATE_A)
        self.assertEqual(result.artifact.source_hash, HASH_A)
        stored = self.supabase.snapshots[(HASH_A, GEOMETRY_CHECKER_VERSION)]
        self.assertEqual(stored["geometry_artifact_id"], result.artifact.artifact_id)

    def test_the_snapshot_carries_metrics_derived_from_the_brep_root(self):
        snapshot = self.derive(ref_a(), SOURCE_A).snapshot

        self.assertAlmostEqual(snapshot["volume_mm3"], 1000.0, places=6)
        self.assertAlmostEqual(snapshot["surface_area_mm2"], 600.0, places=6)
        self.assertEqual(snapshot["solid_count"], 1)
        self.assertEqual(snapshot["face_count"], 6)
        self.assertEqual(snapshot["edge_count"], 12)
        self.assertEqual(snapshot["vertex_count"], 8)
        self.assertTrue(snapshot["geometry_valid"])

    def test_an_already_measured_source_is_not_measured_again(self):
        self.derive(ref_a(), SOURCE_A)
        self.assertEqual(self.engine.measured, [HASH_A])

        again = self.derive(ref_a(), SOURCE_A)

        self.assertEqual(self.engine.measured, [HASH_A], "no second execution")
        self.assertTrue(again.from_cache)
        self.assertAlmostEqual(again.snapshot["volume_mm3"], 1000.0, places=6)

    def test_a_snapshot_measured_under_an_older_checker_version_is_not_reused(self):
        self.supabase.snapshots[(HASH_A, GEOMETRY_CHECKER_VERSION - 1)] = {
            "source_sha256": HASH_A,
            "geometry_checker_version": GEOMETRY_CHECKER_VERSION - 1,
            "volume_mm3": 999999.0,
        }

        result = self.derive(ref_a(), SOURCE_A)

        self.assertFalse(result.from_cache)
        self.assertAlmostEqual(result.snapshot["volume_mm3"], 1000.0, places=6)


class CandidateIsolationTests(_EngineCase):
    """One candidate must never receive another candidate's geometry.

    Isolation here is a property of content addressing: geometry resolves by
    source hash, and the hash is re-verified against the bytes actually stored
    before anything derived from them counts as evidence.
    """

    def test_candidate_b_does_not_receive_candidate_a_geometry(self):
        self.derive(ref_a(), SOURCE_A)

        result = self.derive(ref_b(), SOURCE_B)

        self.assertNotAlmostEqual(
            result.snapshot["volume_mm3"], 1000.0, places=3
        )
        self.assertEqual(self.engine.measured, [HASH_A, HASH_B])

    def test_asking_for_a_hash_nothing_has_measured_yields_nothing(self):
        self.derive(ref_a(), SOURCE_A)

        self.assertIsNone(self.engine.cached(HASH_B))

    def test_a_ref_whose_path_no_longer_holds_its_hash_resolves_nothing(self):
        """A stale path describes a different candidate than the ref claims."""

        mismatched = CandidateSourceRef(PROJECT_ID, PART_ID, CANDIDATE_B, PATH_A, HASH_B)

        resolved = self.engine.resolve(
            mismatched, workdir=self.workdir / "stale", timeout_seconds=30
        )

        self.assertIsNone(resolved)
        self.assertEqual(self.engine.measured, [], "nothing was executed")

    def test_a_ref_with_no_hash_resolves_nothing(self):
        empty = CandidateSourceRef(PROJECT_ID, PART_ID, CANDIDATE_A, PATH_A, "")

        self.assertIsNone(
            self.engine.resolve(empty, workdir=self.workdir / "e", timeout_seconds=30)
        )

    def test_a_candidates_artifact_path_is_scoped_to_that_candidate(self):
        self.derive(ref_a(), SOURCE_A)
        self.derive(ref_b(), SOURCE_B)

        uploads = self.supabase.storage.bucket.uploads
        self.assertIn(f"{PROJECT_ID}/candidates/cad/{PART_ID}/{CANDIDATE_A}/geometry/{HASH_A}.brep", uploads)
        self.assertIn(f"{PROJECT_ID}/candidates/cad/{PART_ID}/{CANDIDATE_B}/geometry/{HASH_B}.brep", uploads)


class ComparisonTests(_EngineCase):
    def test_comparing_two_candidates_reproduces_the_existing_deltas(self):
        previous = self.derive(ref_a(), SOURCE_A)
        current = self.derive(ref_b(), SOURCE_B)

        delta, warnings = self.engine.compare(previous, current)

        # A cavity was cut: less material, same outer box, more faces/edges.
        self.assertLess(delta["volume_mm3"], 0.0)
        self.assertLess(delta["volume_percent"], 0.0)
        self.assertFalse(delta["bbox_changed"])
        self.assertEqual(delta["solid_count"], 0)
        self.assertGreater(delta["face_count"], 0)
        self.assertGreater(delta["edge_count"], 0)
        self.assertFalse(delta["validity_changed"])
        self.assertNotIn("NO_GEOMETRIC_CHANGE", warnings)

    def test_comparing_a_candidate_with_itself_reports_no_geometric_change(self):
        first = self.derive(ref_a(), SOURCE_A)
        second = self.derive(ref_a(), SOURCE_A)

        delta, warnings = self.engine.compare(first, second)

        self.assertEqual(delta["volume_mm3"], 0.0)
        self.assertIn("NO_GEOMETRIC_CHANGE", warnings)

    def test_no_previous_candidate_fabricates_no_delta(self):
        current = self.derive(ref_a(), SOURCE_A)

        delta, warnings = self.engine.compare(None, current)

        self.assertIsNone(delta)
        self.assertEqual(warnings, [])


class NativeGeometryLoadTests(_EngineCase):
    def test_a_recorded_artifact_can_be_reloaded_as_native_geometry(self):
        """The read side of the source of truth: topology without re-execution.

        Nothing above the geometry layer calls this today. It is implemented
        and tested so future bounded queries have a working seam rather than a
        speculative one.
        """

        self.derive(ref_a(), SOURCE_A)

        root = self.engine.load_root(ref_a(), self.workdir / "load")

        self.assertIsInstance(root, cq.Shape)
        self.assertEqual(len(root.Solids()), 1)
        self.assertAlmostEqual(
            ANALYZER.analyze(root)["volume_mm3"], 1000.0, places=6
        )

    def test_loading_geometry_that_was_never_recorded_is_a_structured_failure(self):
        with self.assertRaises(GeometryArtifactError) as caught:
            self.engine.load_root(ref_a(), self.workdir / "missing")

        self.assertEqual(caught.exception.error_code, GEOMETRY_ARTIFACT_UNAVAILABLE)

    def test_an_artifact_whose_bytes_were_tampered_with_is_refused(self):
        """The digest is an integrity check, and it is actually enforced."""

        self.derive(ref_a(), SOURCE_A)
        stored_path = self.supabase.artifacts[(HASH_A, GEOMETRY_CHECKER_VERSION)][
            "artifact_storage_path"
        ]
        self.supabase.storage.bucket.files[stored_path] = b"corrupted"

        with self.assertRaises(GeometryArtifactError) as caught:
            self.engine.load_root(ref_a(), self.workdir / "corrupt")

        self.assertEqual(caught.exception.error_code, GEOMETRY_ARTIFACT_UNAVAILABLE)
        self.assertIn("digest", caught.exception.message)


class DegradedPersistenceTests(_EngineCase):
    def test_a_failed_artifact_upload_does_not_cost_the_snapshot(self):
        """Persistence failure degrades future queries; it loses no evidence."""

        def refuse(*_args, **_kwargs):
            raise RuntimeError("bucket unavailable")

        self.supabase.storage.bucket.upload = refuse

        result = self.derive(ref_a(), SOURCE_A)

        self.assertIsNone(result.artifact)
        self.assertAlmostEqual(result.snapshot["volume_mm3"], 1000.0, places=6)
        stored = self.supabase.snapshots[(HASH_A, GEOMETRY_CHECKER_VERSION)]
        self.assertIsNone(stored["geometry_artifact_id"])
        self.assertAlmostEqual(stored["volume_mm3"], 1000.0, places=6)


if __name__ == "__main__":
    unittest.main()
