from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
sys.path.insert(0, str(ROOT / "workers" / "cad_validator"))
from geometry_check_job import _GEOMETRY_FIELDS  # noqa: E402
GENERATION_JOBS = (
    MIGRATIONS / "20260712050000_generation_jobs.sql"
).read_text(encoding="utf-8")
EDIT_JOBS = (MIGRATIONS / "20260712040000_edit_jobs.sql").read_text(encoding="utf-8")
GEOMETRY_SNAPSHOTS = (
    MIGRATIONS / "20260712070000_geometry_snapshots.sql"
).read_text(encoding="utf-8")
GEOMETRY_ARTIFACTS = (
    MIGRATIONS / "20260712150000_geometry_artifacts.sql"
).read_text(encoding="utf-8")
SURFACE_METRICS = (
    MIGRATIONS / "20260712160000_geometry_snapshot_surface_metrics.sql"
).read_text(encoding="utf-8")


class GeometryCheckMigrationTests(unittest.TestCase):
    def test_generation_jobs_gains_the_geometry_check_type(self):
        self.assertIn(
            "check (type in ('export_cad', 'export_mesh', 'validate_cad', 'geometry_check'))",
            GENERATION_JOBS,
        )
        self.assertIn("previous_source_storage_path text", GENERATION_JOBS)
        self.assertIn("previous_source_sha256 text", GENERATION_JOBS)

    def test_edit_jobs_gains_the_last_checked_source_history_column(self):
        self.assertIn("last_checked_source_sha256 text", EDIT_JOBS)
        self.assertIn(
            "last_checked_source_sha256 ~ '^[0-9a-f]{64}$'", EDIT_JOBS
        )

    def test_geometry_snapshots_table_is_hash_bound_and_cache_keyed(self):
        self.assertIn("create table public.geometry_snapshots", GEOMETRY_SNAPSHOTS)
        self.assertIn(
            "source_sha256 text not null check (source_sha256 ~", GEOMETRY_SNAPSHOTS
        )
        self.assertIn(
            "geometry_checker_version integer not null default 1", GEOMETRY_SNAPSHOTS
        )
        self.assertIn(
            "unique (source_sha256, geometry_checker_version)", GEOMETRY_SNAPSHOTS
        )

    def test_geometry_snapshots_separates_execution_from_validity(self):
        self.assertIn("execution_ok boolean not null", GEOMETRY_SNAPSHOTS)
        self.assertIn("geometry_valid boolean", GEOMETRY_SNAPSHOTS)
        self.assertIn(
            "check (execution_ok = false or geometry_valid is not null)",
            GEOMETRY_SNAPSHOTS,
        )

    def test_every_persisted_geometry_field_exists_as_a_column(self):
        """The worker writes ``_GEOMETRY_FIELDS`` verbatim as column names.

        A name in that tuple with no column behind it is not a degraded
        write -- PostgREST rejects the whole insert with PGRST204, the error
        escapes the duplicate-key handler, and the validator worker dies, so
        ``check_geometry`` fails 100% of the time and Agent3D never gets the
        geometric evidence the tool exists to provide. That is exactly how
        ``diagnostics`` shipped broken.

        Checked against every migration, not just the create-table one, so a
        column added by a later ``alter table`` counts.
        """

        schema = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
        )
        snapshot_ddl = "".join(
            block
            for block in schema.split("create table public.")
            if block.startswith("geometry_snapshots")
        ) + "".join(
            block
            for block in schema.split("alter table public.geometry_snapshots")[1:]
        )
        missing = [
            field for field in _GEOMETRY_FIELDS if f"{field} " not in snapshot_ddl
        ]
        self.assertEqual(missing, [], f"geometry_snapshots has no column for: {missing}")

    def test_bounding_box_preserves_position_not_only_size(self):
        bounding_box_column = GEOMETRY_SNAPSHOTS.split("bounding_box jsonb", 1)[1][:200]
        self.assertIn("jsonb_typeof(bounding_box) = 'object'", bounding_box_column)

    def test_queue_geometry_check_resolves_previous_hash_and_updates_history(self):
        self.assertIn("create function public.queue_geometry_check", GENERATION_JOBS)
        self.assertIn("edit_job.last_checked_source_sha256", GENERATION_JOBS)
        self.assertIn("edit_job.accepted_source_sha256", GENERATION_JOBS)
        self.assertIn(
            "set last_checked_source_sha256 = p_candidate_sha256", GENERATION_JOBS
        )

    def test_queue_geometry_check_does_not_require_worker_lease_ownership(self):
        queue_function = GENERATION_JOBS.split(
            "create function public.queue_geometry_check", 1
        )[1].split("create function public.complete_geometry_check", 1)[0]
        self.assertNotIn("p_worker_id", queue_function)

    def test_complete_geometry_check_is_hash_guarded(self):
        self.assertIn("create function public.complete_geometry_check", GENERATION_JOBS)
        complete_function = GENERATION_JOBS.split(
            "create function public.complete_geometry_check", 1
        )[1]
        self.assertIn("geometry_job.source_sha256 <> p_source_sha256", complete_function)
        self.assertIn("geometry_job.status <> 'running'", complete_function)

    def test_new_functions_are_locked_to_service_role(self):
        self.assertIn(
            "revoke all on function public.queue_geometry_check(uuid, text)",
            GENERATION_JOBS,
        )
        self.assertIn(
            "grant execute on function public.queue_geometry_check(uuid, text)\n  to service_role;",
            GENERATION_JOBS,
        )
        self.assertIn(
            "grant execute on function public.complete_geometry_check(uuid, text, jsonb)\n  to service_role;",
            GENERATION_JOBS,
        )


