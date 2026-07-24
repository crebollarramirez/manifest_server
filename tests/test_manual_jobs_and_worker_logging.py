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
EDGE_SOURCE = (
    ROOT / "supabase" / "functions" / "cad-agent" / "index.ts"
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


class ManualJobEdgeContractTests(unittest.TestCase):
    def test_edge_registers_manual_export_and_validation_actions(self):
        self.assertIn('| "export_part"', EDGE_SOURCE)
        self.assertIn('| "validate_part"', EDGE_SOURCE)
        self.assertIn("handleExportPart(supabase, body)", EDGE_SOURCE)
        self.assertIn("handleValidatePart(supabase, body)", EDGE_SOURCE)

    def test_manual_cad_jobs_are_bound_to_the_current_source_hash(self):
        self.assertIn("currentCadSourceSha256", EDGE_SOURCE)
        self.assertIn('queueGenerationJob(\n    supabase,\n    part,\n    "validate_cad"', EDGE_SOURCE)
        self.assertIn('part.part_type === "cad"\n    ? "export_cad"', EDGE_SOURCE)
        self.assertIn('throw new RequestError("Only CAD parts support validation."', EDGE_SOURCE)

    def test_part_listing_exposes_ids_used_by_manual_commands(self):
        self.assertIn("id=${part.id}", EDGE_SOURCE)


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
