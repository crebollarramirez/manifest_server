"""Terminal client for project- and part-scoped 3D model generation."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
MAX_HISTORY_MESSAGES = 8
INDEX_TEST_TIMEOUT_SECONDS = 60.0
INDEX_TEST_POLL_INTERVAL_SECONDS = 0.5
DEFAULT_CAD_AGENT_HTTP_URL = "http://127.0.0.1:3000"
DEFAULT_CAD_AGENT_WS_URL = "ws://localhost:3010/v1/cad-edits/ws"
DEFAULT_HTTP_TIMEOUT_SECONDS = 120.0
PROGRESS_RECONNECT_ATTEMPTS = 3
PROGRESS_RECONNECT_BACKOFF_SECONDS = 0.5


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


class CadAgentHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        session: Any | None = None,
    ) -> None:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError(
                "Install CLI dependencies first: python -m pip install -r requirements.txt"
            ) from exc

        normalized = base_url.strip().rstrip("/")
        if not normalized:
            raise RuntimeError("CAD_AGENT_HTTP_URL must not be empty.")
        self.action_url = f"{normalized}/v1/cad-agent/actions"
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self._request_error = requests.RequestException

    def invoke(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                self.action_url,
                json=body,
                timeout=self.timeout_seconds,
            )
        except self._request_error as exc:
            raise RuntimeError(f"CAD Agent request failed: {exc}") from exc

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"CAD Agent returned non-JSON HTTP {response.status_code}."
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("CAD Agent response must be a JSON object.")
        if not 200 <= response.status_code < 300:
            detail = payload.get("error")
            if not isinstance(detail, str) or not detail:
                detail = f"HTTP {response.status_code}"
            raise RuntimeError(detail)
        if isinstance(payload.get("error"), str):
            raise RuntimeError(payload["error"])
        return payload

    def close(self) -> None:
        self.session.close()


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
    if verb == "/edit-status" and len(tokens) == 2:
        return CliCommand("get_edit_job", "edit", tokens[1])
    if verb == "/delete" and len(tokens) >= 3 and tokens[1] == "-project":
        return CliCommand("delete_project", "project", _command_name(tokens, 2))
    if verb == "/delete" and len(tokens) >= 3 and tokens[1] == "-part":
        return CliCommand("delete_part", "part", _command_name(tokens, 2))

    raise CommandError(
        "Unknown command. Use `/create -project <name>`, "
        "`/create -part -cad <name>`, `/create -part -mesh <name>`, "
        "`/link -project <projectId>`, "
        "`/link -part <partId>`, `/list -projects`, `/list -parts`, "
        "`/export <partId>`, `/validate <partId>`, "
        "`/index <projectId>`, `/index -test <request>`, "
        "`/edit-status <jobId>`, "
        "`/delete -project <projectId>`, or "
        "`/delete -part <partId>`."
    )


def _command_name(tokens: list[str], start: int) -> str:
    name = " ".join(tokens[start:]).strip()
    if not name:
        raise CommandError("A non-empty project or part name is required.")
    return name


def create_http_client() -> CadAgentHttpClient:
    base_url = os.environ.get("CAD_AGENT_HTTP_URL", DEFAULT_CAD_AGENT_HTTP_URL)
    raw_timeout = os.environ.get(
        "CAD_AGENT_HTTP_TIMEOUT_SECONDS",
        str(DEFAULT_HTTP_TIMEOUT_SECONDS),
    )
    try:
        timeout_seconds = float(raw_timeout)
    except ValueError as exc:
        raise RuntimeError("CAD_AGENT_HTTP_TIMEOUT_SECONDS must be a number.") from exc
    if timeout_seconds <= 0:
        raise RuntimeError("CAD_AGENT_HTTP_TIMEOUT_SECONDS must be greater than zero.")
    return CadAgentHttpClient(base_url, timeout_seconds=timeout_seconds)


def invoke_action(client: Any, body: dict[str, Any]) -> dict[str, Any]:
    payload = client.invoke(body)
    if not isinstance(payload, dict):
        raise RuntimeError("CAD Agent response must be a JSON object.")
    return payload


def response_message(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if not isinstance(message, str):
        raise RuntimeError("CAD Agent response must include a string `message` field.")
    return message


def response_project(payload: dict[str, Any]) -> dict[str, str]:
    project = payload.get("project")
    if not isinstance(project, dict):
        raise RuntimeError("CAD Agent response must include a project object.")
    project_id = project.get("id")
    project_name = project.get("project_name")
    if not isinstance(project_id, str) or not isinstance(project_name, str):
        raise RuntimeError("CAD Agent project response is malformed.")
    return {"id": project_id, "project_name": project_name}


def auto_link_first_project(client: Any, state: CliState) -> str:
    payload = invoke_action(client, {"action": "list_projects"})
    projects = payload.get("projects")
    if not isinstance(projects, list):
        raise RuntimeError("CAD Agent response must include a projects array.")
    if not projects:
        return (
            "No projects are available; the CLI is in unlinked mode. "
            "Use /create -project <name> or /link -project <projectId>."
        )
    project = response_project({"project": projects[0]})
    state.link_project(project)
    return (
        f'Automatically linked project "{project["project_name"]}" '
        f'(id={project["id"]}).'
    )


def response_part(payload: dict[str, Any]) -> dict[str, str]:
    part = payload.get("part")
    if not isinstance(part, dict):
        raise RuntimeError("CAD Agent response must include a part object.")
    required = ("id", "project_id", "part_name", "part_type")
    if any(not isinstance(part.get(field_name), str) for field_name in required):
        raise RuntimeError("CAD Agent part response is malformed.")
    return {field_name: part[field_name] for field_name in required}


def confirm_delete(
    label: str,
    name: str,
    resource_id: str,
    read_input: Callable[[str], str] = input,
) -> bool:
    answer = read_input(
        f'Delete {label} "{name}" (id={resource_id})? [y/N] '
    ).strip().lower()
    return answer == "y"


def response_job_id(payload: dict[str, Any]) -> str:
    job_id = payload.get("job_id")
    if not isinstance(job_id, str):
        raise RuntimeError("CAD Agent response must include a string `job_id` field.")
    return job_id


def format_edit_job(payload: dict[str, Any]) -> str:
    job = payload.get("job")
    if not isinstance(job, dict):
        raise RuntimeError("CAD Agent response must include a CAD edit job object.")
    job_id = job.get("id")
    status = job.get("status")
    state = job.get("state")
    if not all(isinstance(value, str) for value in (job_id, status, state)):
        raise RuntimeError("CAD edit job response is malformed.")
    return (
        f"CAD edit job {job_id}: {status} ({state})\n"
        f"{json.dumps(job, indent=2, sort_keys=True)}"
    )


def wait_for_index_job(
    client: Any,
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
            client,
            {
                "action": "get_index_job",
                "project_id": project_id,
                "job_id": job_id,
            },
        )
        job = payload.get("job")
        if not isinstance(job, dict):
            raise RuntimeError("CAD Agent response must include an index job object.")
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
    client: Any,
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
            client,
            {"action": "create_project", "project_name": command.name},
        )
        state.link_project(response_project(payload))
        return response_message(payload)

    if command.action == "link_project":
        payload = invoke_action(
            client,
            {"action": "link_project", "project_id": command.name},
        )
        state.link_project(response_project(payload))
        return response_message(payload)

    if command.action == "list_projects":
        payload = invoke_action(client, {"action": "list_projects"})
        return response_message(payload)

    if command.action in {"export_part", "validate_part"}:
        payload = invoke_action(
            client,
            {"action": command.action, "part_id": command.name},
        )
        return response_message(payload)

    if command.action == "index_project":
        payload = invoke_action(
            client,
            {"action": "index_project", "project_id": command.name},
        )
        return response_message(payload)

    if command.action == "get_edit_job":
        payload = invoke_action(
            client,
            {"action": "get_edit_job", "job_id": command.name},
        )
        return format_edit_job(payload)

    if command.action == "test_index":
        if not state.project:
            raise CommandError("A linked project is required to test the index.")
        payload = invoke_action(
            client,
            {
                "action": "test_index",
                "project_id": state.project["id"],
                "request_text": command.name,
            },
        )
        job_id = response_job_id(payload)
        return wait_for_index_job(
            client,
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
            client,
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
            client,
            {
                "action": "link_part",
                "project_id": state.project["id"],
                "part_id": command.name,
            },
        )
        state.link_part(response_part(payload))
        return response_message(payload)

    if command.action == "list_parts":
        payload = invoke_action(
            client,
            {
                "action": "list_parts",
                "project_id": state.project["id"],
            },
        )
        return response_message(payload)

    if command.action == "delete_project":
        resolved = invoke_action(
            client,
            {"action": "link_project", "project_id": command.name},
        )
        project = response_project(resolved)
        if not confirm_delete(
            "project", project["project_name"], project["id"], read_input
        ):
            return "Project deletion cancelled."
        payload = invoke_action(
            client,
            {"action": "delete_project", "project_id": project["id"]},
        )
        deleted = response_project(payload)
        if state.project and state.project["id"] == deleted["id"]:
            state.clear_project()
        return response_message(payload)

    if command.action == "delete_part":
        resolved = invoke_action(
            client,
            {
                "action": "link_part",
                "project_id": state.project["id"],
                "part_id": command.name,
            },
        )
        part = response_part(resolved)
        if not confirm_delete("part", part["part_name"], part["id"], read_input):
            return "Part deletion cancelled."
        payload = invoke_action(
            client,
            {
                "action": "delete_part",
                "project_id": state.project["id"],
                "part_id": part["id"],
            },
        )
        deleted = response_part(payload)
        if state.part and state.part["id"] == deleted["id"]:
            state.clear_part()
        return response_message(payload)

    raise CommandError("Unsupported command.")


def send_chat(client: Any, state: CliState, user_message: str) -> tuple[str, str]:
    if not state.project:
        raise CommandError("Link a project before sending AI messages.")

    request_messages = [
        *state.history[-(MAX_HISTORY_MESSAGES - 1):],
        {"role": "user", "content": user_message},
    ]
    body: dict[str, Any] = {
        "action": "chat",
        "project_id": state.project["id"],
        "messages": request_messages,
        "client_request_id": str(uuid.uuid4()),
    }
    if state.part:
        body["part_id"] = state.part["id"]
    payload = invoke_action(
        client,
        body,
    )
    message = response_message(payload)
    job_id = response_job_id(payload)

    state.history = [
        *request_messages,
        {"role": "assistant", "content": message},
    ]
    return message, job_id


def follow_edit_job(
    job_id: str,
    *,
    websocket_url: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Follow durable CAD progress; disconnecting never cancels the edit job."""

    try:
        import websocket
    except ImportError:
        print(
            "progress> websocket-client is not installed; use "
            f"/edit-status {job_id} instead.",
            file=sys.stderr,
        )
        return

    resolved_websocket_url = websocket_url or resolve_websocket_url()
    last_sequence = 0
    for attempt in range(PROGRESS_RECONNECT_ATTEMPTS + 1):
        connection = None
        try:
            connection = websocket.create_connection(resolved_websocket_url, timeout=10)
            connection.send(
                json.dumps(
                    {
                        "event": "cad.edit.subscribe",
                        "data": {
                            "job_id": job_id,
                            "after_sequence": last_sequence,
                        },
                    }
                )
            )
            while True:
                raw = connection.recv()
                if not isinstance(raw, str) or not raw:
                    raise RuntimeError("The progress WebSocket disconnected.")
                message = json.loads(raw)
                event_name = message.get("event")
                data = message.get("data")
                if event_name == "cad.edit.error":
                    detail = data if isinstance(data, dict) else {}
                    print(
                        f"progress error> {detail.get('code', 'ERROR')}: "
                        f"{detail.get('message', 'Unknown WebSocket error')}",
                        file=sys.stderr,
                    )
                    return
                if event_name == "cad.edit.snapshot" and isinstance(data, dict):
                    events = data.get("events")
                    if isinstance(events, list):
                        for progress in events:
                            if not isinstance(progress, dict):
                                continue
                            sequence = progress.get("sequence")
                            if isinstance(sequence, int):
                                last_sequence = max(last_sequence, sequence)
                            print(
                                f"progress[{sequence}]> "
                                f"{progress.get('message', progress.get('event_type'))}"
                            )
                    job = data.get("job")
                    if isinstance(job, dict) and job.get("status") in {
                        "completed",
                        "failed",
                        "cancelled",
                    }:
                        return
                elif event_name == "cad.edit.progress" and isinstance(data, dict):
                    sequence = data.get("sequence")
                    if isinstance(sequence, int):
                        last_sequence = max(last_sequence, sequence)
                    print(
                        f"progress[{sequence}]> "
                        f"{data.get('message', data.get('event_type'))}"
                    )
                    connection.send(
                        json.dumps(
                            {
                                "event": "cad.edit.ack",
                                "data": {
                                    "job_id": job_id,
                                    "sequence": last_sequence,
                                },
                            }
                        )
                    )
                    if data.get("event_type") in {
                        "job.completed",
                        "job.failed",
                        "job.cancelled",
                    }:
                        return
        except KeyboardInterrupt:
            print(
                f"\nprogress> disconnected; job {job_id} continues. "
                f"Use /edit-status {job_id}.",
                file=sys.stderr,
            )
            return
        except Exception as exc:
            if attempt >= PROGRESS_RECONNECT_ATTEMPTS:
                print(
                    f"progress> live updates unavailable ({exc}); "
                    f"use /edit-status {job_id}.",
                    file=sys.stderr,
                )
                return
            delay = PROGRESS_RECONNECT_BACKOFF_SECONDS * (2 ** attempt)
            print(
                f"progress> connection interrupted; retrying from sequence "
                f"{last_sequence} in {delay:g}s...",
                file=sys.stderr,
            )
            sleep(delay)
        finally:
            if connection is not None:
                connection.close()


