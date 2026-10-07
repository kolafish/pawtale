from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Float, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirChatMessageRole,
    MemoirInterviewType,
    MemoirVoiceSessionStatus,
)
from app.models.memoir import (
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
from app.services.memoir.book_plan_sources import sorted_chapters
from app.services.memoir.book_source_links import (
    ChapterSourceChangeState,
    _latest_draft_versions,
    chapter_source_change_states_by_chapter,
    plan_source_ref_dicts_by_chapter,
    source_ref_utterance_ids,
)
from app.services.memoir.material_roles import narrator_material_clause


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


_WHITESPACE_RE = "[[:space:]\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"


def _current_utterance_chars():
    utterance = MemoirVoiceUtterance
    return func.coalesce(
        func.nullif(func.regexp_replace(utterance.transcript_edited, _WHITESPACE_RE, "", "g"), ""),
        func.regexp_replace(utterance.transcript_raw, _WHITESPACE_RE, "", "g"),
    )


async def _voice_progress_metrics(
    db: AsyncSession,
    *,
    user_id: str | None = None,
    project_id: str | None = None,
) -> VoiceProgressMetrics:
    utterance = MemoirVoiceUtterance
    message = MemoirChatMessage
    session = MemoirVoiceSession
    current_chars = _current_utterance_chars()
    utterance_totals = (
        select(
            utterance.voice_session_id.label("session_id"),
            func.count().label("utterances"),
            func.sum(func.length(current_chars)).label("chars"),
            func.sum(
                func.greatest(
                    0,
                    func.round(
                        (utterance.end_ms - utterance.start_ms).cast(Float) / 1000
                    ),
                )
            ).label("seconds"),
        )
        .where(current_chars != "", narrator_material_clause())
        .group_by(utterance.voice_session_id)
        .cte("utterance_totals")
    )
    module = func.coalesce(
        func.nullif(session.current_guidance_module, ""),
        case(
            (session.interview_type == MemoirInterviewType.HUMAN_INTERVIEW.value, "human_interview"),
            (session.guidance_mode == "free_narration", "free_narration"),
            else_="unassigned",
        ),
    ).label("module")
    effective = (
        select(
            session.id.label("id"),
            session.status.label("status"),
            session.updated_at.label("updated_at"),
            module,
            utterance_totals.c.utterances,
            utterance_totals.c.chars,
            utterance_totals.c.seconds,
        )
        .join(utterance_totals, utterance_totals.c.session_id == session.id)
    )
    if project_id is not None:
        effective = effective.where(session.project_id == project_id)
    else:
        effective = effective.join(Project, Project.id == session.project_id).where(
            Project.user_id == user_id
        )
    effective = effective.cte("effective_sessions")
    grouped = (
        select(
            effective.c.module,
            func.count().label("sessions"),
            func.sum(effective.c.utterances).label("utterances"),
            func.sum(effective.c.chars).label("chars"),
        )
        .group_by(effective.c.module)
        .order_by(effective.c.module)
    )
    module_rows = (await db.execute(grouped)).all()
    totals = (
        await db.execute(
            select(
                func.count().label("sessions"),
                func.count().filter(
                    effective.c.status.in_([
                        MemoirVoiceSessionStatus.CREATED.value,
                        MemoirVoiceSessionStatus.LISTENING.value,
                        MemoirVoiceSessionStatus.SPEAKING.value,
                    ])
                ).label("active"),
                func.count().filter(
                    effective.c.status == MemoirVoiceSessionStatus.STOPPED.value
                ).label("stopped"),
                func.count().filter(
                    effective.c.status == MemoirVoiceSessionStatus.FAILED.value
                ).label("failed"),
                func.coalesce(func.sum(effective.c.utterances), 0).label("utterances"),
                func.coalesce(func.sum(effective.c.chars), 0).label("chars"),
                func.coalesce(func.sum(effective.c.seconds), 0).label("seconds"),
                func.max(effective.c.updated_at).label("last_session_at"),
            )
        )
    ).one()
    user_messages, assistant_messages = (
        await db.execute(
            select(
                func.count().filter(
                    and_(
                        message.role == MemoirChatMessageRole.USER.value,
                        func.btrim(message.content_text) != "",
                    )
                ),
                func.count().filter(
                    and_(
                        message.role == MemoirChatMessageRole.ASSISTANT.value,
                        func.btrim(message.content_text) != "",
                        case(
                            (
                                func.json_typeof(message.metadata_json["expects_answer"]) == "boolean",
                                message.metadata_json["expects_answer"].as_string() != "false",
                            ),
                            else_=True,
                        ),
                    )
                ),
            )
            .select_from(message)
            .join(effective, effective.c.id == message.voice_session_id)
        )
    ).one()
    return VoiceProgressMetrics(
        session_count=totals.sessions,
        active_session_count=totals.active,
        stopped_session_count=totals.stopped,
        failed_session_count=totals.failed,
        utterance_count=totals.utterances,
        user_message_count=user_messages,
        assistant_message_count=assistant_messages,
        total_user_char_count=totals.chars,
        total_audio_seconds=int(totals.seconds),
        last_session_at=totals.last_session_at,
        modules=[
            MemoirVoiceModuleProgressResponse(
                module=row.module,
                session_count=row.sessions,
                utterance_count=row.utterances,
                user_char_count=row.chars,
            )
            for row in module_rows
        ],
    )


def char_count(text: str | None) -> int:
    return len("".join(str(text or "").split()))


def _chapter_items(
    plan: MemoirBookPlan | None,
    *,
    valid_utterance_ids: set[str],
    draft_word_counts: dict[str, int],
    source_refs_by_chapter: dict[str, list[dict[str, object]]] | None = None,
    source_change_states_by_chapter: dict[str, ChapterSourceChangeState] | None = None,
) -> list[MemoirProjectProgressChapterResponse]:
    if not plan:
        return []
    items: list[MemoirProjectProgressChapterResponse] = []
    refs_by_chapter = source_refs_by_chapter
    for chapter in sorted_chapters(plan):
        refs = refs_by_chapter.get(chapter.id, []) if refs_by_chapter is not None else []
        source_ids = {
            utterance_id
            for utterance_id in source_ref_utterance_ids(refs)
            if utterance_id in valid_utterance_ids
        }
        source_change_state = (
            source_change_states_by_chapter.get(chapter.id)
            if source_change_states_by_chapter is not None
            else None
        )
        draft = chapter.draft
        items.append(
            MemoirProjectProgressChapterResponse(
                chapter_id=chapter.id,
                title=chapter.title,
                sort_order=chapter.sort_order,
                include_in_book=chapter.include_in_book,
                source_count=len(source_ids),
                pending_source_count=source_change_state.pending_source_count if source_change_state else 0,
                pending_completion_source_count=(
                    source_change_state.pending_completion_source_count
                    if source_change_state
                    else 0
                ),
                pending_new_source_count=(
                    source_change_state.pending_new_source_count
                    if source_change_state
                    else 0
                ),
                removed_source_count=source_change_state.removed_source_count if source_change_state else 0,
                draft_status=draft.status if draft else None,
                draft_word_count=draft_word_counts.get(chapter.id, 0),
            )
        )
    return items


async def _load_project_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft),
        )
    )


