"""Terminal client for project- and part-scoped 3D model generation."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
SUPABASE_FUNCTION_NAME = "cad-agent"
MAX_HISTORY_MESSAGES = 8
INDEX_TEST_TIMEOUT_SECONDS = 60.0
INDEX_TEST_POLL_INTERVAL_SECONDS = 0.5


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class CliCommand:
    action: str
    target: str
    name: str = ""
    part_type: str | None = None


@dataclass
class CliState:
    project: dict[str, str] | None = None
    part: dict[str, str] | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    def link_project(self, project: dict[str, str]) -> None:
        self.project = project
        self.part = None
        self.history.clear()

    def link_part(self, part: dict[str, str]) -> None:
        self.part = part
        self.history.clear()

    def clear_project(self) -> None:
        self.project = None
        self.part = None
        self.history.clear()

    def clear_part(self) -> None:
        self.part = None
        self.history.clear()

    def prompt(self) -> str:
        project_name = self.project["project_name"] if self.project else "<unlinked>"
        part_name = self.part["part_name"] if self.part else "<unlinked>"
        return f"you [{project_name}/{part_name}]> "


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat with the 3D model agent.")
    return parser.parse_args()


def parse_command(value: str) -> CliCommand:
    try:
        tokens = shlex.split(value)
    except ValueError as exc:
        raise CommandError(f"Invalid command quoting: {exc}") from exc

    if not tokens or not tokens[0].startswith("/"):
        raise CommandError("Commands must begin with `/`.")

    verb = tokens[0]
    if verb == "/create" and len(tokens) >= 3 and tokens[1] == "-project":
        return CliCommand("create_project", "project", _command_name(tokens, 2))
    if (
        verb == "/create"
        and len(tokens) >= 4
        and tokens[1] == "-part"
        and tokens[2] in {"-cad", "-mesh"}
    ):
        return CliCommand(
            "create_part",
            "part",
            _command_name(tokens, 3),
            tokens[2].removeprefix("-"),
        )
    if verb == "/link" and len(tokens) >= 3 and tokens[1] == "-project":
        return CliCommand("link_project", "project", _command_name(tokens, 2))
    if verb == "/link" and len(tokens) >= 3 and tokens[1] == "-part":
        return CliCommand("link_part", "part", _command_name(tokens, 2))
    if verb == "/list" and tokens == ["/list", "-projects"]:
        return CliCommand("list_projects", "project")
    if verb == "/list" and tokens == ["/list", "-parts"]:
        return CliCommand("list_parts", "part")
    if verb == "/export" and len(tokens) == 2:
        return CliCommand("export_part", "part", tokens[1])
    if verb == "/validate" and len(tokens) == 2:
        return CliCommand("validate_part", "part", tokens[1])
    if verb == "/index" and len(tokens) >= 3 and tokens[1] == "-test":
        return CliCommand("test_index", "index", _command_name(tokens, 2))
    if verb == "/index" and len(tokens) == 2 and tokens[1] != "-test":
        return CliCommand("index_project", "index", tokens[1])
    if verb == "/delete" and len(tokens) >= 3 and tokens[1] == "-project":
        return CliCommand("delete_project", "project", _command_name(tokens, 2))
    if verb == "/delete" and len(tokens) >= 3 and tokens[1] == "-part":
        return CliCommand("delete_part", "part", _command_name(tokens, 2))

    raise CommandError(
        "Unknown command. Use `/create -project <name>`, "
        "`/create -part -cad <name>`, `/create -part -mesh <name>`, "
        "`/link -project <name>`, "
        "`/link -part <name>`, `/list -projects`, `/list -parts`, "
        "`/export <partId>`, `/validate <partId>`, "
        "`/index <projectId>`, `/index -test <request>`, "
        "`/delete -project <name>`, or "
        "`/delete -part <name>`."
    )


def _command_name(tokens: list[str], start: int) -> str:
    name = " ".join(tokens[start:]).strip()
    if not name:
        raise CommandError("A non-empty project or part name is required.")
    return name


def create_supabase_client():
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not supabase_url:
        raise RuntimeError("Missing SUPABASE_URL. Add it to .env or export it.")

    supabase_key = os.environ.get("SUPABASE_ANON_KEY")
    if not supabase_key:
        raise RuntimeError("Missing SUPABASE_ANON_KEY. Add it to .env or export it.")

    try:
        from supabase import create_client
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "Install the Supabase Python client first: python -m pip install supabase"
        ) from exc
    return create_client(supabase_url, supabase_key)


def invoke_action(supabase: Any, body: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = supabase.functions.invoke(
            SUPABASE_FUNCTION_NAME,
            invoke_options={"body": body, "responseType": "json"},
        )
    except Exception as exc:
        raise RuntimeError(f"Supabase function invocation failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Supabase response must be a JSON object.")
    if isinstance(payload.get("error"), str):
        raise RuntimeError(payload["error"])
    return payload


def response_message(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if not isinstance(message, str):
        raise RuntimeError("Supabase response must include a string `message` field.")
    return message


def response_project(payload: dict[str, Any]) -> dict[str, str]:
    project = payload.get("project")
    if not isinstance(project, dict):
        raise RuntimeError("Supabase response must include a project object.")
    project_id = project.get("id")
    project_name = project.get("project_name")
    if not isinstance(project_id, str) or not isinstance(project_name, str):
        raise RuntimeError("Supabase project response is malformed.")
    return {"id": project_id, "project_name": project_name}


def response_part(payload: dict[str, Any]) -> dict[str, str]:
    part = payload.get("part")
    if not isinstance(part, dict):
        raise RuntimeError("Supabase response must include a part object.")
    required = ("id", "project_id", "part_name", "part_type")
    if any(not isinstance(part.get(field_name), str) for field_name in required):
        raise RuntimeError("Supabase part response is malformed.")
    return {field_name: part[field_name] for field_name in required}


def confirm_delete(
    label: str,
    name: str,
    read_input: Callable[[str], str] = input,
) -> bool:
    answer = read_input(f'Delete {label} "{name}"? [y/N] ').strip().lower()
    return answer == "y"


def response_job_id(payload: dict[str, Any]) -> str:
    job_id = payload.get("job_id")
    if not isinstance(job_id, str):
        raise RuntimeError("Supabase response must include a string `job_id` field.")
    return job_id


def wait_for_index_job(
    supabase: Any,
    project_id: str,
    job_id: str,
    *,
    timeout_seconds: float = INDEX_TEST_TIMEOUT_SECONDS,
    poll_interval_seconds: float = INDEX_TEST_POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> str:
    deadline = monotonic() + timeout_seconds
    while True:
        payload = invoke_action(
            supabase,
            {
                "action": "get_index_job",
                "project_id": project_id,
                "job_id": job_id,
            },
        )
        job = payload.get("job")
        if not isinstance(job, dict):
            raise RuntimeError("Supabase response must include an index job object.")
        status = job.get("status")
        if not isinstance(status, str):
            raise RuntimeError("Index job response must include a string status.")

        if status == "completed":
            result = job.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Completed index job must include a JSON result.")
            return (
                f"Index Getter test completed. Job: {job_id}\n"
                f"{json.dumps(result, indent=2, sort_keys=True)}"
            )
        if status in {"failed", "cancelled"}:
            error = job.get("error_message")
            detail = error if isinstance(error, str) and error else "No error detail."
            return f"Index Getter test {status}. Job: {job_id}\n{detail}"

        now = monotonic()
        if now >= deadline:
            return (
                f"Index Getter test is still {status} after "
                f"{timeout_seconds:g} seconds. Job: {job_id}"
            )
        sleep(min(poll_interval_seconds, deadline - now))


def handle_command(
    supabase: Any,
    state: CliState,
    command: CliCommand,
    read_input: Callable[[str], str] = input,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    index_timeout_seconds: float = INDEX_TEST_TIMEOUT_SECONDS,
) -> str:
    if command.action == "create_project":
        payload = invoke_action(
            supabase,
            {"action": "create_project", "project_name": command.name},
        )
        state.link_project(response_project(payload))
        return response_message(payload)

    if command.action == "link_project":
        payload = invoke_action(
            supabase,
            {"action": "link_project", "project_name": command.name},
        )
        state.link_project(response_project(payload))
        return response_message(payload)

    if command.action == "list_projects":
        payload = invoke_action(supabase, {"action": "list_projects"})
        return response_message(payload)

    if command.action in {"export_part", "validate_part"}:
        payload = invoke_action(
            supabase,
            {"action": command.action, "part_id": command.name},
        )
        return response_message(payload)

    if command.action == "index_project":
        payload = invoke_action(
            supabase,
            {"action": "index_project", "project_id": command.name},
        )
        return response_message(payload)

    if command.action == "test_index":
        if not state.project:
            raise CommandError("A linked project is required to test the index.")
        payload = invoke_action(
            supabase,
            {
                "action": "test_index",
                "project_id": state.project["id"],
                "request_text": command.name,
            },
        )
        job_id = response_job_id(payload)
        return wait_for_index_job(
            supabase,
            state.project["id"],
            job_id,
            timeout_seconds=index_timeout_seconds,
            sleep=sleep,
            monotonic=monotonic,
        )

    if command.action in {"create_part", "link_part", "list_parts", "delete_part"}:
        if not state.project:
            raise CommandError("A linked project is required for part operations.")

    if command.action == "create_part":
        payload = invoke_action(
            supabase,
            {
                "action": "create_part",
                "project_id": state.project["id"],
                "part_name": command.name,
                "part_type": command.part_type,
            },
        )
        state.link_part(response_part(payload))
        return response_message(payload)

    if command.action == "link_part":
        payload = invoke_action(
            supabase,
            {
                "action": "link_part",
                "project_id": state.project["id"],
                "part_name": command.name,
            },
        )
        state.link_part(response_part(payload))
        return response_message(payload)

    if command.action == "list_parts":
        payload = invoke_action(
            supabase,
            {
                "action": "list_parts",
                "project_id": state.project["id"],
            },
        )
        return response_message(payload)

    if command.action == "delete_project":
        resolved = invoke_action(
            supabase,
            {"action": "link_project", "project_name": command.name},
        )
        project = response_project(resolved)
        if not confirm_delete("project", project["project_name"], read_input):
            return "Project deletion cancelled."
        payload = invoke_action(
            supabase,
            {"action": "delete_project", "project_name": project["project_name"]},
        )
        deleted = response_project(payload)
        if state.project and state.project["id"] == deleted["id"]:
            state.clear_project()
        return response_message(payload)

    if command.action == "delete_part":
        resolved = invoke_action(
            supabase,
            {
                "action": "link_part",
                "project_id": state.project["id"],
                "part_name": command.name,
            },
        )
        part = response_part(resolved)
        if not confirm_delete("part", part["part_name"], read_input):
            return "Part deletion cancelled."
        payload = invoke_action(
            supabase,
            {
                "action": "delete_part",
                "project_id": state.project["id"],
                "part_name": part["part_name"],
            },
        )
        deleted = response_part(payload)
        if state.part and state.part["id"] == deleted["id"]:
            state.clear_part()
        return response_message(payload)

    raise CommandError("Unsupported command.")


def send_chat(supabase: Any, state: CliState, user_message: str) -> tuple[str, str]:
    if not state.project or not state.part:
        raise CommandError("Link a project and part before sending AI messages.")

    request_messages = [
        *state.history[-(MAX_HISTORY_MESSAGES - 1):],
        {"role": "user", "content": user_message},
    ]
    payload = invoke_action(
        supabase,
        {
            "action": "chat",
            "project_id": state.project["id"],
            "part_id": state.part["id"],
            "messages": request_messages,
        },
    )
    message = response_message(payload)
    job_id = response_job_id(payload)

    state.history = [
        *request_messages,
        {"role": "assistant", "content": message},
    ]
    return message, job_id


def main() -> int:
    load_dotenv(ENV_PATH)
    parse_args()
    try:
        supabase = create_supabase_client()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    state = CliState()
    print("3D Model Agent")
    print(f"Supabase function: {SUPABASE_FUNCTION_NAME}")
    print("Use /create or /link to select a project and CAD or mesh part.")
    print("Use /list -projects or /list -parts to see available links.")
    print("Use /export <partId> or /validate <partId> to queue manual jobs.")
    print("Use /index <projectId> to index CAD parts in a project.")
    print("Use /index -test <request> to test the linked project's Getter.")
    print("Type `exit` or `quit` to stop.\n")

    while True:
        try:
            value = input(state.prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not value:
            continue
        if value.lower() in {"exit", "quit"}:
            return 0

        try:
            if value.startswith("/"):
                print(f"agent> {handle_command(supabase, state, parse_command(value))}\n")
            else:
                message, job_id = send_chat(supabase, state, value)
                print(f"agent> {message}")
                print(f"job> {job_id}\n")
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
