from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260802000000_move_cad_runtime_to_editor.sql"
).read_text(encoding="utf-8")


class CadEditorCutoverMigrationTests(unittest.TestCase):
    def test_reasoning_states_and_events_are_part_of_the_durable_job(self):
        self.assertIn("'creating_goal'", MIGRATION)
        self.assertIn("'planning_goal'", MIGRATION)
        self.assertIn("'goal.completed'", MIGRATION)
        self.assertIn("'plan.completed'", MIGRATION)

    def test_history_and_progress_appends_require_the_active_lease_owner(self):
        self.assertIn("append_edit_job_history_owned", MIGRATION)
        self.assertIn("append_edit_job_event_owned", MIGRATION)
        self.assertGreaterEqual(MIGRATION.count("worker_id = btrim(p_worker_id)"), 2)
        self.assertGreaterEqual(MIGRATION.count("lease_expires_at >= now()"), 2)
        self.assertIn("EDIT_LEASE_LOST", MIGRATION)
        self.assertIn("to service_role", MIGRATION)

    def test_all_editor_mutations_use_database_checked_lease_ownership(self):
        self.assertIn("create function public.patch_edit_job_owned", MIGRATION)
        self.assertIn("create or replace function public.heartbeat_edit_job", MIGRATION)
        self.assertIn("queue_edit_index_build_owned", MIGRATION)
        self.assertIn("queue_edit_candidate_validation_owned", MIGRATION)
        self.assertIn("queue_edit_export_owned", MIGRATION)
        self.assertGreaterEqual(MIGRATION.count("owned_job.lease_expires_at < now()"), 4)
        self.assertIn("Unsupported CAD editor patch keys", MIGRATION)

    def test_linked_parts_are_reserved_before_replicas_spend_model_calls(self):
        self.assertIn("create function public.reserve_requested_cad_edit_part", MIGRATION)
        self.assertIn("pg_advisory_xact_lock", MIGRATION)
        self.assertIn("new.resolved_part_id := new.requested_part_id", MIGRATION)
        self.assertIn("PART_EDIT_IN_PROGRESS", MIGRATION)

    def test_terminal_event_and_row_are_finalized_atomically(self):
        self.assertIn("create function public.finalize_edit_job_owned", MIGRATION)
        finalize = MIGRATION.split(
            "create function public.finalize_edit_job_owned", 1
        )[1].split("comment on table", 1)[0]
        self.assertIn("insert into public.edit_job_events", finalize)
        self.assertIn("update public.edit_jobs", finalize)
        self.assertIn("last_event_sequence = next_sequence", finalize)
        self.assertIn("lease_expires_at = null", finalize)

    def test_progress_batching_uses_a_bounded_cursor_per_job(self):
        self.assertIn("create function public.edit_job_events_after_cursors", MIGRATION)
        self.assertIn("partition by event.edit_job_id", MIGRATION)
        self.assertIn("event.sequence > cursor.after_sequence", MIGRATION)
        self.assertIn("p_limit_per_job", MIGRATION)

    def test_cutover_requires_a_drain_and_removes_the_nested_tool_queue(self):
        self.assertIn("CAD_EDITOR_CUTOVER_REQUIRES_DRAIN", MIGRATION)
        self.assertIn("edit_jobs where status in ('queued', 'running')", MIGRATION)
        self.assertIn("cad_tool_jobs where status in ('queued', 'running')", MIGRATION)
        self.assertIn("drop function if exists public.claim_next_cad_tool_job", MIGRATION)
        self.assertIn("drop table public.cad_tool_jobs", MIGRATION)


if __name__ == "__main__":
    unittest.main()
