from __future__ import annotations

import copy
import hashlib
import unittest

from tests.test_cad_editor_core import MODEL_SOURCE, PART_ID, plan
from workers.cad_editor.cad_editor.applier import apply_edit_plan
from workers.cad_editor.cad_editor.contracts import InitialCadModel
from workers.cad_editor.cad_editor.orchestrator import EditWorkflowOrchestrator
from workers.cad_editor.cad_editor.targets import source_hash
from workers.indexer.indexer import IndexGetter, SourceFile, build_project_index
from workers.indexer.indexer.repository import is_uninitialized_cad_source


PROJECT_ID = "project-a"
EDIT_JOB_ID = "edit-a"
CANONICAL_PATH = f"{PROJECT_ID}/parts/cad/{PART_ID}/model.py"


def passed_validation() -> dict:
    return {
        "schema_version": 2,
        "status": "passed",
        "stage": "completed",
        "repairable_hint": False,
        "diagnostics": [],
        "build_artifacts": {
            "solid_count": 1,
            "result_type": "cadquery.Workplane",
        },
        "valid": True,
        "checks": {},
        "runtime": {"passed": True, "skipped": False, "errors": []},
    }


def repairable_validation(message: str = "Geometry construction failed.") -> dict:
    return {
        "schema_version": 2,
        "status": "failed",
        "stage": "geometry",
        "repairable_hint": True,
        "diagnostics": [
            {
                "error_code": "GEOMETRY_BUILD_ERROR",
                "message": message,
                "stage": "geometry",
                "function_name": "cut_mounting_holes",
                "semantic_id": "mount_holes",
                "related_symbols": ["cut_mounting_holes"],
            }
        ],
        "build_artifacts": None,
        "valid": False,
        "checks": {},
        "runtime": {"passed": False, "skipped": False, "errors": []},
    }


def body_plan(extrusion: int, summary: str = "Update mounting holes"):
    return plan(
        {
            "operation": "replace_function_body",
            "target_id": f"{PART_ID}:function_body:cut_mounting_holes",
            "replacement_source": (
                'return cq.Workplane("XY")'
                f".circle(params.hole_diameter / 2).extrude({extrusion})"
            ),
        }
    ).model_copy(update={"summary": summary})


class FakeAgent:
    def __init__(self, initial_plan, repair_plans=()):
        self.initial_plan = initial_plan
        self.repair_plans = list(repair_plans)
        self.edit_contexts = []
        self.repair_contexts = []
        self.initial_design_contexts = []
        self.initial_repair_contexts = []
        self.initial_models = []

    def extract_search_queries(self, _request):
        raise AssertionError(
            "Exact feature search should not require keyword extraction"
        )

    def select_targets(self, _request, _candidates):
        raise AssertionError("A single exact feature should resolve automatically")

    def create_edit_plan(self, context):
        self.edit_contexts.append(context)
        return self.initial_plan

    def create_repair_plan(self, context):
        self.repair_contexts.append(context)
        if not self.repair_plans:
            raise AssertionError("No repair plan remains")
        return self.repair_plans.pop(0)

    def create_initial_design(self, context):
        self.initial_design_contexts.append(context)
        if not self.initial_models:
            raise AssertionError("No initial model remains")
        return self.initial_models.pop(0)

    def create_initial_repair(self, context):
        self.initial_repair_contexts.append(context)
        if not self.initial_models:
            raise AssertionError("No initial repair model remains")
        return self.initial_models.pop(0)


