from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "supabase" / "migrations" / "20260808000000_add_geometry_check.sql"
).read_text(encoding="utf-8")


class GeometryCheckMigrationTests(unittest.TestCase):
    def test_generation_jobs_gains_the_geometry_check_type(self):
        self.assertIn(
            "check (type in ('export_cad', 'export_mesh', 'validate_cad', 'geometry_check'))",
            MIGRATION,
        )
        self.assertIn("previous_source_storage_path text", MIGRATION)
        self.assertIn("previous_source_sha256 text", MIGRATION)

    def test_edit_jobs_gains_the_last_checked_source_history_column(self):
        self.assertIn("add column last_checked_source_sha256 text", MIGRATION)
        self.assertIn(
            "last_checked_source_sha256 ~ '^[0-9a-f]{64}$'", MIGRATION
        )

    def test_geometry_snapshots_table_is_hash_bound_and_cache_keyed(self):
        self.assertIn("create table public.geometry_snapshots", MIGRATION)
        self.assertIn("source_sha256 text not null check (source_sha256 ~", MIGRATION)
        self.assertIn("geometry_checker_version integer not null default 1", MIGRATION)
        self.assertIn("unique (source_sha256, geometry_checker_version)", MIGRATION)

    def test_geometry_snapshots_separates_execution_from_validity(self):
        self.assertIn("execution_ok boolean not null", MIGRATION)
        self.assertIn("geometry_valid boolean", MIGRATION)
        self.assertIn(
            "check (execution_ok = false or geometry_valid is not null)", MIGRATION
        )

    def test_bounding_box_preserves_position_not_only_size(self):
        bounding_box_column = MIGRATION.split("bounding_box jsonb", 1)[1][:200]
        self.assertIn("jsonb_typeof(bounding_box) = 'object'", bounding_box_column)

    def test_queue_geometry_check_resolves_previous_hash_and_updates_history(self):
        self.assertIn("create function public.queue_geometry_check", MIGRATION)
        self.assertIn("edit_job.last_checked_source_sha256", MIGRATION)
        self.assertIn("edit_job.accepted_source_sha256", MIGRATION)
        self.assertIn(
            "set last_checked_source_sha256 = p_candidate_sha256", MIGRATION
        )

    def test_queue_geometry_check_does_not_require_worker_lease_ownership(self):
        queue_function = MIGRATION.split(
            "create function public.queue_geometry_check", 1
        )[1].split("create function public.complete_geometry_check", 1)[0]
        self.assertNotIn("p_worker_id", queue_function)

    def test_complete_geometry_check_is_hash_guarded(self):
        self.assertIn("create function public.complete_geometry_check", MIGRATION)
        complete_function = MIGRATION.split(
            "create function public.complete_geometry_check", 1
        )[1]
        self.assertIn("geometry_job.source_sha256 <> p_source_sha256", complete_function)
        self.assertIn("geometry_job.status <> 'running'", complete_function)

    def test_new_functions_are_locked_to_service_role(self):
        self.assertIn(
            "revoke all on function public.queue_geometry_check(uuid, text)",
            MIGRATION,
        )
        self.assertIn(
            "grant execute on function public.queue_geometry_check(uuid, text)\n  to service_role;",
            MIGRATION,
        )
        self.assertIn(
            "grant execute on function public.complete_geometry_check(uuid, text, jsonb)\n  to service_role;",
            MIGRATION,
        )


if __name__ == "__main__":
    unittest.main()