class GeometryArtifactMigrationTests(unittest.TestCase):
    """The B-rep artifact table is what makes a snapshot a derived observation.

    Before it, a candidate's geometry existed only as the numbers in
    ``geometry_snapshots``; the shape itself died with the sandbox subprocess.
    These tests pin the properties that make the artifact usable as a source of
    truth rather than as another cache.
    """

    def test_artifact_is_bound_to_the_candidate_and_its_source(self):
        self.assertIn("create table public.geometry_artifacts", GEOMETRY_ARTIFACTS)
        for column in ("project_id uuid not null", "part_id uuid not null"):
            self.assertIn(column, GEOMETRY_ARTIFACTS)
        # candidate_id is the edit-job id everywhere in this system.
        self.assertIn("edit_job_id uuid references public.edit_jobs(id)", GEOMETRY_ARTIFACTS)
        self.assertIn(
            "source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$')",
            GEOMETRY_ARTIFACTS,
        )
        self.assertIn(
            "foreign key (project_id, part_id)\n    references public.parts(project_id, id)",
            GEOMETRY_ARTIFACTS,
        )

    def test_artifact_records_a_reference_not_the_geometry_itself(self):
        """Large native B-rep structures stay in object storage, not in a row.

        A bytea column here would put the whole topology one careless
        ``select *`` away from an agent-facing payload.
        """

        self.assertIn("artifact_storage_path text not null", GEOMETRY_ARTIFACTS)
        self.assertIn("artifact_format text not null check (artifact_format in ('brep'))", GEOMETRY_ARTIFACTS)
        self.assertIn("artifact_bytes bigint not null", GEOMETRY_ARTIFACTS)
        self.assertNotIn("bytea", GEOMETRY_ARTIFACTS)

    def test_artifact_digest_is_integrity_not_geometry_identity(self):
        """The digest identifies bytes; the source hash identifies geometry.

        OCCT's B-rep serialization is deterministic per construction but not
        canonical across constructions -- a cube built with ``.box()`` and the
        same cube built with ``.rect().extrude()`` serialize to different
        bytes. A unique constraint or cache key on the digest would encode the
        false claim that different bytes mean different geometry.
        """

        self.assertIn(
            "artifact_digest text not null check (artifact_digest ~ '^[0-9a-f]{64}$')",
            GEOMETRY_ARTIFACTS,
        )
        self.assertIn(
            "unique (source_sha256, geometry_checker_version)", GEOMETRY_ARTIFACTS
        )
        self.assertNotIn("unique (artifact_digest", GEOMETRY_ARTIFACTS)

    def test_artifact_records_the_runtime_that_produced_it(self):
        self.assertIn("geometry_runtime jsonb", GEOMETRY_ARTIFACTS)
        self.assertIn("jsonb_typeof(geometry_runtime) = 'object'", GEOMETRY_ARTIFACTS)

    def test_snapshot_names_the_artifact_it_observed(self):
        """A snapshot has to be traceable to the exact geometry it summarizes.

        ``on delete set null`` rather than cascade: a measurement whose
        artifact went away is still a valid measurement of what was there.
        """

        self.assertIn(
            "alter table public.geometry_snapshots\n  add column geometry_artifact_id uuid\n"
            "    references public.geometry_artifacts(id) on delete set null;",
            GEOMETRY_ARTIFACTS,
        )

    def test_artifacts_are_service_role_only_like_every_other_table(self):
        self.assertIn(
            "alter table public.geometry_artifacts enable row level security",
            GEOMETRY_ARTIFACTS,
        )

    def test_surface_metrics_complete_the_snapshot_vocabulary(self):
        self.assertIn("surface_area_mm2 double precision", SURFACE_METRICS)
        self.assertIn("vertex_count integer", SURFACE_METRICS)
        self.assertIn("vertex_count is null or vertex_count >= 0", SURFACE_METRICS)


if __name__ == "__main__":
    unittest.main()
