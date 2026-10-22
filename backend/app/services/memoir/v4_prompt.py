"""The single production prompt used by memoir voice interviews."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_PROMPT_DIR = Path(__file__).with_name("prompt_variants")


@lru_cache(maxsize=None)
def prompt_text(name: str) -> str:
    path = _PROMPT_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").rstrip()


def system_prompt() -> str:
    return prompt_text("v4_system")


def user_instruction() -> str:
    return prompt_text("v4_user")
