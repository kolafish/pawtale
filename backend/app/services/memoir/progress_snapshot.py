from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirChatMessageRole,
    MemoirVoiceSessionStatus,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirProjectProgressChapterResponse,
    MemoirProjectProgressSnapshotResponse,
    MemoirVoiceModuleProgressResponse,
    MemoirVoiceProgressResponse,
)
from app.services.memoir import automation_events
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_plan_sources import (
    pending_source_ids,
    removed_source_ids,
    sorted_chapters,
    source_ref_utterance_ids,
)


@dataclass(frozen=True)
class VoiceProgressMetrics:
    session_count: int
    active_session_count: int
    stopped_session_count: int
    failed_session_count: int
    utterance_count: int
    user_message_count: int
    assistant_message_count: int
    total_user_char_count: int
    total_audio_seconds: int
    last_session_at: datetime | None
    modules: list[MemoirVoiceModuleProgressResponse]


def char_count(text: str | None) -> int:
    return len("".join(str(text or "").split()))


def utterance_text(utterance: MemoirVoiceUtterance) -> str:
    return (utterance.transcript_edited or "").strip() or (utterance.transcript_raw or "").strip()


def visible_utterances(voice_session: MemoirVoiceSession) -> list[MemoirVoiceUtterance]:
    return [utterance for utterance in voice_session.utterances if utterance_text(utterance)]


def assistant_message_expects_answer(message: MemoirChatMessage) -> bool:
    metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
    return metadata.get("expects_answer") is not False


def utterance_duration_seconds(utterance: MemoirVoiceUtterance) -> int:
    if utterance.start_ms is None or utterance.end_ms is None:
        return 0
    return max(0, round((utterance.end_ms - utterance.start_ms) / 1000))


def _session_voice_counts(voice_session: MemoirVoiceSession) -> tuple[int, int, int, int, int]:
    messages = list(voice_session.messages)
    utterances = visible_utterances(voice_session)
    user_messages = [
        message for message in messages
        if message.role == MemoirChatMessageRole.USER.value and message.content_text.strip()
    ]
    assistant_messages = [
        message for message in messages
        if message.role == MemoirChatMessageRole.ASSISTANT.value
        and message.content_text.strip()
        and assistant_message_expects_answer(message)
    ]
    return (
        len(user_messages),
        len(assistant_messages),
        len(utterances),
        sum(char_count(utterance_text(utterance)) for utterance in utterances),
        sum(utterance_duration_seconds(utterance) for utterance in utterances),
    )


def _is_effective_session(voice_session: MemoirVoiceSession) -> bool:
    return bool(visible_utterances(voice_session))


def build_voice_progress_metrics(sessions: list[MemoirVoiceSession]) -> VoiceProgressMetrics:
    active_statuses = {
        MemoirVoiceSessionStatus.CREATED.value,
        MemoirVoiceSessionStatus.LISTENING.value,
        MemoirVoiceSessionStatus.SPEAKING.value,
    }
    effective_sessions = [session for session in sessions if _is_effective_session(session)]
    module_stats: dict[str, dict[str, int]] = {}
    user_message_count = 0
    assistant_message_count = 0
    utterance_count = 0
    total_user_char_count = 0
    total_audio_seconds = 0
    for voice_session in effective_sessions:
        session_user_count, session_assistant_count, session_utterance_count, session_chars, session_seconds = (
            _session_voice_counts(voice_session)
        )
        user_message_count += session_user_count
        assistant_message_count += session_assistant_count
        utterance_count += session_utterance_count
        total_user_char_count += session_chars
        total_audio_seconds += session_seconds

        module = voice_session.current_guidance_module or (
            "free_narration" if voice_session.guidance_mode == "free_narration" else "unassigned"
        )
        stats = module_stats.setdefault(module, {"sessions": 0, "utterances": 0, "chars": 0})
        stats["sessions"] += 1
        stats["utterances"] += session_utterance_count
        stats["chars"] += session_chars

    return VoiceProgressMetrics(
        session_count=len(effective_sessions),
        active_session_count=sum(1 for session in effective_sessions if session.status in active_statuses),
        stopped_session_count=sum(
            1 for session in effective_sessions if session.status == MemoirVoiceSessionStatus.STOPPED.value
        ),
        failed_session_count=sum(
            1 for session in effective_sessions if session.status == MemoirVoiceSessionStatus.FAILED.value
        ),
        utterance_count=utterance_count,
        user_message_count=user_message_count,
        assistant_message_count=assistant_message_count,
        total_user_char_count=total_user_char_count,
        total_audio_seconds=total_audio_seconds,
        last_session_at=effective_sessions[0].updated_at if effective_sessions else None,
        modules=[
            MemoirVoiceModuleProgressResponse(
                module=module,
                session_count=stats["sessions"],
                utterance_count=stats["utterances"],
                user_char_count=stats["chars"],
            )
            for module, stats in sorted(module_stats.items())
        ],
    )


def _latest_draft_word_counts(plan: MemoirBookPlan | None) -> dict[str, int]:
    if not plan:
        return {}
    counts: dict[str, int] = {}
    for chapter in plan.chapters:
        draft = chapter.draft
        if not draft:
            continue
        latest = max(draft.versions, key=lambda version: (version.version, version.created_at, version.id), default=None)
        if latest:
            counts[chapter.id] = char_count(latest.content)
    return counts


