from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any


_RULES_RESOURCE = "asr_correction_rules.json"


@dataclass(frozen=True)
class _RegexRule:
    pattern: re.Pattern[str]
    replacement: str
    chapter_edit_only: bool


@dataclass(frozen=True)
class _ContextualHomophoneRule:
    pattern: re.Pattern[str]
    replacement: str
    before_patterns: tuple[re.Pattern[str], ...]
    after_patterns: tuple[re.Pattern[str], ...]


def normalize_memoir_asr_text(text: str, *, chapter_edit: bool = False) -> str:
    """Normalize known ASR terms for narration or chapter edit instructions."""
    normalized = text
    for rule in _normalization_rules():
        if isinstance(rule, _RegexRule):
            if rule.chapter_edit_only != chapter_edit:
                continue
            normalized = rule.pattern.sub(
                lambda match, replacement=rule.replacement: _format_replacement(replacement, match),
                normalized,
            )
        elif not chapter_edit:
            normalized = rule.pattern.sub(
                lambda match, current=normalized, contextual_rule=rule: _contextual_homophone_replacement(
                    current,
                    match,
                    contextual_rule,
                ),
                normalized,
            )
    return normalized


def _contextual_homophone_replacement(
    text: str,
    match: re.Match[str],
    rule: _ContextualHomophoneRule,
) -> str:
    before = text[max(0, match.start() - 18):match.start()]
    after = text[match.end():match.end() + 18]
    if any(pattern.search(before) for pattern in rule.before_patterns):
        return rule.replacement
    if any(pattern.search(after) for pattern in rule.after_patterns):
        return rule.replacement
    return match.group(0)


def _format_replacement(template: str, match: re.Match[str]) -> str:
    result = template
    for index, group in enumerate(match.groups(), start=1):
        result = result.replace(f"{{{index}}}", group or "")
    return result


@lru_cache(maxsize=1)
def _normalization_rules() -> tuple[_RegexRule | _ContextualHomophoneRule, ...]:
    config = _load_rules_config()
    rules: list[_RegexRule | _ContextualHomophoneRule] = []
    for item in config.get("rules", []):
        kind = item.get("kind")
        if kind == "regex":
            rules.append(_compile_regex_rule(item))
        elif kind == "contextual_homophone":
            rules.append(_compile_contextual_homophone_rule(item))
        else:
            raise ValueError(f"Unsupported ASR correction rule kind: {kind!r}")
    return tuple(rules)


def _load_rules_config() -> dict[str, Any]:
    resource = files(__package__).joinpath(_RULES_RESOURCE)
    with resource.open(encoding="utf-8") as file:
        return json.load(file)


def _compile_regex_rule(item: dict[str, Any]) -> _RegexRule:
    return _RegexRule(
        pattern=re.compile(_required_string(item, "pattern")),
        chapter_edit_only=item.get("chapter_edit_only") is True,
        replacement=_required_string(item, "replacement"),
    )


def _compile_contextual_homophone_rule(item: dict[str, Any]) -> _ContextualHomophoneRule:
    terms = item.get("terms")
    if not isinstance(terms, list) or not all(isinstance(term, str) and term for term in terms):
        raise ValueError("Contextual homophone rules require non-empty string terms")
    return _ContextualHomophoneRule(
        pattern=re.compile("|".join(re.escape(term) for term in terms)),
        replacement=_required_string(item, "replacement"),
        before_patterns=tuple(
            re.compile(rf"(?:{pattern})\s*$")
            for pattern in _required_string_list(item, "before_context_patterns")
        ),
        after_patterns=tuple(
            re.compile(rf"^\s*(?:{pattern})")
            for pattern in _required_string_list(item, "after_context_patterns")
        ),
    )


def _required_string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"ASR correction rule requires non-empty string field: {key}")
    return value


def _required_string_list(item: dict[str, Any], key: str) -> list[str]:
    value = item.get(key)
    if not isinstance(value, list) or not all(isinstance(entry, str) and entry for entry in value):
        raise ValueError(f"ASR correction rule requires non-empty string list field: {key}")
    return value
