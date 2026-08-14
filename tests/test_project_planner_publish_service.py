from __future__ import annotations

import unittest
from uuid import uuid4

from workers.project_planner.project_planner.contracts import (
    AssemblySpec,
    ProjectPlan,
    ProjectPlanningLimits,
)
from workers.project_planner.project_planner.failures import ProjectPlanningFailure
from workers.project_planner.project_planner.publish_service import (
    process_assembly_publish_job,
    run_auto_publish,
)


def _plan_payload(**overrides) -> dict:
    payload = {
        "plan_id": str(uuid4()),
        "schema_version": 1,
        "summary": "A test assembly.",
        "design_mode": "single_part",
        "requirements": [{"ref": "r1", "description": "d"}],
        "parts": [
            {
                "ref": "a",
                "name": "A",
                "kind": "new",
                "existing_part_id": None,
                "purpose": "p",
                "responsibilities": [],
                "addresses_requirements": ["r1"],
            }
        ],
        "interfaces": [],
        "execution_dependencies": [],
        "assumptions": [],
        "clarification": {"required": False, "question": None, "reason": None},
    }
    payload.update(overrides)
    return payload


def _spec_payload(node_id: str, **overrides) -> dict:
    payload = {
        "spec_id": str(uuid4()),
        "schema_version": 1,
        "project_id": "proj-1",
        "summary": "A test assembly.",
        "requirements": [{"ref": "r1", "description": "d"}],
        "nodes": [
            {
                "node_id": node_id,
                "semantic_ref": "a",
                "name": "A",
                "binding": {"mode": "create", "part_id": None},
                "purpose": "p",
                "responsibilities": [],
                "addresses_requirements": ["r1"],
            }
        ],
        "interfaces": [],
        "execution_dependencies": [],
        "assumptions": [],
    }
    payload.update(overrides)
    return payload


class FakeProjectsRepository:
    def list_cad_sources(self, project_id: str):
        return []  # brand-new/empty project -- existing_parts resolves to []


class FakeRepository:
    def __init__(self, *, planning_job: dict | None = None):
        self.projects = FakeProjectsRepository()
        self._planning_job = planning_job
        self.publish_calls: list[dict] = []
        self.created_publish_jobs: list[dict] = []
        self.completed_publish_jobs: list[dict] = []
        self.failed_publish_jobs: list[dict] = []

    def get_completed_planning_job(self, project_id: str, design_request_id: str) -> dict:
        if self._planning_job is None:
            raise ProjectPlanningFailure(
                "ASSEMBLY_PUBLISH_DESIGN_REQUEST_NOT_FOUND",
                f'No project planning job was found with id "{design_request_id}".',
            )
        return self._planning_job

    def publish_revision(self, **kwargs) -> dict:
        self.publish_calls.append(kwargs)
        return {
            "id": str(uuid4()),
            "assembly_id": kwargs["assembly_id"] or str(uuid4()),
            "revision": 1,
            "parent_revision": None,
            "design_request_id": kwargs["design_request_id"],
            "schema_version": kwargs["schema_version"],
            "definition_digest": kwargs["definition_digest"],
            "created_at": "2026-01-01T00:00:00Z",
        }

    def create_running_publish_job(
        self, *, project_id: str, design_request_id: str, target_assembly_id: str | None
    ) -> dict:
        job = {
            "id": str(uuid4()),
            "project_id": project_id,
            "design_request_id": design_request_id,
            "target_assembly_id": target_assembly_id,
            "status": "running",
        }
        self.created_publish_jobs.append(job)
        return job

    def complete_publish_job(self, job_id: str, *, assembly_revision: dict) -> None:
        self.completed_publish_jobs.append(
            {"job_id": job_id, "assembly_revision": assembly_revision}
        )

    def fail_publish_job(
        self, job_id: str, *, code: str, message: str, error_details: dict | None = None
    ) -> None:
        self.failed_publish_jobs.append(
            {"job_id": job_id, "code": code, "message": message, "error_details": error_details}
        )


def _limits() -> ProjectPlanningLimits:
    return ProjectPlanningLimits(max_parts=12, max_interfaces=24)


