from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
ASSEMBLIES = (MIGRATIONS / "20260712090000_assemblies.sql").read_text(encoding="utf-8")
ASSEMBLY_REVISIONS = (
    MIGRATIONS / "20260712100000_assembly_revisions.sql"
).read_text(encoding="utf-8")
ASSEMBLY_PART_BINDINGS = (
    MIGRATIONS / "20260712110000_assembly_part_bindings.sql"
).read_text(encoding="utf-8")
ASSEMBLY_PUBLISH_JOBS = (
    MIGRATIONS / "20260712120000_assembly_publish_jobs.sql"
).read_text(encoding="utf-8")


class AssemblyRevisionsMigrationTests(unittest.TestCase):
    def test_all_four_tables_are_created(self):
        self.assertIn("create table public.assemblies", ASSEMBLIES)
        self.assertIn("create table public.assembly_revisions", ASSEMBLY_REVISIONS)
        self.assertIn(
            "create table public.assembly_part_bindings", ASSEMBLY_PART_BINDINGS
        )
        self.assertIn(
            "create table public.assembly_publish_jobs", ASSEMBLY_PUBLISH_JOBS
        )

    def test_head_revision_is_a_denormalized_cache_not_a_foreign_key(self):
        assemblies_block = ASSEMBLIES.split("create table public.assemblies", 1)[1].split(
            "create index assemblies_project_idx", 1
        )[0]
        self.assertIn("head_revision integer not null default 0", assemblies_block)
        self.assertNotIn("references public.assembly_revisions", assemblies_block)

    def test_assembly_revisions_uses_restrict_not_cascade(self):
        revisions_block = ASSEMBLY_REVISIONS.split(
            "create table public.assembly_revisions", 1
        )[1].split("create index assembly_revisions_assembly_idx", 1)[0]
        self.assertIn(
            "assembly_id uuid not null references public.assemblies(id) on delete restrict",
            revisions_block,
        )
        self.assertIn(
            "references public.project_planning_jobs(id) on delete restrict",
            revisions_block,
        )
        self.assertNotIn("on delete cascade", revisions_block)

    def test_revision_and_parent_revision_constraints(self):
        self.assertIn("unique (design_request_id)", ASSEMBLY_REVISIONS)
        self.assertIn("unique (assembly_id, revision)", ASSEMBLY_REVISIONS)
        self.assertIn(
            "check (parent_revision is null or parent_revision < revision)",
            ASSEMBLY_REVISIONS,
        )
        self.assertIn(
            "check ((revision = 1) = (parent_revision is null))", ASSEMBLY_REVISIONS
        )
        self.assertIn(
            "foreign key (assembly_id, parent_revision)\n"
            "    references public.assembly_revisions (assembly_id, revision)",
            ASSEMBLY_REVISIONS,
        )

    def test_definition_digest_and_json_are_typed(self):
        self.assertIn(
            "definition_digest text not null check (definition_digest ~ '^[0-9a-f]{64}$')",
            ASSEMBLY_REVISIONS,
        )
        self.assertIn(
            "definition_json jsonb not null check (jsonb_typeof(definition_json) = 'object')",
            ASSEMBLY_REVISIONS,
        )

    def test_assembly_revisions_is_immutable(self):
        self.assertIn(
            "create function public.reject_assembly_revision_mutation", ASSEMBLY_REVISIONS
        )
        self.assertIn(
            "create trigger assembly_revisions_immutable\n"
            "before update or delete on public.assembly_revisions",
            ASSEMBLY_REVISIONS,
        )
        self.assertIn(
            "revoke update, delete on public.assembly_revisions from service_role",
            ASSEMBLY_REVISIONS,
        )

    def test_assembly_part_bindings_has_one_binding_per_node(self):
        self.assertIn("primary key (assembly_id, node_id)", ASSEMBLY_PART_BINDINGS)

    def test_publish_jobs_table_has_no_lease_columns_and_shares_status_enum(self):
        publish_jobs_block = ASSEMBLY_PUBLISH_JOBS.split(
            "create table public.assembly_publish_jobs", 1
        )[1].split("create index assembly_publish_jobs_queued_idx", 1)[0]
        self.assertIn(
            "check (status in ('queued', 'running', 'completed', 'failed', 'cancelled'))",
            publish_jobs_block,
        )
        self.assertNotIn("worker_id", publish_jobs_block)
        self.assertNotIn("lease_expires_at", publish_jobs_block)
        self.assertIn(
            "check (status <> 'completed' or assembly_revision is not null)",
            publish_jobs_block,
        )

    def test_claim_function_uses_skip_locked_and_is_service_role_only(self):
        self.assertIn(
            "create function public.claim_next_assembly_publish_job", ASSEMBLY_PUBLISH_JOBS
        )
        self.assertIn("for update skip locked", ASSEMBLY_PUBLISH_JOBS)
        self.assertIn(
            "revoke all on function public.claim_next_assembly_publish_job()\n"
            "  from public, anon, authenticated;",
            ASSEMBLY_PUBLISH_JOBS,
        )
        self.assertIn(
            "grant execute on function public.claim_next_assembly_publish_job()\n"
            "  to service_role;",
            ASSEMBLY_PUBLISH_JOBS,
        )

    def test_publish_function_is_service_role_only(self):
        self.assertIn(
            "create function public.publish_assembly_revision", ASSEMBLY_REVISIONS
        )
        self.assertIn(
            "grant execute on function public.publish_assembly_revision"
            "(uuid, uuid, uuid, integer, text, jsonb)\n  to service_role;",
            ASSEMBLY_REVISIONS,
        )

    def test_row_level_security_enabled_on_every_new_table_with_no_policies(self):
        for table, migration in (
            ("assemblies", ASSEMBLIES),
            ("assembly_revisions", ASSEMBLY_REVISIONS),
            ("assembly_part_bindings", ASSEMBLY_PART_BINDINGS),
            ("assembly_publish_jobs", ASSEMBLY_PUBLISH_JOBS),
        ):
            self.assertIn(
                f"alter table public.{table} enable row level security;", migration
            )
        for migration in (
            ASSEMBLIES,
            ASSEMBLY_REVISIONS,
            ASSEMBLY_PART_BINDINGS,
            ASSEMBLY_PUBLISH_JOBS,
        ):
            self.assertNotIn("create policy", migration)


if __name__ == "__main__":
    unittest.main()
