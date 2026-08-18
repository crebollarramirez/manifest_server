"""The B-rep artifact: serialization, identity, and what a digest does not mean."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import cadquery as cq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers" / "cad_validator"))

from geometry import CadGeometryExtractor, GeometryAnalyzer  # noqa: E402
from geometry.artifact import (  # noqa: E402
    BREP_EXPORT_FAILED,
    GEOMETRY_ARTIFACT_UNAVAILABLE,
    GeometryArtifact,
    GeometryArtifactError,
    artifact_storage_path,
    load_root,
    serialize_root,
    verify_digest,
)

EXTRACTOR = CadGeometryExtractor()
ANALYZER = GeometryAnalyzer()


def root_of(model):
    return EXTRACTOR.extract(model).root


class SerializationTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp())

    def test_serialize_writes_a_brep_and_reports_its_digest_and_size(self):
        destination = self.workdir / "model.brep"

        digest, size = serialize_root(root_of(cq.Workplane("XY").box(10, 10, 10)), destination)

        raw = destination.read_bytes()
        self.assertTrue(raw)
        self.assertEqual(size, len(raw))
        # The digest must be of the bytes actually written, not of an in-memory
        # buffer -- CadQuery's file and BytesIO export paths differ, so hashing
        # anything else would make the integrity check unverifiable on reload.
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_serialize_creates_missing_parent_directories(self):
        destination = self.workdir / "nested" / "deeper" / "model.brep"

        serialize_root(root_of(cq.Workplane("XY").box(2, 2, 2)), destination)

        self.assertTrue(destination.exists())

    def test_an_unwritable_destination_is_a_structured_failure(self):
        blocked = self.workdir / "blocked"
        blocked.write_text("not a directory")

        with self.assertRaises(GeometryArtifactError) as caught:
            serialize_root(root_of(cq.Workplane("XY").box(2, 2, 2)), blocked / "model.brep")

        self.assertEqual(caught.exception.error_code, BREP_EXPORT_FAILED)


class ArtifactIdentityTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp())

    def _digest(self, model, name):
        digest, _size = serialize_root(root_of(model), self.workdir / name)
        return digest

    def test_the_same_construction_serializes_to_the_same_digest(self):
        """Determinism is what makes the digest usable as an integrity check."""

        first = self._digest(cq.Workplane("XY").box(10, 10, 10), "a.brep")
        second = self._digest(cq.Workplane("XY").box(10, 10, 10), "b.brep")

        self.assertEqual(first, second)

    def test_identical_geometry_built_differently_has_a_different_digest(self):
        """The digest identifies bytes, not geometry, and this is why.

        A 10mm cube is a 10mm cube however it was built. OCCT's B-rep
        serialization is deterministic per construction but not canonical
        across constructions, so these two shapes -- equal in volume, bounding
        box, and every count -- serialize differently.

        Any future code that reads "different digest" as "different physical
        geometry" is wrong, and this test is the standing evidence.
        """

        boxed = cq.Workplane("XY").box(10, 10, 10)
        extruded = cq.Workplane("XY").rect(10, 10).extrude(10).translate((0, 0, -5))

        boxed_snapshot = ANALYZER.analyze(root_of(boxed))
        extruded_snapshot = ANALYZER.analyze(root_of(extruded))
        self.assertAlmostEqual(
            boxed_snapshot["volume_mm3"], extruded_snapshot["volume_mm3"], places=6
        )
        self.assertEqual(boxed_snapshot["face_count"], extruded_snapshot["face_count"])

        self.assertNotEqual(self._digest(boxed, "boxed.brep"), self._digest(extruded, "extruded.brep"))

    def test_verify_digest_rejects_bytes_that_do_not_match(self):
        with self.assertRaises(GeometryArtifactError) as caught:
            verify_digest(b"tampered", hashlib.sha256(b"original").hexdigest())

        self.assertEqual(caught.exception.error_code, GEOMETRY_ARTIFACT_UNAVAILABLE)

    def test_verify_digest_accepts_matching_bytes(self):
        verify_digest(b"original", hashlib.sha256(b"original").hexdigest())


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp())

    def test_a_reloaded_artifact_reproduces_the_snapshot_it_was_measured_from(self):
        """Counts and validity exactly; floats within OCCT's round-trip noise.

        This is what makes the artifact usable as a source of truth: the
        topology can be recovered later without re-executing model.py. It is
        also why snapshots are derived once, before serialization -- reloading
        shifts floats at roughly 1e-13 relative, so a re-derived snapshot would
        not be byte-identical to the stored one.
        """

        model = cq.Workplane("XY").box(10, 10, 10).faces(">Z").workplane().circle(2).extrude(3)
        destination = self.workdir / "model.brep"
        original_root = root_of(model)
        before = ANALYZER.analyze(original_root)
        serialize_root(original_root, destination)

        reloaded = load_root(destination.read_bytes(), self.workdir / "reload")
        after = ANALYZER.analyze(reloaded)

        for key in ("solid_count", "face_count", "edge_count", "vertex_count",
                    "geometry_valid", "non_planar_face_count", "sharp_edge_count"):
            self.assertEqual(before[key], after[key], key)
        self.assertAlmostEqual(
            before["volume_mm3"], after["volume_mm3"], delta=abs(before["volume_mm3"]) * 1e-9
        )
        self.assertAlmostEqual(
            before["surface_area_mm2"],
            after["surface_area_mm2"],
            delta=abs(before["surface_area_mm2"]) * 1e-9,
        )
        for axis, (small, large) in enumerate(zip(before["bounding_box"]["size"], after["bounding_box"]["size"])):
            self.assertAlmostEqual(small, large, places=9, msg=f"bbox axis {axis}")

    def test_multiple_solids_survive_the_round_trip(self):
        model = cq.Workplane("XY").box(4, 4, 4).union(
            cq.Workplane("XY").transformed(offset=(20, 0, 0)).box(2, 2, 2)
        )
        destination = self.workdir / "two.brep"
        serialize_root(root_of(model), destination)

        reloaded = load_root(destination.read_bytes(), self.workdir / "reload")

        self.assertEqual(len(reloaded.Solids()), 2)

    def test_corrupted_bytes_are_a_structured_failure_not_a_crash(self):
        with self.assertRaises(GeometryArtifactError) as caught:
            load_root(b"this is not a brep file", self.workdir / "reload")

        self.assertEqual(caught.exception.error_code, GEOMETRY_ARTIFACT_UNAVAILABLE)
        self.assertNotIn("Traceback", caught.exception.message)


class ArtifactProvenanceTests(unittest.TestCase):
    def test_the_artifact_carries_the_provenance_needed_to_identify_it(self):
        artifact = GeometryArtifact(
            artifact_id="artifact-1",
            project_id="project-1",
            part_id="part-1",
            candidate_id="edit-job-1",
            source_hash="a" * 64,
            source_storage_path="project-1/candidates/cad/part-1/edit-job-1/model.py",
            artifact_format="brep",
            artifact_storage_path="project-1/candidates/cad/part-1/edit-job-1/geometry/"
            + "a" * 64
            + ".brep",
            artifact_digest="b" * 64,
            artifact_bytes=3848,
        )

        self.assertEqual(artifact.candidate_id, "edit-job-1")
        self.assertEqual(artifact.source_hash, "a" * 64)
        # Source hash and artifact digest are different identities and must not
        # be conflated.
        self.assertNotEqual(artifact.source_hash, artifact.artifact_digest)


class ArtifactPathTests(unittest.TestCase):
    def test_the_artifact_path_is_derived_from_where_its_source_lives(self):
        self.assertEqual(
            artifact_storage_path("p/candidates/cad/part/job/model.py", "a" * 64),
            f"p/candidates/cad/part/job/geometry/{'a' * 64}.brep",
        )

    def test_accepted_source_artifacts_sit_beside_the_accepted_source(self):
        """The previous side of a comparison is frequently accepted source."""

        self.assertEqual(
            artifact_storage_path("p/parts/cad/part/model.py", "b" * 64),
            f"p/parts/cad/part/geometry/{'b' * 64}.brep",
        )

    def test_two_candidates_never_collide_on_one_artifact_path(self):
        first = artifact_storage_path("p/candidates/cad/part/job-a/model.py", "a" * 64)
        second = artifact_storage_path("p/candidates/cad/part/job-b/model.py", "b" * 64)

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
