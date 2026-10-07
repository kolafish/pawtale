from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirAutomationEventType
from app.models.memoir import MemoirAutomationEvent, MemoirVoiceSession, MemoirVoiceUtterance
from app.services.memoir import automation_events
from app.services.memoir.automation_event_metadata import (
    EVENT_SIGNATURE_KEY,
    VOICE_ONLY_KEY,
    VOICE_PROGRESS_MILESTONE_SOURCE,
)
from app.services.memoir.material_roles import narrator_material_clause


PROGRESS_UTTERANCE_MILESTONES = {1, 3, 5, 10, 15, 20, 30, 50, 75, 100}


def is_progress_utterance_milestone(count: int) -> bool:
    return count in PROGRESS_UTTERANCE_MILESTONES or (
        count >= 150 and count % 50 == 0
    )


def _milestone_signature(count: int) -> str:
    return f"voice-progress:valid-utterance:{count}"


async def _count_project_utterances(
    db: AsyncSession,
    *,
    project_id: str,
) -> int:
    source_text = func.coalesce(
        func.nullif(MemoirVoiceUtterance.transcript_edited, ""),
        MemoirVoiceUtterance.transcript_raw,
    )
    count = await db.scalar(
        select(func.count(MemoirVoiceUtterance.id))
        .join(
            MemoirVoiceSession,
            MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id,
        )
        .where(
            MemoirVoiceSession.project_id == project_id,
            narrator_material_clause(),
            func.length(func.trim(source_text)) > 0,
        )
    )
    return int(count or 0)


async def enqueue_utterance_milestone_notice(
    db: AsyncSession,
    *,
    project_id: str,
    utterance_id: str,
) -> MemoirAutomationEvent | None:
    count = await _count_project_utterances(db, project_id=project_id)
    if not is_progress_utterance_milestone(count):
        return None

    signature = _milestone_signature(count)
    existing = await db.scalar(
        select(MemoirAutomationEvent)
        .where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.metadata_json[EVENT_SIGNATURE_KEY].as_string()
            == signature,
        )
        .limit(1)
    )
    if existing is not None:
        return existing

    return await automation_events.record_automation_event(
        db,
        project_id=project_id,
        event_type=MemoirAutomationEventType.STORY_STRUCTURE_UPDATED,
        utterance_ids=[utterance_id],
        summary_text=f"这个项目已经留下 {count} 条讲述记录。",
        metadata_json={
            EVENT_SIGNATURE_KEY: signature,
            "source": VOICE_PROGRESS_MILESTONE_SOURCE,
            VOICE_ONLY_KEY: True,
            "valid_utterance_count": count,
        },
    )
