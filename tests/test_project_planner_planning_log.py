from pathlib import Path
import stat
import tempfile
import unittest

from workers.project_planner.project_planner.planning_log import PlanningLogWriter


JOB_ID = "33333333-3333-4333-8333-333333333333"


class PlanningLogWriterTests(unittest.TestCase):
    def test_writes_private_human_readable_job_log_on_success(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PlanningLogWriter(directory)

            path = Path(
                writer.write(
                    job={
                        "id": JOB_ID,
                        "project_id": "22222222-2222-4222-8222-222222222222",
                        "request_text": "An adjustable phone stand.",
                        "auto_publish": True,
                    },
                    attempts=[{"attempt": 0, "violations": [{"code": "PROJECT_PLAN_TOO_COMPLEX"}]}],
                    plan={"plan_id": "44444444-4444-4444-8444-444444444444", "summary": "Two-part stand."},
                    spec={"spec_id": "55555555-5555-4555-8555-555555555555", "nodes": []},
                )
            )

            self.assertEqual(path.name, f"{JOB_ID}.txt")
            content = path.read_text(encoding="utf-8")
            self.assertIn("Project Planner Log", content)
            self.assertIn("auto_publish: True", content)
            self.assertIn("An adjustable phone stand.", content)
            self.assertIn('"code": "PROJECT_PLAN_TOO_COMPLEX"', content)
            self.assertIn('"plan_id": "44444444-4444-4444-8444-444444444444"', content)
            self.assertIn('"spec_id": "55555555-5555-4555-8555-555555555555"', content)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_writes_a_failure_section_when_no_plan_was_produced(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PlanningLogWriter(directory)

            path = Path(
                writer.write(
                    job={
                        "id": JOB_ID,
                        "project_id": "22222222-2222-4222-8222-222222222222",
                        "request_text": "Ambiguous request.",
                    },
                    attempts=[],
                    failure={
                        "code": "PROJECT_CLARIFICATION_REQUIRED",
                        "message": "Should the lid be removable?",
                    },
                )
            )

            content = path.read_text(encoding="utf-8")
            self.assertIn("FAILURE", content)
            self.assertIn("PROJECT_CLARIFICATION_REQUIRED", content)
            self.assertNotIn("PROJECT PLAN\n------------", content)

    def test_reclaimed_job_overwrites_the_same_file(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PlanningLogWriter(directory)
            job = {"id": JOB_ID, "project_id": "p", "request_text": "r"}

            first_path = writer.write(job=job, attempts=[], plan={"summary": "first"})
            second_path = writer.write(job=job, attempts=[], plan={"summary": "second"})

            self.assertEqual(first_path, second_path)
            content = Path(second_path).read_text(encoding="utf-8")
            self.assertIn('"summary": "second"', content)
            self.assertNotIn('"summary": "first"', content)

    def test_defaults_to_project_planner_logs_directory(self):
        writer = PlanningLogWriter()
        self.assertTrue(str(writer.directory).endswith("workers/project_planner/logs"))


if __name__ == "__main__":
    unittest.main()
