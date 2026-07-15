from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import (
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerKind,
    MemoirFollowupTaskStatus,
)
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirChapterSourceLinkStatus,
    MemoirSessionGoal,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import MemoirFollowupTask, MemoirFollowupTaskUtterance
from app.schemas.memoir_narrative import MemoirCompletionOutcomeResponse


def _event_task_id(event: MemoirAutomationEvent) -> str:
    metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
    route = metadata.get("route") if isinstance(metadata.get("route"), dict) else metadata
    followup = route.get("followup_answer") if isinstance(route.get("followup_answer"), dict) else {}
    return str(followup.get("task_id") or "").strip()


async def _review_event_id(
    db: AsyncSession,
    *,
    project_id: str,
    task_id: str,
    utterance_id: str,
) -> str | None:
    events = list(
        (
            await db.scalars(
                select(MemoirAutomationEvent)
                .where(
                    MemoirAutomationEvent.project_id == project_id,
                    MemoirAutomationEvent.event_type
                    == MemoirAutomationEventType.SOURCE_HELD.value,
                    MemoirAutomationEvent.status
                    == MemoirAutomationEventStatus.NEEDS_REVIEW.value,
                )
                .order_by(
                    MemoirAutomationEvent.created_at.desc(),
                    MemoirAutomationEvent.id.desc(),
                )
            )
        ).all()
    )
    for event in events:
        utterance_ids = event.utterance_ids_json if isinstance(event.utterance_ids_json, list) else []
        if utterance_id in utterance_ids or _event_task_id(event) == task_id:
            return event.id
    return None


def _response(
    *,
    status: str,
    session: MemoirVoiceSession,
    task: MemoirFollowupTask | None,
    chapter_title: str,
    utterance: MemoirVoiceUtterance | None,
    relation: MemoirFollowupTaskUtterance | None,
    message: str,
    review_event_id: str | None = None,
) -> MemoirCompletionOutcomeResponse:
    answer_kind = (
        MemoirFollowupAnswerKind(relation.answer_kind)
        if relation is not None and relation.answer_kind
        else None
    )
    return MemoirCompletionOutcomeResponse(
        status=status,
        voice_session_id=session.id,
        task_id=task.id if task else None,
        task_status=MemoirFollowupTaskStatus(task.status) if task else None,
        chapter_id=task.chapter_id if task else None,
        chapter_title=chapter_title,
        utterance_id=utterance.id if utterance else None,
        answer_kind=answer_kind,
        route_action=(utterance.source_route_action or "") if utterance else "",
        reason=(utterance.source_route_reason or "") if utterance else "",
        message=message,
        review_event_id=review_event_id,
    )


