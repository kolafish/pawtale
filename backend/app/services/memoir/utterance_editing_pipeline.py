from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import MemoirEditingJob, MemoirVoiceUtterance
from app.services.memoir import editing_jobs


ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY = "transcript_version_ids"
ROUTE_PAYLOAD_SOURCE_TEXT_HASHES_KEY = "source_text_hashes"
ROUTE_PAYLOAD_SOURCE_TEXT_LENGTHS_KEY = "source_text_lengths"


def current_utterance_text(utterance: MemoirVoiceUtterance) -> str:
    return (utterance.transcript_edited or "").strip() or (utterance.transcript_raw or "").strip()


def current_transcript_version_id(utterance: MemoirVoiceUtterance) -> str | None:
    return utterance.chat_message.transcript_version_id if utterance.chat_message else None


def source_text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


async def schedule_source_routing_for_ready_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
    source: str,
    ai_model: str | None = None,
    replace_completed: bool = True,
) -> MemoirEditingJob | None:
    text = current_utterance_text(utterance)
    if not text:
        return None
    text_hash = source_text_hash(text)
    transcript_version_id = current_transcript_version_id(utterance)
    payload_json: dict[str, object] = {
        "utterance_ids": [utterance.id],
        "voice_session_id": utterance.voice_session_id,
        "source": source,
        ROUTE_PAYLOAD_TRANSCRIPT_VERSION_IDS_KEY: {
            utterance.id: transcript_version_id,
        },
        ROUTE_PAYLOAD_SOURCE_TEXT_HASHES_KEY: {
            utterance.id: text_hash,
        },
        ROUTE_PAYLOAD_SOURCE_TEXT_LENGTHS_KEY: {
            utterance.id: len(text),
        },
    }
    if ai_model:
        payload_json["ai_model"] = ai_model
    return await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ROUTE_SOURCES,
        payload_json=payload_json,
        dedupe_key=f"route_source:{utterance.id}",
        replace_completed=replace_completed,
    )
