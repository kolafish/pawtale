from __future__ import annotations

import hashlib
import re

from app.models.memoir import MemoirSectionMaterial, MemoirVoiceUtterance


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


def source_text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