async def _valid_assigned_utterance_ids(
    db: AsyncSession,
    project_id: str,
    candidate_ids: set[str],
) -> set[str]:
    if not candidate_ids:
        return set()
    utterance = MemoirVoiceUtterance
    return set((await db.scalars(
        select(utterance.id)
        .join(MemoirVoiceSession, MemoirVoiceSession.id == utterance.voice_session_id)
        .where(
            MemoirVoiceSession.project_id == project_id,
            utterance.id.in_(candidate_ids),
            _current_utterance_chars() != "",
            narrator_material_clause(),
        )
    )).all())


async def _project_progress_snapshot_and_metrics(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    recent_event_limit: int,
    voice_unannounced_events_only: bool,
) -> tuple[MemoirProjectProgressSnapshotResponse, VoiceProgressMetrics]:
    await load_memoir_project(db, project_id, user_id)
    voice_metrics = await _voice_progress_metrics(db, project_id=project_id)
    plan = await _load_project_plan(db, project_id)
    source_refs_by_chapter = await plan_source_ref_dicts_by_chapter(db, plan)
    candidate_ids = {
        utterance_id
        for refs in source_refs_by_chapter.values()
        for utterance_id in source_ref_utterance_ids(refs)
    }
    valid_utterance_ids = await _valid_assigned_utterance_ids(db, project_id, candidate_ids)
    latest_versions = (
        await _latest_draft_versions(db, project_id=project_id) if plan else {}
    )
    source_change_states = await chapter_source_change_states_by_chapter(
        db,
        plan,
        valid_utterance_ids=valid_utterance_ids,
        source_refs_by_chapter=source_refs_by_chapter,
        latest_versions=latest_versions,
    )
    chapter_items = _chapter_items(
        plan,
        valid_utterance_ids=valid_utterance_ids,
        draft_word_counts={
            chapter_id: char_count(version.content)
            for chapter_id, version in latest_versions.items()
        },
        source_refs_by_chapter=source_refs_by_chapter,
        source_change_states_by_chapter=source_change_states,
    )
    recent_events = await automation_events.recent_automation_event_responses(
        db,
        user_id=user_id,
        project_id=project_id,
        limit=recent_event_limit,
        voice_unannounced_only=voice_unannounced_events_only,
    )
    snapshot = MemoirProjectProgressSnapshotResponse(
        project_id=project_id,
        valid_utterance_count=voice_metrics.utterance_count,
        assigned_utterance_count=len(valid_utterance_ids),
        pending_utterance_count=max(0, voice_metrics.utterance_count - len(valid_utterance_ids)),
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
    return snapshot, voice_metrics


async def get_project_progress_snapshot(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    recent_event_limit: int = 5,
    voice_unannounced_events_only: bool = False,
) -> MemoirProjectProgressSnapshotResponse:
    snapshot, _ = await _project_progress_snapshot_and_metrics(
        db,
        user_id=user_id,
        project_id=project_id,
        recent_event_limit=recent_event_limit,
        voice_unannounced_events_only=voice_unannounced_events_only,
    )
    return snapshot


async def get_voice_progress_from_snapshot(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str | None = None,
) -> MemoirVoiceProgressResponse:
    if project_id:
        snapshot, metrics = await _project_progress_snapshot_and_metrics(
            db, user_id=user_id, project_id=project_id, recent_event_limit=5,
            voice_unannounced_events_only=False,
        )
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
            modules=metrics.modules,
            snapshot=snapshot,
        )

    metrics = await _voice_progress_metrics(db, user_id=user_id)
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
