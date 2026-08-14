from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
EDIT_JOBS = (MIGRATIONS / "20260712040000_edit_jobs.sql").read_text(encoding="utf-8")
EDIT_JOB_EVENTS = (
    MIGRATIONS / "20260712060000_edit_job_events.sql"
).read_text(encoding="utf-8")
ALL_MIGRATIONS_TEXT = "\n".join(
    path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
)


class CadEditorCutoverMigrationTests(unittest.TestCase):
    def test_reasoning_states_and_events_are_part_of_the_durable_job(self):
        self.assertIn("'creating_goal'", EDIT_JOBS)
        self.assertIn("'planning_goal'", EDIT_JOBS)
        self.assertIn("'goal.completed'", EDIT_JOB_EVENTS)
        self.assertIn("'plan.completed'", EDIT_JOB_EVENTS)

    def test_history_and_progress_appends_require_the_active_lease_owner(self):
        self.assertIn("append_edit_job_history_owned", EDIT_JOBS)
        self.assertGreaterEqual(EDIT_JOBS.count("worker_id = btrim(p_worker_id)"), 2)
        self.assertGreaterEqual(EDIT_JOBS.count("lease_expires_at >= now()"), 2)
        self.assertIn("EDIT_LEASE_LOST", EDIT_JOBS)
        self.assertIn("to service_role", EDIT_JOBS)

        self.assertIn("append_edit_job_event_owned", EDIT_JOB_EVENTS)
        self.assertIn("worker_id = btrim(p_worker_id)", EDIT_JOB_EVENTS)
        self.assertIn("lease_expires_at >= now()", EDIT_JOB_EVENTS)
        self.assertIn("EDIT_LEASE_LOST", EDIT_JOB_EVENTS)
        self.assertIn("to service_role", EDIT_JOB_EVENTS)

    def test_all_editor_mutations_use_database_checked_lease_ownership(self):
        self.assertIn("create function public.patch_edit_job_owned", EDIT_JOBS)
        self.assertIn("create function public.heartbeat_edit_job", EDIT_JOBS)
        self.assertIn("queue_edit_index_build_owned", EDIT_JOBS)
        self.assertIn("queue_edit_candidate_validation_owned", EDIT_JOBS)
        self.assertIn("queue_edit_export_owned", EDIT_JOBS)
        self.assertGreaterEqual(EDIT_JOBS.count("owned_job.lease_expires_at < now()"), 4)
        self.assertIn("Unsupported CAD editor patch keys", EDIT_JOBS)

    def test_heartbeat_cannot_be_revived_by_a_stale_lease_owner(self):
        # An expired lease cannot be revived by its former owner -- this is
        # the final, tightened heartbeat_edit_job (superseding the looser
        # version that shipped before any production database existed).
        heartbeat = EDIT_JOBS.split(
            "create function public.heartbeat_edit_job", 1
        )[1].split("create function public.patch_edit_job_owned", 1)[0]
        self.assertIn("and lease_expires_at is not null", heartbeat)
        self.assertIn("and lease_expires_at >= now()", heartbeat)

    def test_linked_parts_are_reserved_before_replicas_spend_model_calls(self):
        self.assertIn("create function public.reserve_requested_cad_edit_part", EDIT_JOBS)
        self.assertIn("pg_advisory_xact_lock", EDIT_JOBS)
        self.assertIn("new.resolved_part_id := new.requested_part_id", EDIT_JOBS)
        self.assertIn("PART_EDIT_IN_PROGRESS", EDIT_JOBS)

    def test_terminal_event_and_row_are_finalized_atomically(self):
        self.assertIn("create function public.finalize_edit_job_owned", EDIT_JOBS)
        finalize = EDIT_JOBS.split(
            "create function public.finalize_edit_job_owned", 1
        )[1].split("revoke all on function public.reserve_requested_cad_edit_part()", 1)[0]
        self.assertIn("insert into public.edit_job_events", finalize)
        self.assertIn("update public.edit_jobs", finalize)
        self.assertIn("last_event_sequence = next_sequence", finalize)
        self.assertIn("lease_expires_at = null", finalize)

    def test_progress_batching_uses_a_bounded_cursor_per_job(self):
        self.assertIn("create function public.edit_job_events_after_cursors", EDIT_JOB_EVENTS)
        self.assertIn("partition by event.edit_job_id", EDIT_JOB_EVENTS)
        self.assertIn("event.sequence > cursor.after_sequence", EDIT_JOB_EVENTS)
        self.assertIn("p_limit_per_job", EDIT_JOB_EVENTS)

    def test_the_nested_tool_queue_and_the_ungoverned_event_rpc_never_exist(self):
        # cad_tool_jobs (and its five owning functions) and the plain,
        # non-lease-checked append_edit_job_event were both created and then
        # fully dropped again before any production database existed. The
        # squashed migration history therefore never introduces them at
        # all -- they must not appear anywhere.
        for needle in (
            "cad_tool_jobs",
            "queue_cad_tool_job",
            "claim_next_cad_tool_job",
            "heartbeat_cad_tool_job",
            "complete_cad_tool_job",
            "fail_cad_tool_job",
        ):
            self.assertNotIn(needle, ALL_MIGRATIONS_TEXT)

        # append_edit_job_event must not exist as its own function
        # definition or grant/revoke target -- only the "_owned" variant
        # survives. A plain substring check would false-positive on
        # append_edit_job_event_owned (which contains it as a prefix), so
        # check the exact statement forms instead.
        self.assertNotIn(
            "create function public.append_edit_job_event(", ALL_MIGRATIONS_TEXT
        )
        self.assertNotIn(
            "function public.append_edit_job_event(\n  uuid, text, text, text, jsonb\n)",
            ALL_MIGRATIONS_TEXT,
        )
        self.assertIn(
            "create function public.append_edit_job_event_owned", ALL_MIGRATIONS_TEXT
        )


if __name__ == "__main__":
    unittest.main()
