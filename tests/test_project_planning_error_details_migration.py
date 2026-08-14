from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260712080000_project_planning_jobs.sql"
).read_text(encoding="utf-8")


class ProjectPlanningErrorDetailsMigrationTests(unittest.TestCase):
    def test_has_a_nullable_typed_jsonb_column(self):
        self.assertIn("error_details jsonb", MIGRATION)
        self.assertIn(
            "check (error_details is null or jsonb_typeof(error_details) = 'object')",
            MIGRATION,
        )


if __name__ == "__main__":
    unittest.main()