def _chapter_items(
    plan: MemoirBookPlan | None,
    *,
    valid_utterance_ids: set[str],
) -> list[MemoirProjectProgressChapterResponse]:
    if not plan:
        return []
    draft_word_counts = _latest_draft_word_counts(plan)
    items: list[MemoirProjectProgressChapterResponse] = []
    for chapter in sorted_chapters(plan):
        source_ids = {
            utterance_id
            for utterance_id in source_ref_utterance_ids(chapter.source_refs_json or [])
            if utterance_id in valid_utterance_ids
        }
        draft = chapter.draft
        items.append(
            MemoirProjectProgressChapterResponse(
                chapter_id=chapter.id,
                title=chapter.title,
                sort_order=chapter.sort_order,
                source_count=len(source_ids),
                pending_source_count=len(pending_source_ids(chapter.metadata_json) & valid_utterance_ids),
                removed_source_count=len(removed_source_ids(chapter.metadata_json)),
                draft_status=draft.status if draft else None,
                draft_word_count=draft_word_counts.get(chapter.id, 0),
            )
        )
    return items


async def _load_project_sessions(db: AsyncSession, project_id: str) -> list[MemoirVoiceSession]:
    result = await db.execute(
        select(MemoirVoiceSession)
        .where(MemoirVoiceSession.project_id == project_id)
        .options(
            selectinload(MemoirVoiceSession.messages),
            selectinload(MemoirVoiceSession.utterances),
        )
        .order_by(MemoirVoiceSession.updated_at.desc(), MemoirVoiceSession.created_at.desc())
    )
    return list(result.scalars())


async def _load_project_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions),
        )
    )


async def get_project_progress_snapshot(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    recent_event_limit: int = 5,
    voice_unannounced_events_only: bool = False,
) -> MemoirProjectProgressSnapshotResponse:
    await load_memoir_project(db, project_id, user_id)
    sessions = await _load_project_sessions(db, project_id)
    voice_metrics = build_voice_progress_metrics(sessions)
    visible = [
        utterance
        for session in sessions
        for utterance in visible_utterances(session)
    ]
    valid_utterance_ids = {utterance.id for utterance in visible}

    plan = await _load_project_plan(db, project_id)
    assigned_utterance_ids = {
        utterance_id
        for chapter in (plan.chapters if plan else [])
        for utterance_id in source_ref_utterance_ids(chapter.source_refs_json or [])
        if utterance_id in valid_utterance_ids
    }
    chapter_items = _chapter_items(plan, valid_utterance_ids=valid_utterance_ids)
    recent_events = await automation_events.recent_automation_event_responses(
        db,
        user_id=user_id,
        project_id=project_id,
        limit=recent_event_limit,
        voice_unannounced_only=voice_unannounced_events_only,
    )

    return MemoirProjectProgressSnapshotResponse(
        project_id=project_id,
        valid_utterance_count=len(valid_utterance_ids),
        assigned_utterance_count=len(assigned_utterance_ids),
        pending_utterance_count=max(0, len(valid_utterance_ids) - len(assigned_utterance_ids)),
        session_count=voice_metrics.session_count,
        active_session_count=voice_metrics.active_session_count,
        stopped_session_count=voice_metrics.stopped_session_count,
        failed_session_count=voice_metrics.failed_session_count,
        user_message_count=voice_metrics.user_message_count,
        assistant_message_count=voice_metrics.assistant_message_count,
        source_char_count=voice_metrics.total_user_char_count,
        total_audio_seconds=voice_metrics.total_audio_seconds,
        chapter_count=len(chapter_items),
        confirmed_chapter_count=sum(
            1 for item in chapter_items if item.draft_status == MemoirBookChapterDraftStatus.CONFIRMED.value
        ),
        draft_word_count=sum(item.draft_word_count for item in chapter_items),
        last_session_at=voice_metrics.last_session_at,
        recent_automation_events=recent_events,
        chapter_progress_items=chapter_items,
    )


async def get_voice_progress_from_snapshot(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str | None = None,
) -> MemoirVoiceProgressResponse:
    if project_id:
        snapshot = await get_project_progress_snapshot(db, user_id=user_id, project_id=project_id)
        return MemoirVoiceProgressResponse(
            project_id=project_id,
            session_count=snapshot.session_count,
            active_session_count=snapshot.active_session_count,
            stopped_session_count=snapshot.stopped_session_count,
            failed_session_count=snapshot.failed_session_count,
            utterance_count=snapshot.valid_utterance_count,
            user_message_count=snapshot.user_message_count,
            assistant_message_count=snapshot.assistant_message_count,
            total_user_char_count=snapshot.source_char_count,
            total_audio_seconds=snapshot.total_audio_seconds,
            last_session_at=snapshot.last_session_at,
            modules=build_voice_progress_metrics(await _load_project_sessions(db, project_id)).modules,
            snapshot=snapshot,
        )

    result = await db.execute(
        select(MemoirVoiceSession)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(Project.user_id == user_id)
        .options(
            selectinload(MemoirVoiceSession.messages),
            selectinload(MemoirVoiceSession.utterances),
        )
        .order_by(MemoirVoiceSession.updated_at.desc(), MemoirVoiceSession.created_at.desc())
    )
    metrics = build_voice_progress_metrics(list(result.scalars()))
    return MemoirVoiceProgressResponse(
        project_id=None,
        session_count=metrics.session_count,
        active_session_count=metrics.active_session_count,
        stopped_session_count=metrics.stopped_session_count,
        failed_session_count=metrics.failed_session_count,
        utterance_count=metrics.utterance_count,
        user_message_count=metrics.user_message_count,
        assistant_message_count=metrics.assistant_message_count,
        total_user_char_count=metrics.total_user_char_count,
        total_audio_seconds=metrics.total_audio_seconds,
        last_session_at=metrics.last_session_at,
        modules=metrics.modules,
        snapshot=None,
    )
