from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
PROJECT_PLANNING_JOBS = (
    MIGRATIONS / "20260712080000_project_planning_jobs.sql"
).read_text(encoding="utf-8")
ASSEMBLIES = (MIGRATIONS / "20260712090000_assemblies.sql").read_text(encoding="utf-8")


class ProjectPlanningAutoPublishMigrationTests(unittest.TestCase):
    def test_adds_auto_publish_boolean_defaulting_false(self):
        self.assertIn(
            "auto_publish boolean not null default false", PROJECT_PLANNING_JOBS
        )

    def test_adds_target_assembly_id_with_set_null_fk(self):
        # target_assembly_id references public.assemblies, which does not
        # exist yet when project_planning_jobs is created, so it is added
        # here as an alter once assemblies exists.
        self.assertIn(
            "alter table public.project_planning_jobs\n"
            "  add column target_assembly_id uuid "
            "references public.assemblies(id) on delete set null;",
            ASSEMBLIES,
        )
        self.assertNotIn(
            "target_assembly_id uuid references public.assemblies(id) on delete restrict",
            ASSEMBLIES,
        )
        self.assertNotIn(
            "target_assembly_id uuid references public.assemblies(id) on delete cascade",
            ASSEMBLIES,
        )


if __name__ == "__main__":
    unittest.main()
