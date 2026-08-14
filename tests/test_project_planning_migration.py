from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "supabase" / "migrations" / "20260712080000_project_planning_jobs.sql"
).read_text(encoding="utf-8")


class ProjectPlanningJobsMigrationTests(unittest.TestCase):
    def test_table_has_no_lease_type_or_singleton_columns(self):
        self.assertIn("create table public.project_planning_jobs", MIGRATION)
        self.assertNotIn("worker_id text", MIGRATION)
        self.assertNotIn("lease_expires_at", MIGRATION)
        self.assertNotIn("heartbeat_at", MIGRATION)
        self.assertNotIn("type text", MIGRATION)

    def test_plan_and_spec_are_separate_typed_jsonb_columns(self):
        self.assertIn(
            "project_plan jsonb\n    check (project_plan is null or "
            "jsonb_typeof(project_plan) = 'object')",
            MIGRATION,
        )
        self.assertIn(
            "assembly_spec jsonb\n    check (assembly_spec is null or "
            "jsonb_typeof(assembly_spec) = 'object')",
            MIGRATION,
        )

    def test_completed_jobs_always_have_both_artifacts(self):
        self.assertIn(
            "status <> 'completed'\n    or (project_plan is not null and "
            "assembly_spec is not null)",
            MIGRATION,
        )

    def test_error_code_column_exists_alongside_error_message(self):
        self.assertIn("error_code text", MIGRATION)
        self.assertIn("error_message text", MIGRATION)

    def test_claim_function_uses_skip_locked_and_sets_running(self):
        self.assertIn(
            "create function public.claim_next_project_planning_job", MIGRATION
        )
        self.assertIn("for update skip locked", MIGRATION)
        self.assertIn(
            "set\n    status = 'running',\n    started_at = now(),", MIGRATION
        )

    def test_claim_function_is_service_role_only(self):
        self.assertIn("alter table public.project_planning_jobs enable row level security", MIGRATION)
        self.assertIn(
            "revoke all on function public.claim_next_project_planning_job()\n"
            "  from public, anon, authenticated;",
            MIGRATION,
        )
        self.assertIn(
            "grant execute on function public.claim_next_project_planning_job()\n"
            "  to service_role;",
            MIGRATION,
        )

    def test_no_policies_are_created(self):
        self.assertNotIn("create policy", MIGRATION)


if __name__ == "__main__":
    unittest.main()
