from __future__ import annotations

import unittest
from types import SimpleNamespace

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.repository import SupabaseEditRepository


class SupabaseEditRepositoryTests(unittest.TestCase):
    def test_patch_edit_job_uses_the_update_builder_without_single(self):
        calls = []

        class UpdateBuilder:
            def update(self, values):
                calls.append(("update", values))
                return self

            def eq(self, column, value):
                calls.append(("eq", column, value))
                return self

            def select(self, columns):
                calls.append(("select", columns))
                return self

            def execute(self):
                calls.append(("execute",))
                return SimpleNamespace(
                    data=[{"id": "edit-a", "state": "resolving_target"}]
                )

        class Supabase:
            def table(self, name):
                calls.append(("table", name))
                return UpdateBuilder()

        repository = SupabaseEditRepository(Supabase())

        updated = repository.patch_edit_job(
            "edit-a",
            {"state": "resolving_target"},
        )

        self.assertEqual(
            updated,
            {"id": "edit-a", "state": "resolving_target"},
        )
        self.assertEqual(
            calls,
            [
                ("table", "edit_jobs"),
                ("update", {"state": "resolving_target"}),
                ("eq", "id", "edit-a"),
                ("select", "*"),
                ("execute",),
            ],
        )

    def test_patch_edit_job_rejects_an_update_that_returns_no_row(self):
        class UpdateBuilder:
            def update(self, _values):
                return self

            def eq(self, _column, _value):
                return self

            def select(self, _columns):
                return self

            def execute(self):
                return SimpleNamespace(data=[])

        supabase = SimpleNamespace(table=lambda _name: UpdateBuilder())
        repository = SupabaseEditRepository(supabase)

        with self.assertRaisesRegex(RuntimeError, "could not be updated"):
            repository.patch_edit_job("missing-edit", {"state": "failed"})

    def test_generation_job_selects_by_id(self):
        calls = []

        class SelectBuilder:
            def select(self, columns):
                calls.append(("select", columns))
                return self

            def eq(self, column, value):
                calls.append(("eq", column, value))
                return self

            def single(self):
                calls.append(("single",))
                return self

            def execute(self):
                calls.append(("execute",))
                return SimpleNamespace(data={"id": "validation-1", "status": "completed"})

        supabase = SimpleNamespace(table=lambda name: (calls.append(("table", name)), SelectBuilder())[1])
        repository = SupabaseEditRepository(supabase)

        result = repository.generation_job("validation-1")

        self.assertEqual(result, {"id": "validation-1", "status": "completed"})
        self.assertEqual(
            calls,
            [
                ("table", "generation_jobs"),
                ("select", "*"),
                ("eq", "id", "validation-1"),
                ("single",),
                ("execute",),
            ],
        )

    def test_queue_validation_run_calls_the_owned_rpc_with_all_arguments(self):
        calls = []

        class RpcBuilder:
            def execute(self):
                return SimpleNamespace(data="validation-7")

        def rpc(name, params):
            calls.append((name, params))
            return RpcBuilder()

        supabase = SimpleNamespace(rpc=rpc)
        repository = SupabaseEditRepository(supabase)

        result = repository.queue_validation_run(
            "edit-a",
            "project/candidates/cad/part/edit-a/validation-3/model.py",
            "b" * 64,
            3,
            worker_id="worker-1",
        )

        self.assertEqual(result, "validation-7")
        self.assertEqual(
            calls,
            [
                (
                    "queue_edit_candidate_validation_run_owned",
                    {
                        "p_edit_job_id": "edit-a",
                        "p_worker_id": "worker-1",
                        "p_candidate_path": (
                            "project/candidates/cad/part/edit-a/validation-3/model.py"
                        ),
                        "p_candidate_sha256": "b" * 64,
                        "p_validation_run": 3,
                    },
                )
            ],
        )

    def test_queue_validation_run_translates_a_lost_lease(self):
        class RpcBuilder:
            def execute(self):
                raise RuntimeError("EDIT_LEASE_LOST: gone")

        supabase = SimpleNamespace(rpc=lambda _name, _params: RpcBuilder())
        repository = SupabaseEditRepository(supabase)

        with self.assertRaisesRegex(WorkflowFailure, "no longer owns"):
            repository.queue_validation_run(
                "edit-a", "path", "b" * 64, 1, worker_id="worker-1"
            )

    def test_queue_export_calls_the_owned_rpc_with_all_arguments(self):
        calls = []

        class RpcBuilder:
            def execute(self):
                return SimpleNamespace(data="export-1")

        def rpc(name, params):
            calls.append((name, params))
            return RpcBuilder()

        supabase = SimpleNamespace(rpc=rpc)
        repository = SupabaseEditRepository(supabase)

        result = repository.queue_export("edit-a", "a" * 64, worker_id="worker-1")

        self.assertEqual(result, "export-1")
        self.assertEqual(
            calls,
            [
                (
                    "queue_edit_export_owned",
                    {
                        "p_edit_job_id": "edit-a",
                        "p_worker_id": "worker-1",
                        "p_source_sha256": "a" * 64,
                    },
                )
            ],
        )

    def test_queue_export_translates_a_lost_lease(self):
        class RpcBuilder:
            def execute(self):
                raise RuntimeError("EDIT_LEASE_LOST: gone")

        supabase = SimpleNamespace(rpc=lambda _name, _params: RpcBuilder())
        repository = SupabaseEditRepository(supabase)

        with self.assertRaisesRegex(WorkflowFailure, "no longer owns"):
            repository.queue_export("edit-a", "a" * 64, worker_id="worker-1")

    def test_queue_geometry_check_calls_the_rpc_with_no_worker_identity(self):
        calls = []

        class RpcBuilder:
            def execute(self):
                return SimpleNamespace(data="geometry-check-1")

        def rpc(name, params):
            calls.append((name, params))
            return RpcBuilder()

        supabase = SimpleNamespace(rpc=rpc)
        repository = SupabaseEditRepository(supabase)

        result = repository.queue_geometry_check("edit-a", "a" * 64)

        self.assertEqual(result, "geometry-check-1")
        self.assertEqual(
            calls,
            [
                (
                    "queue_geometry_check",
                    {
                        "p_edit_job_id": "edit-a",
                        "p_candidate_sha256": "a" * 64,
                    },
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
