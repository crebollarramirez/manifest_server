from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTION_SOURCE = (
    ROOT / "services" / "cad_agent" / "src" / "cad-actions.service.ts"
).read_text(encoding="utf-8")
ACTION_CONTROLLER = (
    ROOT / "services" / "cad_agent" / "src" / "cad-actions.controller.ts"
).read_text(encoding="utf-8")
SUPABASE_CONFIG = (ROOT / "supabase" / "config.toml").read_text(encoding="utf-8")
REQUESTED_PART_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260726010000_add_requested_cad_part.sql"
).read_text(encoding="utf-8")


def load_worker_module(module_name: str, path: Path, dependency_name: str):
    fake_supabase = types.ModuleType("supabase")
    fake_supabase.create_client = lambda *_args, **_kwargs: object()
    fake_dependency = types.ModuleType(dependency_name)
    fake_dependency.SupersededJob = type("SupersededJob", (Exception,), {})
    fake_dependency.run_export_job = lambda *_args, **_kwargs: None
    fake_dependency.validate_cad_job = lambda *_args, **_kwargs: None

    previous_supabase = sys.modules.get("supabase")
    previous_dependency = sys.modules.get(dependency_name)
    previous_url = os.environ.get("SUPABASE_URL")
    previous_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    sys.modules["supabase"] = fake_supabase
    sys.modules[dependency_name] = fake_dependency
    os.environ["SUPABASE_URL"] = "http://localhost"
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_supabase is None:
            sys.modules.pop("supabase", None)
        else:
            sys.modules["supabase"] = previous_supabase
        if previous_dependency is None:
            sys.modules.pop(dependency_name, None)
        else:
            sys.modules[dependency_name] = previous_dependency
        if previous_url is None:
            os.environ.pop("SUPABASE_URL", None)
        else:
            os.environ["SUPABASE_URL"] = previous_url
        if previous_key is None:
            os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        else:
            os.environ["SUPABASE_SERVICE_ROLE_KEY"] = previous_key


class CadAgentCutoverContractTests(unittest.TestCase):
    def test_nest_owns_the_action_endpoint_and_edge_runtime_is_removed(self):
        self.assertIn("@Controller('v1/cad-agent/actions')", ACTION_CONTROLLER)
        self.assertIn("@HttpCode(HttpStatus.OK)", ACTION_CONTROLLER)
        self.assertFalse(
            (ROOT / "supabase" / "functions" / "cad-agent" / "index.ts").exists()
        )
        self.assertNotIn("[functions.cad-agent]", SUPABASE_CONFIG)

    def test_nest_actions_preserve_manual_jobs_initialization_and_status(self):
        self.assertIn("currentCadSourceSha256", ACTION_SOURCE)
        self.assertIn("queueGenerationJob(part, 'validate_cad'", ACTION_SOURCE)
        self.assertIn("? `${CAD_MODEL_RUNTIME_IMPORT}\\n`", ACTION_SOURCE)
        self.assertIn("queueStandaloneIndexJob(part.project_id, 'build_index')", ACTION_SOURCE)
        self.assertIn("publicActionJob(job)", ACTION_SOURCE)
        self.assertIn("this.submissions.submit", ACTION_SOURCE)

    def test_linked_cad_target_remains_constrained_to_its_project(self):
        self.assertIn("this.repository.part(action.project_id, action.part_id)", ACTION_SOURCE)
        self.assertIn("add column requested_part_id uuid", REQUESTED_PART_MIGRATION)
        self.assertIn(
            "foreign key (project_id, requested_part_id)",
            REQUESTED_PART_MIGRATION,
        )
        self.assertIn(
            "references public.parts(project_id, id)",
            REQUESTED_PART_MIGRATION,
        )

    def test_mesh_chat_retains_direct_generation_and_export(self):
        self.assertIn("this.mesh.generate(part, action.messages)", ACTION_SOURCE)
        self.assertIn("Updated mesh part", ACTION_SOURCE)


class WorkerLoggingTests(unittest.TestCase):
    def test_exporter_prints_full_failure_to_stderr(self):
        worker = load_worker_module(
            "test_cad_worker",
            ROOT / "workers" / "cad_exporter" / "cad_worker.py",
            "run_export_job",
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            worker.print_job_error("job-1", "Traceback\ngeometry exploded")

        output = stderr.getvalue()
        self.assertIn("export[job-1] failed", output)
        self.assertIn("Traceback\ngeometry exploded", output)

    def test_validator_prints_every_contract_and_runtime_error(self):
        worker = load_worker_module(
            "test_cad_validation_worker",
            ROOT / "workers" / "cad_validator" / "cad_validation_worker.py",
            "validate_cad_job",
        )
        report = {
            "valid": False,
            "checks": {
                "model_params": {
                    "errors": [{"message": "ModelParams is missing", "line": 1, "column": 1}],
                },
                "build_model": {
                    "errors": [{"message": "build_model is missing", "line": 2, "column": 1}],
                },
                "cad_part_decorators": {
                    "errors": [{"message": "cad_part is missing", "line": 3, "column": 1}],
                },
            },
            "runtime": {
                "errors": [{"message": "Runtime skipped"}],
                "stdout": "",
                "stderr": "contract traceback",
            },
        }
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            worker.print_report("job-2", report)

        output = stderr.getvalue()
        self.assertIn("ModelParams is missing", output)
        self.assertIn("build_model is missing", output)
        self.assertIn("cad_part is missing", output)
        self.assertIn("Runtime skipped", output)
        self.assertIn("contract traceback", output)
        self.assertIn('"valid": false', output)


if __name__ == "__main__":
    unittest.main()
