from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_FRAMEWORK_TEMPLATE_KEY = "memoir_life_topics"
DEFAULT_FRAMEWORK_TEMPLATE_VERSION = "1"


class FrameworkTemplateModuleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    module_key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    sort_order: int = Field(ge=0)

    @field_validator("module_key", "title")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("framework template text must not be blank")
        return value


class FrameworkTemplateItemDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_key: str = Field(min_length=1, max_length=64)
    module_key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=160)
    intent: str = Field(min_length=1, max_length=500)
    evidence_hints: tuple[str, ...] = Field(min_length=1)
    sort_order: int = Field(ge=0)

    @field_validator("item_key", "module_key", "title", "intent")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("framework template text must not be blank")
        return value

    @field_validator("evidence_hints")
    @classmethod
    def validate_evidence_hints(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("framework evidence hints must not be blank")
        return values


class FrameworkTemplateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    template_key: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=24)
    modules: tuple[FrameworkTemplateModuleDefinition, ...] = Field(min_length=1)
    items: tuple[FrameworkTemplateItemDefinition, ...] = Field(min_length=1)

    @field_validator("template_key", "version")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("framework template identity must not be blank")
        return value

    @model_validator(mode="after")
    def validate_references(self) -> "FrameworkTemplateDefinition":
        module_keys = [module.module_key for module in self.modules]
        item_keys = [item.item_key for item in self.items]
        if len(set(module_keys)) != len(module_keys):
            raise ValueError("framework template module keys must be unique")
        if len(set(item_keys)) != len(item_keys):
            raise ValueError("framework template item keys must be unique")
        unknown_modules = {
            item.module_key for item in self.items if item.module_key not in module_keys
        }
        if unknown_modules:
            raise ValueError(
                "framework template items reference unknown modules: "
                + ", ".join(sorted(unknown_modules))
            )
        return self


def canonical_template_snapshot(
    template: FrameworkTemplateDefinition,
) -> dict[str, object]:
    return json.loads(template.model_dump_json())


def framework_template_hash(snapshot: dict[str, object]) -> str:
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@lru_cache(maxsize=None)
def load_framework_template(
    template_key: str = DEFAULT_FRAMEWORK_TEMPLATE_KEY,
    version: str = DEFAULT_FRAMEWORK_TEMPLATE_VERSION,
) -> FrameworkTemplateDefinition:
    filenames = {
        (DEFAULT_FRAMEWORK_TEMPLATE_KEY, DEFAULT_FRAMEWORK_TEMPLATE_VERSION):
            "life_topics_v1.json",
    }
    filename = filenames.get((template_key, version))
    if filename is None:
        raise ValueError(f"unknown memoir framework template {template_key}:{version}")
    path = Path(__file__).with_name("framework_templates") / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    template = FrameworkTemplateDefinition.model_validate(payload)
    if template.template_key != template_key or template.version != version:
        raise ValueError("framework template file identity does not match its registry")
    return template
