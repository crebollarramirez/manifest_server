from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from workers.agent_3d.planning.agent_trace import (
    AgentTraceContext,
    AgentTraceWriter,
    content_sha256,
)


EDIT_JOB_ID = "33333333-3333-4333-8333-333333333333"


class AgentTraceContextTests(unittest.TestCase):
    def test_is_frozen(self):
        context = AgentTraceContext(
            edit_job_id=EDIT_JOB_ID, agent_turn=1, step_attempt=1, reasoning_round=1
        )

        with self.assertRaises(Exception):
            context.agent_turn = 2  # type: ignore[misc]


class ContentSha256Tests(unittest.TestCase):
    def test_same_content_hashes_the_same(self):
        self.assertEqual(content_sha256("abc"), content_sha256("abc"))

    def test_different_content_hashes_differently(self):
        self.assertNotEqual(content_sha256("abc"), content_sha256("abd"))

    def test_returns_a_hex_digest(self):
        digest = content_sha256("abc")
        self.assertEqual(len(digest), 64)
        int(digest, 16)  # raises ValueError if not valid hex


class AgentTraceWriterTests(unittest.TestCase):
    def test_appends_one_json_line_per_call(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = AgentTraceWriter(directory)

            writer.log("llm.request", edit_job_id=EDIT_JOB_ID, agent_turn=1)
            writer.log("llm.response", edit_job_id=EDIT_JOB_ID, agent_turn=1)

            path = Path(directory) / f"{EDIT_JOB_ID}.trace.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            second = json.loads(lines[1])
            self.assertEqual(first["event"], "llm.request")
            self.assertEqual(second["event"], "llm.response")
            self.assertEqual(first["edit_job_id"], EDIT_JOB_ID)
            self.assertIn("timestamp", first)

    def test_returns_the_absolute_trace_path(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = AgentTraceWriter(directory)

            returned = writer.log("llm.request", edit_job_id=EDIT_JOB_ID)

            self.assertEqual(returned, str((Path(directory) / f"{EDIT_JOB_ID}.trace.jsonl").resolve()))

    def test_file_is_created_with_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = AgentTraceWriter(directory)

            writer.log("llm.request", edit_job_id=EDIT_JOB_ID)

            path = Path(directory) / f"{EDIT_JOB_ID}.trace.jsonl"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_different_edit_jobs_get_separate_files(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = AgentTraceWriter(directory)

            writer.log("llm.request", edit_job_id="job-a")
            writer.log("llm.request", edit_job_id="job-b")

            self.assertTrue((Path(directory) / "job-a.trace.jsonl").exists())
            self.assertTrue((Path(directory) / "job-b.trace.jsonl").exists())

    def test_serializes_a_pydantic_model_via_model_dump(self):
        class FakeModel:
            def model_dump(self, mode="python"):
                return {"mode": mode, "value": 1}

        with tempfile.TemporaryDirectory() as directory:
            writer = AgentTraceWriter(directory)

            writer.log("llm.response", edit_job_id=EDIT_JOB_ID, response=FakeModel())

            path = Path(directory) / f"{EDIT_JOB_ID}.trace.jsonl"
            record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["response"], {"mode": "json", "value": 1})

    def test_serializes_nested_simplenamespace_objects(self):
        response = SimpleNamespace(
            id="r1",
            output=[SimpleNamespace(type="function_call", name="index_search")],
        )

        with tempfile.TemporaryDirectory() as directory:
            writer = AgentTraceWriter(directory)

            writer.log("llm.response", edit_job_id=EDIT_JOB_ID, response=response)

            path = Path(directory) / f"{EDIT_JOB_ID}.trace.jsonl"
            record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["response"]["id"], "r1")
            self.assertEqual(record["response"]["output"][0]["name"], "index_search")

    def test_unserializable_values_fall_back_to_str_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = AgentTraceWriter(directory)

            class Opaque:
                __slots__ = ()

                def __repr__(self):
                    return "<opaque>"

            # No __dict__, no model_dump -- exercises the json.dumps(default=str)
            # safety net rather than raising a serialization error.
            writer.log("tool.started", edit_job_id=EDIT_JOB_ID, weird=Opaque())

            path = Path(directory) / f"{EDIT_JOB_ID}.trace.jsonl"
            record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["weird"], "<opaque>")

    def test_reads_the_directory_from_the_env_var_when_not_given_explicitly(self):
        import os

        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("CAD_EDITOR_LOG_DIRECTORY")
            os.environ["CAD_EDITOR_LOG_DIRECTORY"] = directory
            try:
                writer = AgentTraceWriter()
                self.assertEqual(writer.directory, Path(directory))
            finally:
                if previous is None:
                    os.environ.pop("CAD_EDITOR_LOG_DIRECTORY", None)
                else:
                    os.environ["CAD_EDITOR_LOG_DIRECTORY"] = previous


if __name__ == "__main__":
    unittest.main()