async def completion_outcome(
    db: AsyncSession,
    *,
    project_id: str,
    voice_session_id: str,
) -> MemoirCompletionOutcomeResponse:
    session = await db.scalar(
        select(MemoirVoiceSession).where(
            MemoirVoiceSession.project_id == project_id,
            MemoirVoiceSession.id == voice_session_id,
        )
    )
    if session is None:
        raise ValueError("voice session does not belong to the project")
    if session.session_goal != MemoirSessionGoal.BOOK_COMPLETION.value:
        raise ValueError("voice session is not a book completion session")

    row = (
        await db.execute(
            select(
                MemoirFollowupTaskUtterance,
                MemoirVoiceUtterance,
                MemoirFollowupTask,
            )
            .join(
                MemoirVoiceUtterance,
                MemoirVoiceUtterance.id == MemoirFollowupTaskUtterance.utterance_id,
            )
            .join(
                MemoirFollowupTask,
                and_(
                    MemoirFollowupTask.project_id
                    == MemoirFollowupTaskUtterance.project_id,
                    MemoirFollowupTask.id == MemoirFollowupTaskUtterance.task_id,
                ),
            )
            .where(
                MemoirFollowupTaskUtterance.project_id == project_id,
                MemoirVoiceUtterance.voice_session_id == voice_session_id,
                MemoirFollowupTaskUtterance.is_assignment_snapshot.is_(True),
            )
            .order_by(
                MemoirVoiceUtterance.created_at.desc(),
                MemoirVoiceUtterance.id.desc(),
            )
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        utterance = await db.scalar(
            select(MemoirVoiceUtterance)
            .where(MemoirVoiceUtterance.voice_session_id == voice_session_id)
            .order_by(
                MemoirVoiceUtterance.created_at.desc(),
                MemoirVoiceUtterance.id.desc(),
            )
            .limit(1)
        )
        if utterance is not None:
            return _response(
                status="processing",
                session=session,
                task=None,
                chapter_title="",
                utterance=utterance,
                relation=None,
                message="回答已经保存，正在建立补全关联。",
            )
        return _response(
            status="not_used",
            session=session,
            task=None,
            chapter_title="",
            utterance=None,
            relation=None,
            message="这次没有检测到可处理的补全回答。",
        )

    relation, utterance, task = row
    chapter_title = ""
    if task.chapter_id:
        chapter_title = (
            await db.scalar(
                select(MemoirBookPlanChapter.title).where(
                    MemoirBookPlanChapter.project_id == project_id,
                    MemoirBookPlanChapter.id == task.chapter_id,
                )
            )
            or ""
        )

    if (
        relation.interpretation_status
        == MemoirFollowupAnswerInterpretationStatus.NEEDS_CONFIRMATION.value
        or utterance.source_route_action == "hold_for_review"
    ):
        event_id = await _review_event_id(
            db,
            project_id=project_id,
            task_id=task.id,
            utterance_id=utterance.id,
        )
        return _response(
            status="needs_confirmation",
            session=session,
            task=task,
            chapter_title=chapter_title,
            utterance=utterance,
            relation=relation,
            message=f"这段回答还需要确认，确认后才能用于《{chapter_title}》。"
            if chapter_title
            else "这段回答还需要确认后才能使用。",
            review_event_id=event_id,
        )

    if utterance.source_route_action == "ignore":
        return _response(
            status="not_used",
            session=session,
            task=task,
            chapter_title=chapter_title,
            utterance=utterance,
            relation=relation,
            message="这段话没有作为书稿素材使用。",
        )

    source_link = await db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.project_id == project_id,
            MemoirChapterSourceLink.utterance_id == utterance.id,
            MemoirChapterSourceLink.status == MemoirChapterSourceLinkStatus.ACTIVE.value,
        )
    )
    if source_link:
        source_chapter_title = (
            await db.scalar(
                select(MemoirBookPlanChapter.title).where(
                    MemoirBookPlanChapter.project_id == project_id,
                    MemoirBookPlanChapter.id == source_link.chapter_id,
                )
            )
            or chapter_title
        )
        return _response(
            status="incorporated",
            session=session,
            task=task,
            chapter_title=source_chapter_title,
            utterance=utterance,
            relation=relation,
            message=f"这段回答已收入《{source_chapter_title}》的章节素材。"
            if source_chapter_title
            else "这段回答已收入章节素材。",
        )

    if (
        relation.interpretation_status
        == MemoirFollowupAnswerInterpretationStatus.RESOLVED.value
        and relation.answer_kind
        in {
            MemoirFollowupAnswerKind.CLARIFICATION.value,
            MemoirFollowupAnswerKind.FACT_RESOLUTION.value,
        }
        and task.status == MemoirFollowupTaskStatus.ANSWERED.value
    ):
        return _response(
            status="context_recorded",
            session=session,
            task=task,
            chapter_title=chapter_title,
            utterance=utterance,
            relation=relation,
            message=f"这段回答已作为《{chapter_title}》的章节说明保存。"
            if chapter_title
            else "这段回答已作为章节说明保存。",
        )

    return _response(
        status="processing",
        session=session,
        task=task,
        chapter_title=chapter_title,
        utterance=utterance,
        relation=relation,
        message="回答已经保存，系统仍在判断是否入章。",
    )
