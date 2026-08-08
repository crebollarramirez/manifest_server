from pathlib import Path
import stat
import tempfile
import unittest

from workers.agent_3d.planning.planning_log import PlanningLogWriter


EDIT_JOB_ID = "33333333-3333-4333-8333-333333333333"


class PlanningLogWriterTests(unittest.TestCase):
    def test_writes_private_human_readable_job_log(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PlanningLogWriter(directory)

            path = Path(
                writer.write(
                    job={
                        "id": EDIT_JOB_ID,
                        "project_id": "22222222-2222-4222-8222-222222222222",
                        "requested_part_id": "11111111-1111-4111-8111-111111111111",
                        "resolved_part_id": "11111111-1111-4111-8111-111111111111",
                        "workflow_mode": "edit",
                        "request_text": "Make the bracket taller",
                        "messages": [
                            {"role": "user", "content": "Make the bracket taller"}
                        ],
                    },
                    goal={
                        "goal_id": "44444444-4444-4444-8444-444444444444",
                        "description": "Increase bracket height.",
                    },
                    plan={
                        "plan_id": "55555555-5555-4555-8555-555555555555",
                        "summary": "Update the bracket height.",
                    },
                )
            )

            self.assertEqual(path.name, f"{EDIT_JOB_ID}.txt")
            content = path.read_text(encoding="utf-8")
            self.assertIn("CAD Editor Planning Log", content)
            self.assertIn("Make the bracket taller", content)
            self.assertIn('"goal_id": "44444444-4444-4444-8444-444444444444"', content)
            self.assertIn('"plan_id": "55555555-5555-4555-8555-555555555555"', content)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    # Per-turn Agent3D reasoning logging was superseded by the structured
    # trace system (workers/agent_3d/planning/agent_trace.py) -- see
    # test_agent_trace.py and test_agent_3d.py for its coverage.


if __name__ == "__main__":
    unittest.main()
