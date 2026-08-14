from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
EDIT_JOBS = (MIGRATIONS / "20260712040000_edit_jobs.sql").read_text(encoding="utf-8")
EDIT_JOB_EVENTS = (
    MIGRATIONS / "20260712060000_edit_job_events.sql"
).read_text(encoding="utf-8")


class CadAgentMigrationContractTests(unittest.TestCase):
    def test_submission_is_idempotent_and_creates_the_first_progress_event(self):
        self.assertIn("create unique index edit_jobs_client_request_id_idx", EDIT_JOBS)
        self.assertIn("create function public.submit_cad_edit_job", EDIT_JOBS)
        self.assertIn("CLIENT_REQUEST_ID_CONFLICT", EDIT_JOBS)
        self.assertIn("'job.queued'", EDIT_JOBS)
        self.assertIn("last_event_sequence", EDIT_JOBS)

    def test_progress_events_are_ordered_and_replayable(self):
        self.assertIn("create table public.edit_job_events", EDIT_JOB_EVENTS)
        self.assertIn("unique (edit_job_id, sequence)", EDIT_JOB_EVENTS)
        self.assertIn("edit_job_events_replay_idx", EDIT_JOB_EVENTS)
        # append_edit_job_event (the plain, non-lease-checked RPC this
        # migration originally introduced) does not exist in the final
        # schema -- it was superseded by append_edit_job_event_owned before
        # any production database existed. See
        # test_cad_editor_cutover_migration.py for the assertion that it
        # never appears anywhere in supabase/migrations/.
        self.assertIn("create function public.append_edit_job_event_owned", EDIT_JOB_EVENTS)

    # The nested Nest-to-Python cad_tool_jobs execution queue this migration
    # originally introduced never exists in the final schema either -- it was
    # created and fully dropped again before any production database
    # existed. See test_cad_editor_cutover_migration.py for the assertion
    # that it never appears anywhere in supabase/migrations/.


if __name__ == "__main__":
    unittest.main()
