from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal


ServicePromptName = Literal[
    "goal-creation", "planning", "cad-system", "agent-reasoning"
]

PROMPT_DIRECTORY = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=None)
def _read_prompt(path: str) -> str:
    prompt_path = Path(path)
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"Prompt file is empty: {prompt_path}")
    return prompt


def load_service_prompt(name: ServicePromptName) -> str:
    return _read_prompt(str((PROMPT_DIRECTORY / f"{name}.md").resolve()))


def load_goal_creation_prompt() -> str:
    return load_service_prompt("goal-creation")


def load_planning_prompt() -> str:
    return load_service_prompt("planning")


def load_cad_system_prompt() -> str:
    return load_service_prompt("cad-system")


def load_agent_reasoning_prompt() -> str:
    return load_service_prompt("agent-reasoning")