def resolve_websocket_url(http_url: str | None = None) -> str:
    configured = os.environ.get("CAD_AGENT_WS_URL", "").strip()
    if configured:
        return configured
    base = (http_url or os.environ.get("CAD_AGENT_HTTP_URL", DEFAULT_CAD_AGENT_HTTP_URL)).strip()
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return DEFAULT_CAD_AGENT_WS_URL
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/v1/cad-edits/ws", "", ""))


def main() -> int:
    load_dotenv(ENV_PATH)
    parse_args()
    try:
        client = create_http_client()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    state = CliState()
    try:
        try:
            startup_message = auto_link_first_project(client, state)
        except Exception as exc:
            print(f"error: could not initialize the CLI: {exc}", file=sys.stderr)
            return 1

        print("3D Model Agent")
        print(f"Nest action API: {client.action_url}")
        print(startup_message)
        print("Use /create or /link to select a project and CAD or mesh part.")
        print("Use /list -projects or /list -parts to see available IDs.")
        print("Use /export <partId> or /validate <partId> to queue manual jobs.")
        print("Use /index <projectId> to index CAD parts in a project.")
        print("Use /index -test <request> to test the linked project's Getter.")
        print("Use /edit-status <jobId> to inspect a CAD edit workflow.")
        print("CAD requests require a project; mesh requests require a linked mesh part.")
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
                    print(f"agent> {handle_command(client, state, parse_command(value))}\n")
                else:
                    message, job_id = send_chat(client, state, value)
                    print(f"agent> {message}")
                    print(f"job> {job_id}")
                    if not state.part or state.part.get("part_type") == "cad":
                        follow_edit_job(job_id)
                    print()
            except Exception as exc:
                print(f"error: {exc}", file=sys.stderr)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
