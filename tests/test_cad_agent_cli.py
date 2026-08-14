from __future__ import annotations

import contextlib
import io
import json
import os
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


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, body):
        self.calls.append(body)
        if not self.responses:
            raise AssertionError("No fake response remains for this invocation")
        return self.responses.pop(0)


class FakeConversationClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def submit(self, body):
        self.calls.append(body)
        if not self.responses:
            raise AssertionError("No fake response remains for this submission")
        return self.responses.pop(0)


class CommandParsingTests(unittest.TestCase):
    def test_parses_all_supported_commands(self):
        cases = {
            "/create -project Desk Mount": ("create_project", "Desk Mount"),
            '/create -part -cad "Left Bracket"': ("create_part", "Left Bracket"),
            '/create -part -mesh "Dragon Body"': ("create_part", "Dragon Body"),
            f"/link -project {PROJECT['id']}": ("link_project", PROJECT["id"]),
            f"/link -part {PART['id']}": ("link_part", PART["id"]),
            "/list -projects": ("list_projects", ""),
            "/list -parts": ("list_parts", ""),
            f"/export {PART['id']}": ("export_part", PART["id"]),
            f"/validate {PART['id']}": ("validate_part", PART["id"]),
            f"/index {PROJECT['id']}": ("index_project", PROJECT["id"]),
            "/index -test make the mounting holes bigger": (
                "test_index",
                "make the mounting holes bigger",
            ),
            "/plan an adjustable phone stand with a rotating arm": (
                "plan_project",
                "an adjustable phone stand with a rotating arm",
            ),
            "/publish design-request-1": ("publish_assembly_revision", "design-request-1"),
            "/list -assemblies": ("list_assemblies", ""),
            "/list -assembly-revisions assembly-1": ("list_assembly_revisions", "assembly-1"),
            f"/edit-status {EDIT_JOB_ID}": ("get_edit_job", EDIT_JOB_ID),
            f"/delete -project {PROJECT['id']}": ("delete_project", PROJECT["id"]),
            f"/delete -part {PART['id']}": ("delete_part", PART["id"]),
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                command = cad_agent_cli.parse_command(source)
                self.assertEqual((command.action, command.name), expected)

        mesh_command = cad_agent_cli.parse_command('/create -part -mesh "Dragon Body"')
        self.assertEqual(mesh_command.part_type, "mesh")

        publish_with_assembly = cad_agent_cli.parse_command(
            "/publish design-request-1 -assembly assembly-1"
        )
        self.assertEqual(publish_with_assembly.action, "publish_assembly_revision")
        self.assertEqual(publish_with_assembly.name, "design-request-1")
        self.assertEqual(publish_with_assembly.assembly_id, "assembly-1")

        plan_auto_publish = cad_agent_cli.parse_command(
            "/plan -auto-publish -assembly assembly-1 a two-part phone stand"
        )
        self.assertEqual(plan_auto_publish.action, "plan_project")
        self.assertEqual(plan_auto_publish.name, "a two-part phone stand")
        self.assertTrue(plan_auto_publish.auto_publish)
        self.assertEqual(plan_auto_publish.assembly_id, "assembly-1")

        plan_default = cad_agent_cli.parse_command("/plan a two-part phone stand")
        self.assertFalse(plan_default.auto_publish)
        self.assertIsNone(plan_default.assembly_id)

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
            cad_agent_cli.parse_command("/plan")
        with self.assertRaises(cad_agent_cli.CommandError):
            cad_agent_cli.parse_command("/edit-status")


class LinkedStateTests(unittest.TestCase):
    def test_startup_auto_links_first_project_or_stays_unlinked(self):
        state = cad_agent_cli.CliState()
        client = FakeClient([{"projects": [PROJECT, {"id": PART["id"], "project_name": "Other"}]}])

        message = cad_agent_cli.auto_link_first_project(client, state)

        self.assertEqual(state.project, PROJECT)
        self.assertIn(PROJECT["id"], message)
        self.assertEqual(client.calls, [{"action": "list_projects"}])

        empty_state = cad_agent_cli.CliState()
        empty_message = cad_agent_cli.auto_link_first_project(
            FakeClient([{"projects": []}]),
            empty_state,
        )
        self.assertIsNone(empty_state.project)
        self.assertIn("unlinked mode", empty_message)

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
        client = FakeClient([
            {"message": "created project", "project": PROJECT},
            {"message": "created part", "part": PART},
        ])
        state = cad_agent_cli.CliState()

        cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command("/create -project Desk Mount"),
        )
        self.assertEqual(state.project, PROJECT)

        cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command("/create -part -cad Left Bracket"),
        )
        self.assertEqual(state.part, PART)

    def test_link_commands_send_durable_ids(self):
        client = FakeClient([
            {"message": "linked project", "project": PROJECT},
            {"message": "linked part", "part": PART},
        ])
        state = cad_agent_cli.CliState()

        cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command(f"/link -project {PROJECT['id']}"),
        )
        cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command(f"/link -part {PART['id']}"),
        )

        self.assertEqual(
            client.calls,
            [
                {"action": "link_project", "project_id": PROJECT["id"]},
                {
                    "action": "link_part",
                    "project_id": PROJECT["id"],
                    "part_id": PART["id"],
                },
            ],
        )

    def test_part_operation_requires_linked_project(self):
        state = cad_agent_cli.CliState()
        with self.assertRaisesRegex(cad_agent_cli.CommandError, "linked project"):
            cad_agent_cli.handle_command(
                FakeClient([]),
                state,
                cad_agent_cli.parse_command(f"/link -part {PART['id']}"),
            )

        with self.assertRaisesRegex(cad_agent_cli.CommandError, "linked project"):
            cad_agent_cli.handle_command(
                FakeClient([]),
                state,
                cad_agent_cli.parse_command("/list -parts"),
            )

    def test_list_projects_preserves_linked_state_and_history(self):
        client = FakeClient([{"message": "Projects:\n- Desk Mount"}])
        history = [{"role": "user", "content": "keep this"}]
        state = cad_agent_cli.CliState(
            project=PROJECT.copy(),
            part=PART.copy(),
            history=history.copy(),
        )

        result = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command("/list -projects"),
        )

        self.assertEqual(result, "Projects:\n- Desk Mount")
        self.assertEqual(state.project, PROJECT)
        self.assertEqual(state.part, PART)
        self.assertEqual(state.history, history)
        self.assertEqual(
            client.calls[0],
            {"action": "list_projects"},
        )

    def test_list_parts_scopes_request_to_linked_project(self):
        client = FakeClient([
            {"message": 'Parts in "Desk Mount":\n- Left Bracket [cad]'},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy(), part=PART.copy())

        result = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command("/list -parts"),
        )

        self.assertIn("Left Bracket [cad]", result)
        self.assertEqual(
            client.calls[0],
            {"action": "list_parts", "project_id": PROJECT["id"]},
        )
        self.assertEqual(state.part, PART)

    def test_creating_mesh_part_auto_links_and_clears_history(self):
        client = FakeClient([{"message": "created mesh", "part": MESH_PART}])
        state = cad_agent_cli.CliState(
            project=PROJECT.copy(),
            part=PART.copy(),
            history=[{"role": "user", "content": "old CAD context"}],
        )

        cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command('/create -part -mesh "Dragon Body"'),
        )

        self.assertEqual(state.part, MESH_PART)
        self.assertEqual(state.history, [])
        body = client.calls[0]
        self.assertEqual(body["part_type"], "mesh")

    def test_cancelled_delete_does_not_mutate(self):
        client = FakeClient([
            {"message": "resolved", "part": PART},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy(), part=PART.copy())
        result = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command(f"/delete -part {PART['id']}"),
            read_input=lambda _prompt: "n",
        )
        self.assertEqual(result, "Part deletion cancelled.")
        self.assertEqual(state.part, PART)
        self.assertEqual(len(client.calls), 1)

    def test_confirmed_part_delete_uses_id_and_clears_matching_state(self):
        prompts = []
        client = FakeClient([
            {"message": "resolved", "part": PART},
            {"message": "deleted", "part": PART},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy(), part=PART.copy())

        result = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command(f"/delete -part {PART['id']}"),
            read_input=lambda prompt: prompts.append(prompt) or "y",
        )

        self.assertEqual(result, "deleted")
        self.assertIsNone(state.part)
        self.assertIn(PART["part_name"], prompts[0])
        self.assertIn(PART["id"], prompts[0])
        self.assertEqual(
            client.calls[1],
            {
                "action": "delete_part",
                "project_id": PROJECT["id"],
                "part_id": PART["id"],
            },
        )

    def test_manual_jobs_do_not_require_linked_state(self):
        export_job_id = "66666666-6666-4666-8666-666666666666"
        validation_job_id = "77777777-7777-4777-8777-777777777777"
        client = FakeClient([
            {"message": f"Queued export_cad. Job: {export_job_id}"},
            {"message": f"Queued validate_cad. Job: {validation_job_id}"},
        ])
        state = cad_agent_cli.CliState()

        export_message = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command(f"/export {PART['id']}"),
        )
        validation_message = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command(f"/validate {PART['id']}"),
        )

        self.assertIn(export_job_id, export_message)
        self.assertIn(validation_job_id, validation_message)
        self.assertEqual(
            client.calls[0],
            {"action": "export_part", "part_id": PART["id"]},
        )
        self.assertEqual(
            client.calls[1],
            {"action": "validate_part", "part_id": PART["id"]},
        )
        self.assertIsNone(state.project)
        self.assertIsNone(state.part)

    def test_index_build_and_getter_test_commands(self):
        build_job_id = "88888888-8888-4888-8888-888888888888"
        build_supabase = FakeClient([
            {"message": f"Index job queued. Job: {build_job_id}"},
        ])
        build_message = cad_agent_cli.handle_command(
            build_supabase,
            cad_agent_cli.CliState(),
            cad_agent_cli.parse_command(f"/index {PROJECT['id']}"),
        )
        self.assertIn(build_job_id, build_message)
        self.assertEqual(
            build_supabase.calls[0],
            {"action": "index_project", "project_id": PROJECT["id"]},
        )

        test_job_id = "99999999-9999-4999-8999-999999999999"
        test_supabase = FakeClient([
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
            test_supabase.calls[0],
            {
                "action": "test_index",
                "project_id": PROJECT["id"],
                "request_text": "make holes larger",
            },
        )
        self.assertEqual(
            test_supabase.calls[1]["action"],
            "get_index_job",
        )

    def test_plan_command_submits_and_prints_completed_plan(self):
        plan_job_id = "77777777-7777-4777-8777-777777777777"
        client = FakeClient([
            {"message": "queued", "job_id": plan_job_id},
            {
                "message": "completed",
                "job": {
                    "status": "completed",
                    "project_plan": {"summary": "A two-part phone stand.", "parts": []},
                    "assembly_spec": {"nodes": []},
                },
            },
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())

        result = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command("/plan an adjustable phone stand"),
        )

        self.assertIn("Project plan completed", result)
        self.assertIn('"summary": "A two-part phone stand."', result)
        self.assertIn('"nodes": []', result)
        self.assertEqual(
            client.calls[0],
            {
                "action": "plan_project",
                "project_id": PROJECT["id"],
                "request_text": "an adjustable phone stand",
            },
        )
        self.assertEqual(client.calls[1]["action"], "get_project_plan")

    def test_plan_command_with_auto_publish_sends_flags_and_prints_revision(self):
        plan_job_id = "88888888-8888-4888-8888-888888888888"
        client = FakeClient([
            {"message": "queued", "job_id": plan_job_id},
            {
                "message": "completed",
                "job": {
                    "status": "completed",
                    "project_plan": {"summary": "A two-part phone stand.", "parts": []},
                    "assembly_spec": {"nodes": []},
                },
                "publish": {
                    "status": "completed",
                    "assembly_revision": {"assembly_id": "assembly-1", "revision": 1},
                },
            },
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())

        result = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command(
                "/plan -auto-publish -assembly assembly-1 an adjustable phone stand"
            ),
        )

        self.assertIn("Project plan completed", result)
        self.assertIn("publish (auto)", result)
        self.assertIn('"revision": 1', result)
        self.assertEqual(
            client.calls[0],
            {
                "action": "plan_project",
                "project_id": PROJECT["id"],
                "request_text": "an adjustable phone stand",
                "auto_publish": True,
                "assembly_id": "assembly-1",
            },
        )

    def test_plan_command_reports_clarification_failure(self):
        plan_job_id = "66666666-6666-4666-8666-666666666666"
        client = FakeClient([
            {"message": "queued", "job_id": plan_job_id},
            {
                "message": "failed",
                "job": {
                    "status": "failed",
                    "error_code": "PROJECT_CLARIFICATION_REQUIRED",
                    "error_message": "Should the lid be removable or permanently attached?",
                    "project_plan": None,
                },
            },
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())

        result = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command("/plan make the lid attached"),
        )

        self.assertIn("Project plan failed", result)
        self.assertIn("PROJECT_CLARIFICATION_REQUIRED", result)
        self.assertIn("removable or permanently attached", result)

    def test_plan_command_prints_error_details_when_present(self):
        plan_job_id = "55555555-5555-4555-8555-555555555555"
        client = FakeClient([
            {"message": "queued", "job_id": plan_job_id},
            {
                "message": "failed",
                "job": {
                    "status": "failed",
                    "error_code": "PROJECT_PLAN_INVALID",
                    "error_message": "The project plan still had 1 unresolved violation(s).",
                    "project_plan": None,
                    "error_details": {
                        "violations": [
                            {
                                "code": "PROJECT_PLAN_INVALID_INTERFACE",
                                "message": 'Interface "x" connects a part to itself.',
                                "details": {"condition": "self_reference"},
                                "retryable": True,
                            }
                        ],
                        "attempts": [{"attempt": 0}],
                    },
                },
            },
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())

        result = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command("/plan a self-referencing widget"),
        )

        self.assertIn("--- error_details ---", result)
        self.assertIn("PROJECT_PLAN_INVALID_INTERFACE", result)
        self.assertIn("self_reference", result)

    def test_plan_command_requires_linked_project(self):
        client = FakeClient([])
        with self.assertRaises(cad_agent_cli.CommandError):
            cad_agent_cli.handle_command(
                client,
                cad_agent_cli.CliState(),
                cad_agent_cli.parse_command("/plan a flower pot"),
            )

    def test_publish_command_submits_and_prints_completed_revision(self):
        design_request_id = "88888888-8888-4888-8888-888888888888"
        publish_job_id = "99999999-9999-4999-9999-999999999999"
        assembly_id = "aaaaaaaa-1111-4111-8111-111111111111"
        client = FakeClient([
            {"message": "queued", "job_id": publish_job_id},
            {
                "message": "completed",
                "job": {
                    "status": "completed",
                    "assembly_revision": {
                        "revision_id": "rev-1",
                        "assembly_id": assembly_id,
                        "revision": 1,
                        "parent_revision": None,
                    },
                },
            },
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())

        result = cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command(f"/publish {design_request_id}"),
        )

        self.assertIn("Assembly revision published", result)
        self.assertIn('"revision": 1', result)
        self.assertEqual(
            client.calls[0],
            {
                "action": "publish_assembly_revision",
                "project_id": PROJECT["id"],
                "design_request_id": design_request_id,
            },
        )
        self.assertEqual(client.calls[1]["action"], "get_assembly_publish_job")

    def test_publish_command_includes_assembly_id_when_continuing(self):
        design_request_id = "88888888-8888-4888-8888-888888888888"
        assembly_id = "aaaaaaaa-1111-4111-8111-111111111111"
        client = FakeClient([
            {"message": "queued", "job_id": "job-1"},
            {"message": "completed", "job": {"status": "completed", "assembly_revision": {"revision": 2}}},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())

        cad_agent_cli.handle_command(
            client,
            state,
            cad_agent_cli.parse_command(f"/publish {design_request_id} -assembly {assembly_id}"),
        )

        self.assertEqual(
            client.calls[0],
            {
                "action": "publish_assembly_revision",
                "project_id": PROJECT["id"],
                "design_request_id": design_request_id,
                "assembly_id": assembly_id,
            },
        )

    def test_publish_command_reports_plan_invalid_with_error_details(self):
        client = FakeClient([
            {"message": "queued", "job_id": "job-1"},
            {
                "message": "failed",
                "job": {
                    "status": "failed",
                    "error_code": "ASSEMBLY_PUBLISH_PLAN_INVALID",
                    "error_message": "The project plan failed canonical validation with 1 violation(s).",
                    "error_details": {
                        "violations": [
                            {"code": "PROJECT_PLAN_EXECUTION_DEPENDENCY_CYCLE", "message": "cyclic"}
                        ]
                    },
                },
            },
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())

        result = cad_agent_cli.handle_command(
            client, state, cad_agent_cli.parse_command("/publish design-request-1")
        )

        self.assertIn("Assembly publish failed", result)
        self.assertIn("ASSEMBLY_PUBLISH_PLAN_INVALID", result)
        self.assertIn("PROJECT_PLAN_EXECUTION_DEPENDENCY_CYCLE", result)

    def test_publish_command_requires_linked_project(self):
        client = FakeClient([])
        with self.assertRaises(cad_agent_cli.CommandError):
            cad_agent_cli.handle_command(
                client,
                cad_agent_cli.CliState(),
                cad_agent_cli.parse_command("/publish design-request-1"),
            )

    def test_list_assemblies_and_assembly_revisions_commands(self):
        assembly_id = "aaaaaaaa-1111-4111-8111-111111111111"
        client = FakeClient([
            {
                "message": "Project has 1 assembly.",
                "assemblies": [{"id": assembly_id, "head_revision": 2}],
            },
            {
                "message": f"Assembly {assembly_id} has 2 revision(s).",
                "revisions": [{"revision": 1}, {"revision": 2}],
            },
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())

        assemblies_result = cad_agent_cli.handle_command(
            client, state, cad_agent_cli.parse_command("/list -assemblies")
        )
        self.assertIn("Project has 1 assembly.", assemblies_result)
        self.assertIn('"head_revision": 2', assemblies_result)
        self.assertEqual(
            client.calls[0], {"action": "list_assemblies", "project_id": PROJECT["id"]}
        )

        revisions_result = cad_agent_cli.handle_command(
            client, state, cad_agent_cli.parse_command(f"/list -assembly-revisions {assembly_id}")
        )
        self.assertIn("2 revision(s)", revisions_result)
        self.assertIn('"revision": 1', revisions_result)
        self.assertEqual(
            client.calls[1],
            {"action": "list_assembly_revisions", "project_id": PROJECT["id"], "assembly_id": assembly_id},
        )

    def test_edit_status_does_not_require_linked_state(self):
        client = FakeClient([
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
            client,
            cad_agent_cli.CliState(),
            cad_agent_cli.parse_command(f"/edit-status {EDIT_JOB_ID}"),
        )

        self.assertIn("validating_candidate", result)
        self.assertIn('"attempt_count": 1', result)
        self.assertEqual(
            client.calls[0],
            {"action": "get_edit_job", "job_id": EDIT_JOB_ID},
        )


class ChatTests(unittest.TestCase):
    def test_chat_requires_linked_project(self):
        with self.assertRaisesRegex(cad_agent_cli.CommandError, "Link a project"):
            cad_agent_cli.send_chat(
                FakeConversationClient([]),
                cad_agent_cli.CliState(),
                "add a hole",
            )

    def test_cad_chat_requires_only_project(self):
        client = FakeConversationClient([
            {
                "message": "CAD edit request queued.",
                "event": "cad.edit.accepted",
                "job_id": EDIT_JOB_ID,
            },
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy())
        message, job_id = cad_agent_cli.send_chat(client, state, "add a hole")

        self.assertEqual(message, "CAD edit request queued.")
        self.assertEqual(job_id, EDIT_JOB_ID)
        body = client.calls[0]
        self.assertEqual(body["project_id"], PROJECT["id"])
        self.assertEqual(body["request_text"], "add a hole")
        self.assertNotIn("action", body)
        self.assertNotIn("part_id", body)
        self.assertEqual(
            state.history[-1],
            {"role": "assistant", "content": "CAD edit request queued."},
        )

    def test_linked_cad_part_is_sent_as_the_authoritative_edit_target(self):
        client = FakeConversationClient([
            {"message": "queued", "job_id": "33333333-3333-4333-8333-333333333333"},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy(), part=PART.copy())

        cad_agent_cli.send_chat(client, state, "make the right bracket wider")

        body = client.calls[0]
        self.assertEqual(body["part_id"], PART["id"])
        self.assertIn("client_request_id", body)

    def test_mesh_chat_uses_the_same_part_scoped_action(self):
        client = FakeConversationClient([
            {"message": "updated mesh", "job_id": "55555555-5555-4555-8555-555555555555"},
        ])
        state = cad_agent_cli.CliState(project=PROJECT.copy(), part=MESH_PART.copy())

        message, _job_id = cad_agent_cli.send_chat(
            client,
            state,
            "make the horns longer",
        )

        self.assertEqual(message, "updated mesh")
        body = client.calls[0]
        self.assertEqual(body["part_id"], MESH_PART["id"])
        self.assertEqual(state.history[-1]["role"], "assistant")

    def test_websocket_client_submits_event_envelope_and_reads_job_ack(self):
        class Connection:
            def __init__(self):
                self.sent = []
                self.closed = False

            def send(self, value):
                self.sent.append(json.loads(value))

            def recv(self):
                return json.dumps({
                    "event": "cad.edit.accepted",
                    "data": {
                        "status": "queued",
                        "state": "received",
                        "job_id": EDIT_JOB_ID,
                    },
                })

            def close(self):
                self.closed = True

        connection = Connection()
        client = cad_agent_cli.CadAgentWebSocketClient(
            "ws://cad-agent.test/v1/cad-edits/ws",
            timeout_seconds=42,
            connection_factory=lambda *_args, **_kwargs: connection,
        )

        payload = client.submit({"project_id": PROJECT["id"], "request_text": "add a hole"})

        self.assertEqual(connection.sent[0]["event"], "cad.edit.submit")
        self.assertEqual(connection.sent[0]["data"]["request_text"], "add a hole")
        self.assertEqual(payload["event"], "cad.edit.accepted")
        self.assertEqual(payload["job_id"], EDIT_JOB_ID)
        self.assertEqual(payload["message"], "CAD edit request accepted.")
        self.assertTrue(connection.closed)

    def test_websocket_client_surfaces_agent_errors(self):
        class Connection:
            def send(self, _value):
                pass

            def recv(self):
                return json.dumps({
                    "event": "cad.edit.error",
                    "data": {"code": "INVALID_REQUEST", "message": "request rejected"},
                })

            def close(self):
                pass

        client = cad_agent_cli.CadAgentWebSocketClient(
            "ws://cad-agent.test/v1/cad-edits/ws",
            connection_factory=lambda *_args, **_kwargs: Connection(),
        )
        with self.assertRaisesRegex(RuntimeError, "request rejected"):
            client.submit({"project_id": PROJECT["id"], "request_text": "bad"})


class LiveProgressTests(unittest.TestCase):
    def test_uses_websocket_default_without_an_http_override(self):
        previous_ws = os.environ.pop("CAD_AGENT_WS_URL", None)
        previous_http = os.environ.pop("CAD_AGENT_HTTP_URL", None)
        try:
            self.assertEqual(
                cad_agent_cli.resolve_websocket_url(),
                cad_agent_cli.DEFAULT_CAD_AGENT_WS_URL,
            )
        finally:
            if previous_ws is not None:
                os.environ["CAD_AGENT_WS_URL"] = previous_ws
            if previous_http is not None:
                os.environ["CAD_AGENT_HTTP_URL"] = previous_http

    def test_derives_websocket_url_from_http_url(self):
        self.assertEqual(
            cad_agent_cli.resolve_websocket_url("https://cad.example.test/base"),
            "wss://cad.example.test/v1/cad-edits/ws",
        )

    def test_websocket_subscription_replays_and_acknowledges_ordered_progress(self):
        class FakeConnection:
            def __init__(self):
                self.sent: list[dict] = []
                self.closed = False
                self.messages = [
                    {
                        "event": "cad.edit.snapshot",
                        "data": {
                            "job": {"status": "running"},
                            "events": [
                                {
                                    "sequence": 1,
                                    "event_type": "job.queued",
                                    "message": "CAD edit request queued.",
                                }
                            ],
                        },
                    },
                    {
                        "event": "cad.edit.progress",
                        "data": {
                            "sequence": 2,
                            "event_type": "job.completed",
                            "message": "CAD edit completed.",
                        },
                    },
                ]

            def send(self, value: str) -> None:
                self.sent.append(json.loads(value))

            def recv(self) -> str:
                return json.dumps(self.messages.pop(0))

            def close(self) -> None:
                self.closed = True

        connection = FakeConnection()
        websocket = types.ModuleType("websocket")
        websocket.create_connection = lambda *_args, **_kwargs: connection
        previous = sys.modules.get("websocket")
        sys.modules["websocket"] = websocket
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                cad_agent_cli.follow_edit_job(
                    EDIT_JOB_ID,
                    websocket_url="ws://cad-agent.test/v1/cad-edits/ws",
                )
        finally:
            if previous is None:
                sys.modules.pop("websocket", None)
            else:
                sys.modules["websocket"] = previous

        self.assertEqual(connection.sent[0]["event"], "cad.edit.subscribe")
        self.assertEqual(
            connection.sent[0]["data"],
            {"job_id": EDIT_JOB_ID, "after_sequence": 0},
        )
        self.assertEqual(connection.sent[1]["event"], "cad.edit.ack")
        self.assertEqual(connection.sent[1]["data"]["sequence"], 2)
        self.assertTrue(connection.closed)
        self.assertIn("CAD edit request queued.", output.getvalue())
        self.assertIn("CAD edit completed.", output.getvalue())

    def test_reconnect_resumes_after_last_sequence(self):
        class FakeConnection:
            def __init__(self, messages):
                self.messages = list(messages)
                self.sent = []
                self.closed = False

            def send(self, value):
                self.sent.append(json.loads(value))

            def recv(self):
                value = self.messages.pop(0)
                if isinstance(value, Exception):
                    raise value
                return json.dumps(value)

            def close(self):
                self.closed = True

        first = FakeConnection([
            {
                "event": "cad.edit.progress",
                "data": {"sequence": 4, "event_type": "planning", "message": "planning"},
            },
            RuntimeError("socket lost"),
        ])
        second = FakeConnection([
            {
                "event": "cad.edit.snapshot",
                "data": {"job": {"status": "completed"}, "events": []},
            },
        ])
        connections = [first, second]
        websocket = types.ModuleType("websocket")
        websocket.create_connection = lambda *_args, **_kwargs: connections.pop(0)
        previous = sys.modules.get("websocket")
        sys.modules["websocket"] = websocket
        try:
            cad_agent_cli.follow_edit_job(
                EDIT_JOB_ID,
                websocket_url="ws://cad-agent.test/v1/cad-edits/ws",
                sleep=lambda _seconds: None,
            )
        finally:
            if previous is None:
                sys.modules.pop("websocket", None)
            else:
                sys.modules["websocket"] = previous

        self.assertEqual(second.sent[0]["data"]["after_sequence"], 4)
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)


class HttpClientTests(unittest.TestCase):
    def test_posts_json_with_timeout_and_closes_session(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"message": "ok"}

        class Session:
            def __init__(self):
                self.calls = []
                self.closed = False

            def post(self, url, *, json, timeout):
                self.calls.append((url, json, timeout))
                return Response()

            def close(self):
                self.closed = True

        session = Session()
        client = cad_agent_cli.CadAgentHttpClient(
            "http://cad-agent.test/",
            timeout_seconds=42,
            session=session,
        )

        self.assertEqual(client.invoke({"action": "list_projects"}), {"message": "ok"})
        self.assertEqual(
            session.calls[0],
            (
                "http://cad-agent.test/v1/cad-agent/actions",
                {"action": "list_projects"},
                42,
            ),
        )
        client.close()
        self.assertTrue(session.closed)

    def test_surfaces_backend_error_message(self):
        class Response:
            status_code = 404

            @staticmethod
            def json():
                return {"error": "project missing"}

        class Session:
            def post(self, *_args, **_kwargs):
                return Response()

            def close(self):
                pass

        client = cad_agent_cli.CadAgentHttpClient("http://cad-agent.test", session=Session())
        with self.assertRaisesRegex(RuntimeError, "project missing"):
            client.invoke({"action": "link_project", "project_id": PROJECT["id"]})


if __name__ == "__main__":
    unittest.main()
