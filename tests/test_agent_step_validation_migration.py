from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
MIGRATION = (
    MIGRATIONS / "20260712040000_edit_jobs.sql"
).read_text(encoding="utf-8")


class AgentStepValidationMigrationTests(unittest.TestCase):
    def test_validation_runs_are_counted_separately_from_commit_attempts(self):
        self.assertIn("validation_run_count integer not null default 0", MIGRATION)
        self.assertIn("check (validation_run_count >= 0)", MIGRATION)
        # The whole point of the new column: attempt_count is capped at 3 by
        # a table CHECK, which cannot express "one validation per plan step".
        # That cap is unwidened -- validation_run_count is a separate,
        # uncapped counter, not a replacement for attempt_count's cap.
        self.assertIn("check (attempt_count between 0 and 3)", MIGRATION)

    def test_the_run_counter_is_rpc_owned_rather_than_patchable(self):
        # patch_edit_job_owned's allowlist deliberately does not include
        # validation_run_count: the only race-free place to allocate a run
        # number is inside the RPC that already holds `for update` on the
        # edit row.
        allowlist = MIGRATION.split(
            "create function public.patch_edit_job_owned", 1
        )[1].split("]::text[]);", 1)[0]
        self.assertNotIn("validation_run_count", allowlist)
        self.assertIn("validation_run_count = p_validation_run", MIGRATION)

    def test_the_candidate_path_is_scoped_to_the_validation_run(self):
        self.assertIn("'/validation-'", MIGRATION)
        self.assertIn("p_validation_run::text", MIGRATION)
        self.assertIn(
            "Candidate source path does not belong to the validation run.",
            MIGRATION,
        )

    def test_run_allocation_is_monotonic_and_uncapped(self):
        self.assertIn("p_validation_run < edit_job.validation_run_count", MIGRATION)
        self.assertIn(
            "p_validation_run > edit_job.validation_run_count + 1", MIGRATION
        )
        self.assertIn("Candidate validation run is out of sequence.", MIGRATION)
        # No max_attempts ceiling inside the new run-based queueing function:
        # a plan may legitimately validate once per step plus a bounded
        # number of repairs. (max_attempts still appears elsewhere in this
        # file -- it is the column definition and the older attempt-based
        # RPC, both of which remain in place.)
        run_function = MIGRATION.split(
            "create function public.queue_edit_candidate_validation_run(", 1
        )[1].split("create function public.queue_edit_candidate_validation_run_owned", 1)[0]
        self.assertNotIn("max_attempts", run_function)

    def test_idempotent_reuse_is_scoped_to_this_job_and_this_content(self):
        for predicate in (
            "validation_job.type = 'validate_cad'",
            "validation_job.source_kind = 'candidate'",
            "validation_job.edit_job_id = edit_job.id",
            "validation_job.source_storage_path = p_candidate_path",
            "validation_job.source_sha256 = p_candidate_sha256",
        ):
            self.assertIn(predicate, MIGRATION)

    def test_queueing_requires_the_active_lease_owner(self):
        self.assertIn(
            "create function public.queue_edit_candidate_validation_run_owned",
            MIGRATION,
        )
        for predicate in (
            "owned_job.status <> 'running'",
            "owned_job.worker_id <> btrim(p_worker_id)",
            "owned_job.lease_expires_at is null",
            "owned_job.lease_expires_at < now()",
        ):
            self.assertIn(predicate, MIGRATION)
        self.assertIn("EDIT_LEASE_LOST", MIGRATION)

    def test_only_the_lease_owning_wrapper_is_callable_by_service_role(self):
        self.assertIn(
            "revoke all on function public.queue_edit_candidate_validation_run(",
            MIGRATION,
        )
        self.assertIn(
            "grant execute on function "
            "public.queue_edit_candidate_validation_run_owned(",
            MIGRATION,
        )
        # The inner function is never granted: the wrapper reaches it as its
        # definer, the same arrangement used for every other queue_edit_*
        # child RPC in this file.
        self.assertNotIn(
            "grant execute on function public.queue_edit_candidate_validation_run(",
            MIGRATION,
        )
        # And it is explicitly locked out of the local Supabase default ACL
        # that would otherwise hand service_role EXECUTE automatically.
        self.assertIn(
            "revoke execute on function public.queue_edit_candidate_validation_run(",
            MIGRATION,
        )


if __name__ == "__main__":
    unittest.main()
