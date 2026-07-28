from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260727000000_add_cad_agent_progress_and_tools.sql"
).read_text(encoding="utf-8")


class CadAgentMigrationContractTests(unittest.TestCase):
    def test_submission_is_idempotent_and_creates_the_first_progress_event(self):
        self.assertIn("create unique index edit_jobs_client_request_id_idx", MIGRATION)
        self.assertIn("create function public.submit_cad_edit_job", MIGRATION)
        self.assertIn("CLIENT_REQUEST_ID_CONFLICT", MIGRATION)
        self.assertIn("'job.queued'", MIGRATION)
        self.assertIn("last_event_sequence", MIGRATION)

    def test_progress_events_are_ordered_and_replayable(self):
        self.assertIn("create table public.edit_job_events", MIGRATION)
        self.assertIn("unique (edit_job_id, sequence)", MIGRATION)
        self.assertIn("edit_job_events_replay_idx", MIGRATION)
        self.assertIn("create function public.append_edit_job_event", MIGRATION)

    def test_tool_queue_is_typed_leased_and_service_role_only(self):
        self.assertIn("create table public.cad_tool_jobs", MIGRATION)
        self.assertIn("'prepare_context'", MIGRATION)
        self.assertIn("'apply_plan'", MIGRATION)
        self.assertIn("'prepare_repair_context'", MIGRATION)
        self.assertIn("unique (edit_job_id, attempt, kind)", MIGRATION)
        self.assertIn("for update skip locked", MIGRATION)
        self.assertIn("create function public.heartbeat_cad_tool_job", MIGRATION)
        self.assertIn("enable row level security", MIGRATION)
        self.assertIn("to service_role", MIGRATION)
        self.assertIn("from public, anon, authenticated", MIGRATION)


if __name__ == "__main__":
    unittest.main()
