"""Runtime-selectable interview prompt variants for device comparison."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

PromptVariant = Literal["v3_improved", "v4", "v3_plus"]
DEFAULT_PROMPT_VARIANT: PromptVariant = "v3_improved"
PROMPT_VARIANTS: tuple[PromptVariant, ...] = ("v3_improved", "v4", "v3_plus")
_PROMPT_VARIANT_DIR = Path(__file__).with_name("prompt_variants")


@lru_cache(maxsize=None)
def prompt_variant_text(name: str) -> str:
    path = _PROMPT_VARIANT_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").rstrip()


def normalize_prompt_variant(value: object) -> PromptVariant:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized == "standalone":
            return "v4"
        if normalized in PROMPT_VARIANTS:
            return normalized  # type: ignore[return-value]
    return DEFAULT_PROMPT_VARIANT


def variant_system_prompt(variant: PromptVariant) -> str:
    if variant == "v4":
        return prompt_variant_text("v4_system")
    if variant == "v3_plus":
        return prompt_variant_text("v3_plus_system")
    return ""


def variant_user_instruction(variant: PromptVariant) -> str:
    if variant == "v3_improved":
        return prompt_variant_text("grounded_v3")
    if variant == "v4":
        return prompt_variant_text("v4_user")
    return prompt_variant_text("v3_plus_user")
