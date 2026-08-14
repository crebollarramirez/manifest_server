from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkerComposeContractTests(unittest.TestCase):
    def test_exporter_and_validator_have_independent_compose_projects(self):
        exporter = (
            ROOT / "workers" / "cad_exporter" / "docker-compose.yml"
        ).read_text(encoding="utf-8")
        validator = (
            ROOT / "workers" / "cad_validator" / "docker-compose.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("cad-worker:", exporter)
        self.assertNotIn("cad-validator:", exporter)
        self.assertIn("name: manifest-cad-validator", validator)
        self.assertIn("cad-validator:", validator)
        self.assertIn("manifest-cad-validator:local", validator)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY is required", validator)
        dockerfile = (
            ROOT / "workers" / "cad_validator" / "Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn('CMD ["python", "cad_validation_worker.py"]', dockerfile)

        indexer = (
            ROOT / "workers" / "indexer" / "docker-compose.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("name: manifest-indexer", indexer)
        self.assertEqual(indexer.count("\n  indexer:\n"), 1)
        self.assertNotIn("getter:", indexer)

        editor = (
            ROOT / "workers" / "agent_3d" / "docker-compose.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("name: manifest-cad-agent", editor)
        self.assertEqual(editor.count("\n  cad-agent:\n"), 1)
        self.assertEqual(editor.count("\n  cad-editor:\n"), 1)
        self.assertNotIn("cad-tool-worker:", editor)
        self.assertIn("services/cad_agent/Dockerfile", editor)
        self.assertIn("OPENAI_API_KEY", editor)
        self.assertIn("CAD_EDITOR_LEASE_SECONDS", editor)
        self.assertIn("CAD_EDITOR_PLANNING_ONLY", editor)
        self.assertIn("CAD_AGENT_PORT: 3000", editor)
        self.assertIn('"${CAD_AGENT_PORT:-3000}:3000"', editor)
        self.assertIn("CAD_AGENT_EVENT_POLL_INTERVAL_MS", editor)
        self.assertNotIn("CAD_AGENT_LEASE_SECONDS", editor)
        self.assertNotIn("cad-editor-repair:", editor)
        editor_dockerfile = (
            ROOT / "workers" / "agent_3d" / "Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn('CMD ["python", "workers/agent_3d/edit_worker.py"]', editor_dockerfile)
        self.assertIn("workers/agent_3d/planning/prompts", editor_dockerfile)

        planner = (
            ROOT / "workers" / "project_planner" / "docker-compose.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("name: manifest-project-planner", planner)
        self.assertEqual(planner.count("\n  project-planner:\n"), 1)
        self.assertIn("manifest-project-planner:local", planner)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY is required", planner)
        self.assertIn("OPENAI_API_KEY is required", planner)
        self.assertIn("OPENAI_PROJECT_PLANNING_MODEL", planner)
        self.assertNotIn("worker_id", planner)
        self.assertNotIn("LEASE_SECONDS", planner)
        planner_dockerfile = (
            ROOT / "workers" / "project_planner" / "Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'CMD ["python", "workers/project_planner/project_planner_worker.py"]',
            planner_dockerfile,
        )
        self.assertIn("workers/indexer/indexer", planner_dockerfile)
        self.assertIn("workers/project_planner/project_planner", planner_dockerfile)

        # assembly_publisher was folded into project_planner_worker.py --
        # there is no separate deployable for it anymore.
        self.assertFalse((ROOT / "workers" / "assembly_publisher").exists())


if __name__ == "__main__":
    unittest.main()
