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
            ROOT / "workers" / "cad_editor" / "docker-compose.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("name: manifest-cad-editor", editor)
        self.assertEqual(editor.count("\n  cad-editor:\n"), 1)
        self.assertNotIn("cad-editor-repair:", editor)
        editor_dockerfile = (
            ROOT / "workers" / "cad_editor" / "Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn('CMD ["python", "workers/cad_editor/edit_worker.py"]', editor_dockerfile)


if __name__ == "__main__":
    unittest.main()