class FakeRepository:
    def __init__(
        self,
        validation_results,
        *,
        initial_index: bool = True,
        fail_reindex: bool = False,
        race_source: str | None = None,
    ):
        self.storage = {CANONICAL_PATH: MODEL_SOURCE}
        self.index = self._build_index() if initial_index else None
        self.validation_results = list(validation_results)
        self.fail_reindex = fail_reindex
        self.race_source = race_source
        self.source_calls = 0
        self.validation_queue_count = 0
        self.index_queue_count = 0
        self.export_queue_count = 0
        self.cleanup_count = 0
        self.generations = {}
        self.index_jobs = {}
        self.jobs = {
            EDIT_JOB_ID: {
                "id": EDIT_JOB_ID,
                "project_id": PROJECT_ID,
                "request_text": "make the mounting holes deeper",
                "messages": [
                    {
                        "role": "user",
                        "content": "make the mounting holes deeper",
                    }
                ],
                "resolved_part_id": None,
                "resolved_targets": [],
                "status": "running",
                "state": "received",
                "attempt_count": 0,
                "max_attempts": 3,
                "accepted_source_sha256": None,
                "original_storage_path": None,
                "current_candidate_path": None,
                "current_candidate_sha256": None,
                "validation_job_id": None,
                "index_job_id": None,
                "export_job_id": None,
                "history": [],
            }
        }

    def _source_file(self) -> SourceFile:
        return SourceFile.from_content(
            part_id=PART_ID,
            part_name="Left Bracket",
            storage_path=CANONICAL_PATH,
            content=self.storage[CANONICAL_PATH],
        )

    def _build_index(self):
        source = self._source_file()
        sources = [] if is_uninitialized_cad_source(source.content) else [source]
        return build_project_index(PROJECT_ID, "Fixture", sources)

    def edit_job(self, edit_job_id):
        return copy.deepcopy(self.jobs[edit_job_id])

    def patch_edit_job(self, edit_job_id, values):
        self.jobs[edit_job_id].update(copy.deepcopy(values))
        return self.edit_job(edit_job_id)

    def append_history(self, edit_job_id, event):
        self.jobs[edit_job_id]["history"].append(copy.deepcopy(event))
        return self.edit_job(edit_job_id)

    def heartbeat(self, edit_job_id, _worker_id, _lease_seconds):
        if self.jobs[edit_job_id]["status"] != "running":
            raise AssertionError("Heartbeat attempted after terminal state")

    def getter(self, _project_id):
        if self.index is None:
            raise ValueError("missing index")
        source = self._source_file()
        sources = [] if is_uninitialized_cad_source(source.content) else [source]
        return IndexGetter(self.index, sources)

    def source(self, _project_id, _part_id):
        self.source_calls += 1
        if self.race_source is not None and self.source_calls == 1:
            self.storage[CANONICAL_PATH] = self.race_source
        return self._source_file()

    def read_text(self, path):
        return self.storage[path]

    def write_text(self, path, content):
        self.storage[path] = content

    def verify_text_hash(self, path, expected_hash):
        content = self.storage[path]
        if source_hash(content) != expected_hash:
            raise AssertionError("Fake storage hash mismatch")
        return content

    def candidate_path(self, project_id, part_id, edit_job_id, attempt):
        return (
            f"{project_id}/candidates/cad/{part_id}/{edit_job_id}/"
            f"attempt-{attempt}/model.py"
        )

    def original_path(self, project_id, part_id, edit_job_id):
        return (
            f"{project_id}/candidates/cad/{part_id}/{edit_job_id}/" "original/model.py"
        )

    def canonical_source_path(self, _project_id, _part_id):
        return CANONICAL_PATH

    def cleanup_candidates(self, project_id, part_id, edit_job_id, max_attempts=3):
        self.cleanup_count += 1
        paths = [
            self.original_path(project_id, part_id, edit_job_id),
            *[
                self.candidate_path(project_id, part_id, edit_job_id, attempt)
                for attempt in range(1, max_attempts + 1)
            ],
        ]
        for path in paths:
            self.storage.pop(path, None)
        return None

    def queue_candidate_validation(
        self,
        *,
        edit_job_id,
        candidate_path,
        candidate_hash,
        attempt,
    ):
        self.validation_queue_count += 1
        result = copy.deepcopy(self.validation_results[attempt - 1])
        for diagnostic in result.get("diagnostics", []):
            diagnostic.setdefault("file_path", candidate_path)
        validation_id = f"validation-{attempt}"
        self.generations[validation_id] = {
            "id": validation_id,
            "project_id": PROJECT_ID,
            "part_id": PART_ID,
            "type": "validate_cad",
            "status": ("completed" if result.get("status") == "passed" else "failed"),
            "source_kind": "candidate",
            "source_storage_path": candidate_path,
            "source_sha256": candidate_hash,
            "edit_job_id": edit_job_id,
            "result": result,
            "error_message": None,
        }
        self.patch_edit_job(
            edit_job_id,
            {
                "state": "validating_candidate",
                "attempt_count": attempt,
                "current_candidate_path": candidate_path,
                "current_candidate_sha256": candidate_hash,
                "validation_job_id": validation_id,
            },
        )
        return validation_id

    def generation_job(self, generation_job_id):
        return copy.deepcopy(self.generations[generation_job_id])

    def queue_index(self, edit_job_id, state):
        self.index_queue_count += 1
        index_id = f"index-{self.index_queue_count}"
        failed = state == "reindexing" and self.fail_reindex
        if not failed:
            self.index = self._build_index()
        self.index_jobs[index_id] = {
            "id": index_id,
            "status": "failed" if failed else "completed",
            "error_message": "simulated reindex failure" if failed else None,
        }
        self.patch_edit_job(
            edit_job_id,
            {"state": state, "index_job_id": index_id},
        )
        return index_id

    def index_job(self, index_job_id):
        return copy.deepcopy(self.index_jobs[index_job_id])

    def queue_export(self, edit_job_id, source_hash_value):
        self.export_queue_count += 1
        export_id = f"export-{self.export_queue_count}"
        self.generations[export_id] = {
            "id": export_id,
            "type": "export_cad",
            "status": "queued",
            "source_sha256": source_hash_value,
            "edit_job_id": edit_job_id,
        }
        self.patch_edit_job(
            edit_job_id,
            {"state": "queueing_export", "export_job_id": export_id},
        )
        return export_id

    def complete_edit_job(self, edit_job_id, result):
        return self.patch_edit_job(
            edit_job_id,
            {
                "status": "completed",
                "state": "completed",
                "result": copy.deepcopy(result),
            },
        )

    def fail_edit_job(self, edit_job_id, *, code, message, result):
        return self.patch_edit_job(
            edit_job_id,
            {
                "status": "failed",
                "state": "failed",
                "error_code": code,
                "error_message": message,
                "result": copy.deepcopy(result),
            },
        )