class ProcessAssemblyPublishJobTests(unittest.TestCase):
    def test_publishes_a_valid_plan(self):
        node_id = str(uuid4())
        design_request_id = str(uuid4())
        repository = FakeRepository(
            planning_job={
                "id": design_request_id,
                "project_id": "proj-1",
                "status": "completed",
                "project_plan": _plan_payload(),
                "assembly_spec": _spec_payload(node_id),
            }
        )
        job = {"project_id": "proj-1", "design_request_id": design_request_id, "target_assembly_id": None}

        result = process_assembly_publish_job(repository, job, limits=_limits())

        self.assertTrue(result["assembly_revision"]["revision_id"])
        self.assertEqual(len(repository.publish_calls), 1)
        call = repository.publish_calls[0]
        self.assertEqual(call["schema_version"], 1)
        self.assertTrue(call["definition_digest"])

    def test_reuses_stored_assembly_spec_node_ids_without_rebuilding(self):
        node_id = str(uuid4())
        design_request_id = str(uuid4())
        repository = FakeRepository(
            planning_job={
                "id": design_request_id,
                "project_id": "proj-1",
                "status": "completed",
                "project_plan": _plan_payload(),
                "assembly_spec": _spec_payload(node_id),
            }
        )
        job = {"project_id": "proj-1", "design_request_id": design_request_id, "target_assembly_id": None}

        process_assembly_publish_job(repository, job, limits=_limits())

        published_definition = repository.publish_calls[0]["definition_json"]
        self.assertEqual(published_definition["nodes"][0]["node_id"], node_id)

    def test_rejects_a_plan_with_a_dependency_cycle(self):
        node_id = str(uuid4())
        design_request_id = str(uuid4())
        cyclic_plan = _plan_payload(
            execution_dependencies=[
                {"prerequisite_part_ref": "a", "dependent_part_ref": "a", "reason": "r"}
            ]
        )
        repository = FakeRepository(
            planning_job={
                "id": design_request_id,
                "project_id": "proj-1",
                "status": "completed",
                "project_plan": cyclic_plan,
                "assembly_spec": _spec_payload(node_id),
            }
        )
        job = {"project_id": "proj-1", "design_request_id": design_request_id, "target_assembly_id": None}

        with self.assertRaises(ProjectPlanningFailure) as ctx:
            process_assembly_publish_job(repository, job, limits=_limits())

        self.assertEqual(ctx.exception.code, "ASSEMBLY_PUBLISH_PLAN_INVALID")
        self.assertEqual(len(repository.publish_calls), 0)
        violation_codes = {v["code"] for v in ctx.exception.details["violations"]}
        self.assertIn("PROJECT_PLAN_EXECUTION_DEPENDENCY_CYCLE", violation_codes)

    def test_propagates_design_request_not_completed_failure(self):
        repository = FakeRepository(planning_job=None)
        job = {
            "project_id": "proj-1",
            "design_request_id": str(uuid4()),
            "target_assembly_id": None,
        }

        with self.assertRaises(ProjectPlanningFailure) as ctx:
            process_assembly_publish_job(repository, job, limits=_limits())

        self.assertEqual(ctx.exception.code, "ASSEMBLY_PUBLISH_DESIGN_REQUEST_NOT_FOUND")
        self.assertEqual(len(repository.publish_calls), 0)


class RunAutoPublishTests(unittest.TestCase):
    """run_auto_publish is the inline auto_publish=true path: plan/spec are
    already in memory (no get_completed_planning_job fetch), but it must
    still create + resolve its own assembly_publish_jobs row so auto and
    explicit publishes are indistinguishable in the publish history."""

    def test_publishes_and_completes_a_running_job_row(self):
        node_id = str(uuid4())
        design_request_id = str(uuid4())
        repository = FakeRepository()
        plan = ProjectPlan.model_validate(_plan_payload())
        spec = AssemblySpec.model_validate(_spec_payload(node_id))

        result = run_auto_publish(
            repository,
            project_id="proj-1",
            design_request_id=design_request_id,
            target_assembly_id=None,
            plan=plan,
            spec=spec,
            limits=_limits(),
        )

        self.assertTrue(result["assembly_revision"]["revision_id"])
        self.assertEqual(len(repository.created_publish_jobs), 1)
        self.assertEqual(repository.created_publish_jobs[0]["design_request_id"], design_request_id)
        self.assertEqual(len(repository.completed_publish_jobs), 1)
        self.assertEqual(len(repository.failed_publish_jobs), 0)
        self.assertEqual(
            repository.completed_publish_jobs[0]["job_id"],
            repository.created_publish_jobs[0]["id"],
        )

    def test_invalid_plan_fails_the_created_job_row_and_reraises(self):
        node_id = str(uuid4())
        design_request_id = str(uuid4())
        repository = FakeRepository()
        cyclic_plan = ProjectPlan.model_validate(
            _plan_payload(
                execution_dependencies=[
                    {"prerequisite_part_ref": "a", "dependent_part_ref": "a", "reason": "r"}
                ]
            )
        )
        spec = AssemblySpec.model_validate(_spec_payload(node_id))

        with self.assertRaises(ProjectPlanningFailure) as ctx:
            run_auto_publish(
                repository,
                project_id="proj-1",
                design_request_id=design_request_id,
                target_assembly_id=None,
                plan=cyclic_plan,
                spec=spec,
                limits=_limits(),
            )

        self.assertEqual(ctx.exception.code, "ASSEMBLY_PUBLISH_PLAN_INVALID")
        self.assertEqual(len(repository.created_publish_jobs), 1)
        self.assertEqual(len(repository.completed_publish_jobs), 0)
        self.assertEqual(len(repository.failed_publish_jobs), 1)
        self.assertEqual(repository.failed_publish_jobs[0]["code"], "ASSEMBLY_PUBLISH_PLAN_INVALID")
        self.assertEqual(
            repository.failed_publish_jobs[0]["job_id"],
            repository.created_publish_jobs[0]["id"],
        )


if __name__ == "__main__":
    unittest.main()
