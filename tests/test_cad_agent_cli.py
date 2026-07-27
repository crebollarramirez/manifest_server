from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *_args, **_kwargs: None
    sys.modules["dotenv"] = dotenv

import cad_agent_cli


PROJECT = {"id": "11111111-1111-4111-8111-111111111111", "project_name": "Desk Mount"}
PART = {
    "id": "22222222-2222-4222-8222-222222222222",
    "project_id": PROJECT["id"],
    "part_name": "Left Bracket",
    "part_type": "cad",
}
MESH_PART = {
    "id": "44444444-4444-4444-8444-444444444444",
    "project_id": PROJECT["id"],
    "part_name": "Dragon Body",
    "part_type": "mesh",
}
EDIT_JOB_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class FakeFunctions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, function_name, invoke_options):
        self.calls.append((function_name, invoke_options))
        if not self.responses:
            raise AssertionError("No fake response remains for this invocation")
        return self.responses.pop(0)


class FakeSupabase:
    def __init__(self, responses):
        self.functions = FakeFunctions(responses)


class CommandParsingTests(unittest.TestCase):
    def test_parses_all_supported_commands(self):
        cases = {
            "/create -project Desk Mount": ("create_project", "Desk Mount"),
            '/create -part -cad "Left Bracket"': ("create_part", "Left Bracket"),
            '/create -part -mesh "Dragon Body"': ("create_part", "Dragon Body"),
            "/link -project Desk Mount": ("link_project", "Desk Mount"),
            "/link -part Left Bracket": ("link_part", "Left Bracket"),
            "/list -projects": ("list_projects", ""),
            "/list -parts": ("list_parts", ""),
            f"/export {PART['id']}": ("export_part", PART["id"]),
            f"/validate {PART['id']}": ("validate_part", PART["id"]),
            f"/index {PROJECT['id']}": ("index_project", PROJECT["id"]),
            "/index -test make the mounting holes bigger": (
                "test_index",
                "make the mounting holes bigger",
            ),
            f"/edit-status {EDIT_JOB_ID}": ("get_edit_job", EDIT_JOB_ID),
            "/delete -project Desk Mount": ("delete_project", "Desk Mount"),
            "/delete -part Left Bracket": ("delete_part", "Left Bracket"),
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                command = cad_agent_cli.parse_command(source)
                self.assertEqual((command.action, command.name), expected)

        mesh_command = cad_agent_cli.parse_command('/create -part -mesh "Dragon Body"')
        self.assertEqual(mesh_command.part_type, "mesh")

    def test_rejects_unknown_or_unterminated_commands(self):
        with self.assertRaises(cad_agent_cli.CommandError):
            cad_agent_cli.parse_command("/create -part Left Bracket")
        with self.assertRaises(cad_agent_cli.CommandError):
            cad_agent_cli.parse_command('/link -project "Desk Mount')
        with self.assertRaises(cad_agent_cli.CommandError):
            cad_agent_cli.parse_command("/export")
        with self.assertRaises(cad_agent_cli.CommandError):
            cad_agent_cli.parse_command(f"/validate {PART['id']} extra")
        with self.assertRaises(cad_agent_cli.CommandError):
            cad_agent_cli.parse_command("/index -test")
        with self.assertRaises(cad_agent_cli.CommandError):
            cad_agent_cli.parse_command("/edit-status")


class LinkedStateTests(unittest.TestCase):
    def test_linking_project_and_part_clears_history(self):
        state = cad_agent_cli.CliState(history=[{"role": "user", "content": "old"}])
        state.link_project(PROJECT.copy())
        self.assertEqual(state.project, PROJECT)
        self.assertIsNone(state.part)
        self.assertEqual(state.history, [])

        state.history.append({"role": "user", "content": "another"})
        state.link_part(PART.copy())
        self.assertEqual(state.part, PART)
        self.assertEqual(state.history, [])

    def test_create_commands_auto_link(self):
        supabase = FakeSupabase([
            {"message": "created project", "project": PROJECT},
            {"message": "created part", "part": PART},
        ])
        state = cad_agent_cli.CliState()

        cad_agent_cli.handle_command(
            supabase,
            state,
            cad_agent_cli.parse_command("/create -project Desk Mount"),
        )
        self.assertEqual(state.project, PROJECT)

        cad_agent_cli.handle_command(
            supabase,
            state,
            cad_agent_cli.parse_command("/create -part -cad Left Bracket"),
        )
        self.assertEqual(state.part, PART)

    def test_part_operation_requires_linked_project(self):
        state = cad_agent_cli.CliState()
        with self.assertRaisesRegex(cad_agent_cli.CommandError, "linked project"):
            cad_agent_cli.handle_command(
                FakeSupabase([]),
                state,
                cad_agent_cli.parse_command("/link -part Left Bracket"),
            )

        with self.assertRaisesRegex(cad_agent_cli.CommandError, "linked project"):
            cad_agent_cli.handle_command(
                FakeSupabase([]),
                state,
                cad_agent_cli.parse_command("/list -parts"),
            )

    def test_list_projects_preserves_linked_state_and_history(self):
        supabase = FakeSupabase([{"message": "Projects:\n- Desk Mount"}])
        history = [{"role": "user", "content": "keep this"}]
        state = cad_agent_cli.CliState(
            project=PROJECT.copy(),
            part=PART.copy(),
            history=history.copy(),
        )

        result = cad_agent_cli.handle_command(
            supabase,
            state,
            cad_agent_cli.parse_command("/list -projects"),
        )

        self.assertEqual(result, "Projects:\n- Desk Mount")
        self.assertEqual(state.project, PROJECT)
        self.assertEqual(state.part, PART)
        self.assertEqual(state.history, history)
        self.assertEqual(
            supabase.functions.calls[0][1]["body"],
            {"action": "list_projects"},
        )

    def test_list_parts_scopes_request_to_linked_project(self):
        supabase = FakeSupabase([
            {"message": 'Parts in "Desk Mount":\n- Left Bracket [cad]'},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy(), part=PART.copy())

        result = cad_agent_cli.handle_command(
            supabase,
            state,
            cad_agent_cli.parse_command("/list -parts"),
        )

        self.assertIn("Left Bracket [cad]", result)
        self.assertEqual(
            supabase.functions.calls[0][1]["body"],
            {"action": "list_parts", "project_id": PROJECT["id"]},
        )
        self.assertEqual(state.part, PART)

    def test_creating_mesh_part_auto_links_and_clears_history(self):
        supabase = FakeSupabase([{"message": "created mesh", "part": MESH_PART}])
        state = cad_agent_cli.CliState(
            project=PROJECT.copy(),
            part=PART.copy(),
            history=[{"role": "user", "content": "old CAD context"}],
        )

        cad_agent_cli.handle_command(
            supabase,
            state,
            cad_agent_cli.parse_command('/create -part -mesh "Dragon Body"'),
        )

        self.assertEqual(state.part, MESH_PART)
        self.assertEqual(state.history, [])
        body = supabase.functions.calls[0][1]["body"]
        self.assertEqual(body["part_type"], "mesh")

    def test_cancelled_delete_does_not_mutate(self):
        supabase = FakeSupabase([
            {"message": "resolved", "part": PART},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy(), part=PART.copy())
        result = cad_agent_cli.handle_command(
            supabase,
            state,
            cad_agent_cli.parse_command("/delete -part left bracket"),
            read_input=lambda _prompt: "n",
        )
        self.assertEqual(result, "Part deletion cancelled.")
        self.assertEqual(state.part, PART)
        self.assertEqual(len(supabase.functions.calls), 1)

    def test_manual_jobs_do_not_require_linked_state(self):
        export_job_id = "66666666-6666-4666-8666-666666666666"
        validation_job_id = "77777777-7777-4777-8777-777777777777"
        supabase = FakeSupabase([
            {"message": f"Queued export_cad. Job: {export_job_id}"},
            {"message": f"Queued validate_cad. Job: {validation_job_id}"},
        ])
        state = cad_agent_cli.CliState()

        export_message = cad_agent_cli.handle_command(
            supabase,
            state,
            cad_agent_cli.parse_command(f"/export {PART['id']}"),
        )
        validation_message = cad_agent_cli.handle_command(
            supabase,
            state,
            cad_agent_cli.parse_command(f"/validate {PART['id']}"),
        )

        self.assertIn(export_job_id, export_message)
        self.assertIn(validation_job_id, validation_message)
        self.assertEqual(
            supabase.functions.calls[0][1]["body"],
            {"action": "export_part", "part_id": PART["id"]},
        )
        self.assertEqual(
            supabase.functions.calls[1][1]["body"],
            {"action": "validate_part", "part_id": PART["id"]},
        )
        self.assertIsNone(state.project)
        self.assertIsNone(state.part)

    def test_index_build_and_getter_test_commands(self):
        build_job_id = "88888888-8888-4888-8888-888888888888"
        build_supabase = FakeSupabase([
            {"message": f"Index job queued. Job: {build_job_id}"},
        ])
        build_message = cad_agent_cli.handle_command(
            build_supabase,
            cad_agent_cli.CliState(),
            cad_agent_cli.parse_command(f"/index {PROJECT['id']}"),
        )
        self.assertIn(build_job_id, build_message)
        self.assertEqual(
            build_supabase.functions.calls[0][1]["body"],
            {"action": "index_project", "project_id": PROJECT["id"]},
        )

        test_job_id = "99999999-9999-4999-8999-999999999999"
        test_supabase = FakeSupabase([
            {"message": "queued", "job_id": test_job_id},
            {
                "message": "completed",
                "job": {
                    "status": "completed",
                    "result": {"status": "ok", "matches": []},
                },
            },
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())
        result = cad_agent_cli.handle_command(
            test_supabase,
            state,
            cad_agent_cli.parse_command("/index -test make holes larger"),
        )
        self.assertIn('"status": "ok"', result)
        self.assertEqual(
            test_supabase.functions.calls[0][1]["body"],
            {
                "action": "test_index",
                "project_id": PROJECT["id"],
                "request_text": "make holes larger",
            },
        )
        self.assertEqual(
            test_supabase.functions.calls[1][1]["body"]["action"],
            "get_index_job",
        )

    def test_edit_status_does_not_require_linked_state(self):
        supabase = FakeSupabase([
            {
                "message": "running",
                "job": {
                    "id": EDIT_JOB_ID,
                    "status": "running",
                    "state": "validating_candidate",
                    "attempt_count": 1,
                    "resolved_targets": ["mount_holes"],
                    "validation_job_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                },
            },
        ])

        result = cad_agent_cli.handle_command(
            supabase,
            cad_agent_cli.CliState(),
            cad_agent_cli.parse_command(f"/edit-status {EDIT_JOB_ID}"),
        )

        self.assertIn("validating_candidate", result)
        self.assertIn('"attempt_count": 1', result)
        self.assertEqual(
            supabase.functions.calls[0][1]["body"],
            {"action": "get_edit_job", "job_id": EDIT_JOB_ID},
        )


class ChatTests(unittest.TestCase):
    def test_chat_requires_linked_project(self):
        with self.assertRaisesRegex(cad_agent_cli.CommandError, "Link a project"):
            cad_agent_cli.send_chat(FakeSupabase([]), cad_agent_cli.CliState(), "add a hole")

    def test_cad_chat_requires_only_project(self):
        supabase = FakeSupabase([
            {"message": "queued", "job_id": "33333333-3333-4333-8333-333333333333"},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())
        message, job_id = cad_agent_cli.send_chat(supabase, state, "add a hole")

        self.assertEqual(message, "queued")
        self.assertTrue(job_id.startswith("33333333"))
        body = supabase.functions.calls[0][1]["body"]
        self.assertEqual(body["action"], "chat")
        self.assertEqual(body["project_id"], PROJECT["id"])
        self.assertNotIn("part_id", body)
        self.assertEqual(state.history[-1], {"role": "assistant", "content": "queued"})

    def test_linked_cad_part_does_not_constrain_project_scoped_edit(self):
        supabase = FakeSupabase([
            {"message": "queued", "job_id": "33333333-3333-4333-8333-333333333333"},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy(), part=PART.copy())

        cad_agent_cli.send_chat(supabase, state, "make the right bracket wider")

        body = supabase.functions.calls[0][1]["body"]
        self.assertNotIn("part_id", body)

    def test_mesh_chat_uses_the_same_part_scoped_action(self):
        supabase = FakeSupabase([
            {"message": "updated mesh", "job_id": "55555555-5555-4555-8555-555555555555"},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy(), part=MESH_PART.copy())

        message, _job_id = cad_agent_cli.send_chat(
            supabase,
            state,
            "make the horns longer",
        )

        self.assertEqual(message, "updated mesh")
        body = supabase.functions.calls[0][1]["body"]
        self.assertEqual(body["part_id"], MESH_PART["id"])
        self.assertEqual(state.history[-1]["role"], "assistant")


if __name__ == "__main__":
    unittest.main()