def run_workflow(repository: FakeRepository, agent: FakeAgent):
    orchestrator = EditWorkflowOrchestrator(
        repository,
        agent,
        worker_id="test-worker",
        sleep=lambda _seconds: None,
    )
    return orchestrator.run(repository.edit_job(EDIT_JOB_ID))


class EditOrchestrationTests(unittest.TestCase):
    def test_successfully_indexes_plans_validates_commits_reindexes_and_exports(self):
        repository = FakeRepository([passed_validation()], initial_index=False)
        agent = FakeAgent(body_plan(4))

        result = run_workflow(repository, agent)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["attempts"], 1)
        self.assertIn("extrude(4)", repository.storage[CANONICAL_PATH])
        self.assertEqual(repository.validation_queue_count, 1)
        self.assertEqual(repository.index_queue_count, 2)
        self.assertEqual(repository.export_queue_count, 1)
        self.assertEqual(repository.jobs[EDIT_JOB_ID]["status"], "completed")
        self.assertEqual(repository.cleanup_count, 1)
        self.assertEqual(agent.edit_contexts[0].part_id, PART_ID)

    def test_repairs_the_latest_candidate_once_before_commit(self):
        repository = FakeRepository([repairable_validation(), passed_validation()])
        agent = FakeAgent(body_plan(-1), [body_plan(5, "Repair geometry")])

        result = run_workflow(repository, agent)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(repository.validation_queue_count, 2)
        self.assertEqual(len(agent.repair_contexts), 1)
        failed_chunks = agent.repair_contexts[0].failed_candidate_chunks
        self.assertTrue(
            any("extrude(-1)" in chunk["source"] for chunk in failed_chunks)
        )
        self.assertIn("extrude(5)", repository.storage[CANONICAL_PATH])

    def test_stops_after_three_failed_candidate_attempts(self):
        repository = FakeRepository(
            [
                repairable_validation("attempt one"),
                repairable_validation("attempt two"),
                repairable_validation("attempt three"),
            ]
        )
        agent = FakeAgent(
            body_plan(-1),
            [body_plan(-2), body_plan(-3)],
        )

        result = run_workflow(repository, agent)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "MAX_REPAIR_ATTEMPTS")
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(repository.validation_queue_count, 3)
        self.assertEqual(repository.storage[CANONICAL_PATH], MODEL_SOURCE)
        self.assertEqual(repository.export_queue_count, 0)

    def test_rejects_a_source_race_before_candidate_creation(self):
        raced_source = MODEL_SOURCE.replace(
            "plate_width: float = 20.0",
            "plate_width: float = 21.0",
        )
        repository = FakeRepository(
            [passed_validation()],
            race_source=raced_source,
        )
        agent = FakeAgent(body_plan(4))

        result = run_workflow(repository, agent)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "SOURCE_CHANGED")
        self.assertEqual(repository.storage[CANONICAL_PATH], raced_source)
        self.assertEqual(repository.validation_queue_count, 0)
        self.assertEqual(agent.edit_contexts, [])

    def test_restart_reuses_persisted_candidate_and_validation_job(self):
        repository = FakeRepository([passed_validation()])
        edit_plan = body_plan(6)
        candidate = apply_edit_plan(
            MODEL_SOURCE,
            expected_hash=source_hash(MODEL_SOURCE),
            part_id=PART_ID,
            semantic_ids=["mount_holes"],
            plan=edit_plan,
        )
        candidate_path = repository.candidate_path(
            PROJECT_ID,
            PART_ID,
            EDIT_JOB_ID,
            1,
        )
        original_path = repository.original_path(PROJECT_ID, PART_ID, EDIT_JOB_ID)
        repository.storage[candidate_path] = candidate.content
        repository.storage[original_path] = MODEL_SOURCE
        validation_id = "persisted-validation"
        repository.generations[validation_id] = {
            "id": validation_id,
            "project_id": PROJECT_ID,
            "part_id": PART_ID,
            "type": "validate_cad",
            "status": "completed",
            "source_kind": "candidate",
            "source_storage_path": candidate_path,
            "source_sha256": candidate.content_hash,
            "edit_job_id": EDIT_JOB_ID,
            "result": passed_validation(),
        }
        repository.patch_edit_job(
            EDIT_JOB_ID,
            {
                "resolved_part_id": PART_ID,
                "resolved_targets": [
                    {
                        "part_id": PART_ID,
                        "part_name": "Left Bracket",
                        "semantic_ids": ["mount_holes"],
                        "confidence": 1.0,
                        "reason": "persisted",
                        "candidates": [],
                    }
                ],
                "state": "validating_candidate",
                "attempt_count": 1,
                "accepted_source_sha256": source_hash(MODEL_SOURCE),
                "original_storage_path": original_path,
                "current_candidate_path": candidate_path,
                "current_candidate_sha256": candidate.content_hash,
                "validation_job_id": validation_id,
                "history": [
                    {
                        "event": "candidate_created",
                        "attempt": 1,
                        "base_hash": source_hash(MODEL_SOURCE),
                        "candidate_hash": candidate.content_hash,
                        "candidate_path": candidate_path,
                        "changed_symbols": candidate.changed_symbols,
                        "plan": edit_plan.model_dump(mode="json"),
                    }
                ],
            },
        )
        agent = FakeAgent(body_plan(99))

        result = run_workflow(repository, agent)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(repository.validation_queue_count, 0)
        self.assertEqual(agent.edit_contexts, [])
        self.assertIn("extrude(6)", repository.storage[CANONICAL_PATH])
        self.assertEqual(repository.index_queue_count, 1)
        self.assertEqual(repository.export_queue_count, 1)

    def test_failed_reindex_restores_the_original_source(self):
        repository = FakeRepository(
            [passed_validation()],
            fail_reindex=True,
        )
        agent = FakeAgent(body_plan(7))

        result = run_workflow(repository, agent)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "REINDEX_FAILED")
        self.assertTrue(result["source_restored"])
        self.assertEqual(repository.storage[CANONICAL_PATH], MODEL_SOURCE)
        self.assertEqual(repository.export_queue_count, 0)
        self.assertEqual(repository.cleanup_count, 1)

    def test_established_edit_can_repair_a_newly_added_feature(self):
        failed = repairable_validation("New slot geometry is invalid.")
        failed["diagnostics"][0].update(
            {
                "function_name": "cut_drainage_slots",
                "semantic_id": "drainage_slots",
                "related_symbols": ["cut_drainage_slots"],
            }
        )
        repository = FakeRepository([failed, passed_validation()])
        initial_plan = plan(
            {
                "operation": "add_model_parameter",
                "name": "slot_width",
                "field_source": "slot_width: float = 3.0",
            },
            {
                "operation": "add_cad_feature",
                "semantic_id": "drainage_slots",
                "function_name": "cut_drainage_slots",
                "role": "drainage_features",
                "parameters": ["slot_width"],
                "depends_on": ["mount_holes"],
                "search_keys": ["drainage slots"],
                "function_source": (
                    "def cut_drainage_slots(params: ModelParams, base):\n"
                    "    return base"
                ),
            },
            {
                "operation": "replace_build_model_body",
                "target_id": f"{PART_ID}:build_model_body:build_model",
                "replacement_source": (
                    "base = cut_mounting_holes(params)\n"
                    "return cut_drainage_slots(params, base)"
                ),
            },
        )
        repair_plan = plan(
            {
                "operation": "replace_function_body",
                "target_id": f"{PART_ID}:function_body:cut_drainage_slots",
                "replacement_source": (
                    "return base.faces('>Z').workplane().rect("
                    "params.slot_width, params.slot_width * 2).cutThruAll()"
                ),
            },
            semantic_ids=["mount_holes", "drainage_slots"],
        )
        agent = FakeAgent(initial_plan, [repair_plan])

        result = run_workflow(repository, agent)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["attempts"], 2)
        self.assertIn("cut_drainage_slots", repository.storage[CANONICAL_PATH])
        self.assertEqual(len(agent.repair_contexts), 1)
        self.assertTrue(
            any(
                target.kind == "function_body"
                and target.name == "cut_drainage_slots"
                for target in agent.repair_contexts[0].allowed_targets
            )
        )

    def test_initial_design_generates_validated_model_for_blank_linked_part(self):
        repository = FakeRepository([passed_validation()], initial_index=False)
        repository.storage[CANONICAL_PATH] = (
            "from cadquery_runtime import cad_part, cq, dataclass\n"
        )
        repository.jobs[EDIT_JOB_ID].update(
            {
                "workflow_mode": "initial_design",
                "resolved_part_id": PART_ID,
                "resolved_targets": [
                    {
                        "part_id": PART_ID,
                        "part_name": "Left Bracket",
                        "semantic_ids": [],
                        "confidence": 1.0,
                        "reason": "linked blank part",
                        "candidates": [],
                    }
                ],
            }
        )
        agent = FakeAgent(body_plan(1))
        agent.initial_models = [
            InitialCadModel(
                summary="Create a soap holder",
                model_body='''@dataclass(frozen=True)
class ModelParams:
    width_mm: float = 90.0

@cad_part(
    semantic_id="soap_tray",
    role="draining_tray",
    library="cadquery",
    parameters=("width_mm",),
    depends_on=(),
    search_keys=("soap holder", "draining tray"),
)
def build_soap_tray(params: ModelParams):
    """Create the primary soap tray."""
    return cq.Workplane("XY").box(params.width_mm, 60.0, 8.0)

def build_model(params: ModelParams):
    return build_soap_tray(params)''',
            )
        ]

        result = run_workflow(repository, agent)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(repository.index_queue_count, 2)
        self.assertEqual(repository.export_queue_count, 1)
        self.assertEqual(len(agent.initial_design_contexts), 1)
        self.assertIn("soap_tray", repository.storage[CANONICAL_PATH])
        self.assertNotIn("headphone", repository.storage[CANONICAL_PATH])


if __name__ == "__main__":
    unittest.main()
