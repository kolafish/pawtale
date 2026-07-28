from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import (
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirSectionMaterial,
    MemoirVoiceUtterance,
)
from app.services.memoir import editing_jobs, sections
from app.services.memoir.narrative.framework_matching import (
    schedule_framework_matching_job,
)
from app.services.memoir.transcript_projection import (
    current_transcript_version_id,
    current_utterance_text,
    source_text_hash,
)


@dataclass(frozen=True)
class TranscriptMutationGuard:
    source_text_hash: str
    transcript_version_id: str | None


def transcript_mutation_guard(
    utterance: MemoirVoiceUtterance,
) -> TranscriptMutationGuard:
    return TranscriptMutationGuard(
        source_text_hash=source_text_hash(current_utterance_text(utterance)),
        transcript_version_id=current_transcript_version_id(utterance),
    )


async def claim_transcript_mutation_guard(
    db: AsyncSession,
    *,
    utterance: MemoirVoiceUtterance,
    expected: TranscriptMutationGuard,
) -> bool:
    if transcript_mutation_guard(utterance) != expected:
        return False
    message = utterance.chat_message
    if message is None:
        return False
    if source_text_hash(message.content_text) != expected.source_text_hash:
        return False

    version_filter = (
        MemoirChatMessage.transcript_version_id.is_(None)
        if expected.transcript_version_id is None
        else MemoirChatMessage.transcript_version_id
        == expected.transcript_version_id
    )
    result = await db.execute(
        update(MemoirChatMessage)
        .where(
            MemoirChatMessage.id == message.id,
            MemoirChatMessage.content_text == message.content_text,
            version_filter,
        )
        .values(
            content_text=MemoirChatMessage.content_text,
            transcript_version_id=MemoirChatMessage.transcript_version_id,
            updated_at=MemoirChatMessage.updated_at,
        )
    )
    return result.rowcount == 1


async def schedule_material_organization_for_ready_utterance(
    db: AsyncSession,
    *,
    project_id: str,
    utterance: MemoirVoiceUtterance,
    source: str,
    ai_model: str | None = None,
) -> MemoirEditingJob | None:
    text = current_utterance_text(utterance)
    if not text:
        return None
    await schedule_framework_matching_job(
        db,
        project_id=project_id,
        material_ids=[utterance.id],
        ai_model=ai_model,
    )

    existing = await db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == utterance.id,
        )
    )
    transcript_version_id = current_transcript_version_id(utterance)
    if existing is not None:
        if transcript_version_id:
            await sections.refresh_material_transcript_version(
                db,
                project_id=project_id,
                utterance_id=utterance.id,
                transcript_version_id=transcript_version_id,
            )
        return None
    if utterance.completion_issue_id:
        return None

    payload: dict[str, object] = {
        "utterance_id": utterance.id,
        "voice_session_id": utterance.voice_session_id,
        "transcript_version_id": transcript_version_id,
        "source_text_hash": source_text_hash(text),
        "source": source,
    }
    if ai_model:
        payload["ai_model"] = ai_model
    return await editing_jobs.enqueue_editing_job(
        db,
        project_id=project_id,
        job_type=MemoirEditingJobType.ORGANIZE_MATERIAL,
        payload_json=payload,
        dedupe_key=f"organize_material:{utterance.id}",
        replace_completed=True,
    )
