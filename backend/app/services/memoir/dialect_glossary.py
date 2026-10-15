"""The project's 方言/家族词表: spoken or misrecognised forms and the writing to use instead."""
from __future__ import annotations

from app.models.projects import Project
from app.schemas.memoir_interview_editor import MemoirDialectGlossaryEntry

GLOSSARY_METADATA_KEY = "memoir_dialect_glossary_entries"


def _entry_from_raw(value: object) -> MemoirDialectGlossaryEntry | None:
    if not isinstance(value, dict):
        return None
    source = str(value.get("source") or "").strip()
    target = str(value.get("target") or "").strip()
    note = str(value.get("note") or "").strip() or None
    if not source or not target:
        return None
    return MemoirDialectGlossaryEntry(source=source, target=target, note=note)


def glossary_entries(project: Project) -> list[MemoirDialectGlossaryEntry]:
    raw_entries = (project.metadata_json or {}).get(GLOSSARY_METADATA_KEY)
    if not isinstance(raw_entries, list):
        return []
    entries: list[MemoirDialectGlossaryEntry] = []
    seen: set[tuple[str, str]] = set()
    for raw_entry in raw_entries:
        entry = _entry_from_raw(raw_entry)
        if entry is None:
            continue
        key = (entry.source, entry.target)
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return entries


def glossary_payload(entries: list[MemoirDialectGlossaryEntry]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        source = entry.source.strip()
        target = entry.target.strip()
        note = (entry.note or "").strip()
        key = (source, target)
        if not source or not target or key in seen:
            continue
        seen.add(key)
        item = {"source": source, "target": target}
        if note:
            item["note"] = note
        payload.append(item)
    return payload


def glossary_context_lines(entries: list[MemoirDialectGlossaryEntry]) -> tuple[str, ...]:
    lines: list[str] = []
    for entry in entries:
        line = f"{entry.source} => {entry.target}"
        if entry.note:
            line = f"{line}（{entry.note}）"
        lines.append(line)
    return tuple(lines)


def save_glossary_entries(project: Project, entries: list[MemoirDialectGlossaryEntry]) -> list[MemoirDialectGlossaryEntry]:
    saved = [MemoirDialectGlossaryEntry(**entry) for entry in glossary_payload(entries)]
    metadata = dict(project.metadata_json or {})
    metadata[GLOSSARY_METADATA_KEY] = [entry.model_dump(exclude_none=True) for entry in saved]
    project.metadata_json = metadata
    return saved


def add_glossary_entry(project: Project, *, source: str, target: str, note: str | None = None) -> bool:
    """Add one correction unless the same pair is already listed; True when it was added."""
    entries = glossary_entries(project)
    if any(entry.source == source and entry.target == target for entry in entries):
        return False
    save_glossary_entries(project, [*entries, MemoirDialectGlossaryEntry(source=source, target=target, note=note)])
    return True


def remove_glossary_entry(project: Project, *, source: str, target: str) -> None:
    save_glossary_entries(project, [
        entry for entry in glossary_entries(project)
        if not (entry.source == source and entry.target == target)
    ])
