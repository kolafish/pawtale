from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirSectionMaterial, MemoirVoiceUtterance, MemoirTranscriptVersion
from app.domain.memoir.types import MemoirCreatedBy


_NON_STORY_RE = re.compile(r"[\W_]|嗯|啊|呃|额|哦|那个|这个|就是")
MIN_STORY_CHARS = 2


def has_story_content(text: str) -> bool:
    return len(_NON_STORY_RE.sub("", text)) >= MIN_STORY_CHARS


def current_utterance_text(utterance: MemoirVoiceUtterance) -> str:
    edited = (utterance.transcript_edited or "").strip()
    return edited or (utterance.transcript_raw or "").strip()


def current_utterance_text_and_source(
    utterance: MemoirVoiceUtterance,
) -> tuple[str, str]:
    edited = (utterance.transcript_edited or "").strip()
    if edited:
        return edited, "edited"
    return (utterance.transcript_raw or "").strip(), "raw"


def current_transcript_version_id(
    utterance: MemoirVoiceUtterance,
) -> str | None:
    message = utterance.chat_message
    return message.transcript_version_id if message else None


def current_material_text(
    material: MemoirSectionMaterial,
    *,
    utterance: MemoirVoiceUtterance | None = None,
) -> str:
    version = material.transcript_version
    if version is not None:
        version_text = (version.content or "").strip()
        if version_text:
            return version_text
    return current_utterance_text(utterance or material.utterance)


@dataclass(frozen=True)
class TranscriptEdit:
    edited_at: datetime
    before: str
    after: str


async def load_transcript_edits(
    db: AsyncSession, materials: list[MemoirSectionMaterial],
) -> dict[str, tuple[TranscriptEdit, ...]]:
    """Explain actual human changes, rather than treating a later save as a new fact."""
    source_ids = {material.utterance_id for material in materials}
    if not source_ids:
        return {}
    versions = (await db.scalars(select(MemoirTranscriptVersion).where(
        MemoirTranscriptVersion.source_artifact_id.in_(source_ids),
    ).order_by(MemoirTranscriptVersion.created_at, MemoirTranscriptVersion.id))).all()
    by_source: dict[str, list[MemoirTranscriptVersion]] = {}
    for version in versions:
        by_source.setdefault(version.source_artifact_id, []).append(version)
    result = {}
    for material in materials:
        current = material.transcript_version
        if current is None:
            continue
        previous = material.utterance.transcript_raw
        edits = []
        for version in by_source.get(material.utterance_id, []):
            if version.created_at > current.created_at:
                break
            if version.created_by == MemoirCreatedBy.USER.value and previous != version.content:
                edits.append(TranscriptEdit(version.created_at, previous, version.content))
            previous = version.content
            if version.id == current.id:
                break
        result[current.id] = tuple(edits)
    return result


def transcript_edits_prompt(edits: tuple[TranscriptEdit, ...]) -> str:
    lines = []
    for edit in edits:
        changes = transcript_text_changes(edit.before, edit.after, label="用户实际替换")
        if changes:
            lines.append(f"用户编辑（UTC {edit.edited_at.isoformat()}）：\n" + changes)
    if not lines:
        return ""
    return (
        "讲述文字的人工修改记录：\n" + "\n".join(lines)
        + "\n仅‘用户实际替换’涉及的事实属于这次更正；按记录时间与核对回答比较，采用较晚的明确更正。"
        "未改动的原片段只是定位上下文，不能撤销先前的事实更正（包括先前人工编辑）。\n"
    )


def source_text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def transcript_text_changes(before: str, after: str, *, label: str) -> str:
    changes = []
    for tag, a, b, c, d in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal":
            changes.append(
                f"- 原片段：{before[max(0, a - 20):b + 20]}\n"
                f"  {label}：{before[a:b]!r} → {after[c:d]!r}"
            )
    return "\n".join(changes)
