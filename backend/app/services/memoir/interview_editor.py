from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.domain.memoir.types import (
    MemoirChatMessageRole,
    MemoirInterviewType,
)
from app.models.memoir import (
    MemoirBookPlan,
    MemoirChatMessage,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_interview_editor import (
    MemoirInterviewEditorAnswerResponse,
    MemoirInterviewEditorQuestionResponse,
    MemoirInterviewEditorSessionDetailResponse,
    MemoirInterviewEditorSessionListResponse,
    MemoirInterviewEditorSessionSummaryResponse,
    MemoirInterviewEditorTimelineItemResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_source_links import included_chapters_by_utterance
from app.services.memoir.source_disposition import is_countable_source_utterance


QUESTION_ROLES = {
    MemoirChatMessageRole.INTERVIEWER.value,
    MemoirChatMessageRole.ASSISTANT.value,
}


def _message_order_key(message: MemoirChatMessage) -> tuple[int, object, str]:
    return message.turn_index, message.created_at, message.id


def _utterance_order_key(utterance: MemoirVoiceUtterance) -> tuple[object, str]:
    return utterance.created_at, utterance.id


def _utterance_text(utterance: MemoirVoiceUtterance) -> str:
    return (utterance.transcript_edited or "").strip() or (utterance.transcript_raw or "").strip()


def _message_text(message: MemoirChatMessage) -> str:
    text = (message.content_text or "").strip()
    if text:
        return text
    utterance = _primary_utterance(message)
    return _utterance_text(utterance) if utterance else ""


def _primary_utterance(message: MemoirChatMessage) -> MemoirVoiceUtterance | None:
    utterances = sorted(list(message.utterances), key=_utterance_order_key)
    return utterances[0] if utterances else None


def _answer_pairs(session: MemoirVoiceSession) -> list[tuple[MemoirChatMessage, MemoirVoiceUtterance]]:
    pairs: list[tuple[MemoirChatMessage, MemoirVoiceUtterance]] = []
    for message in session.messages:
        if message.role != MemoirChatMessageRole.USER.value:
            continue
        for utterance in sorted(list(message.utterances), key=_utterance_order_key):
            text = _utterance_text(utterance)
            if text and is_countable_source_utterance(utterance, text):
                pairs.append((message, utterance))
    return pairs


def _utterance_duration_seconds(utterance: MemoirVoiceUtterance) -> int:
    if utterance.audio_file and utterance.audio_file.duration_seconds is not None:
        return int(utterance.audio_file.duration_seconds)
    if utterance.start_ms is None or utterance.end_ms is None:
        return 0
    return max(0, round((utterance.end_ms - utterance.start_ms) / 1000))


def _detail_options():
    return (
        selectinload(MemoirVoiceSession.messages)
        .selectinload(MemoirChatMessage.utterances)
        .selectinload(MemoirVoiceUtterance.audio_file),
        selectinload(MemoirVoiceSession.utterances).selectinload(MemoirVoiceUtterance.audio_file),
    )


async def _load_owned_session(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    voice_session_id: str,
) -> MemoirVoiceSession:
    await load_memoir_project(db, project_id, user_id)
    session = await db.scalar(
        select(MemoirVoiceSession)
        .where(
            MemoirVoiceSession.id == voice_session_id,
            MemoirVoiceSession.project_id == project_id,
        )
        .options(*_detail_options())
    )
    if not session:
        raise HTTPException(status_code=404, detail="访谈记录不存在")
    return session


async def _project_book_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id))


def _session_summary(session: MemoirVoiceSession) -> MemoirInterviewEditorSessionSummaryResponse:
    messages = sorted(list(session.messages), key=_message_order_key)
    question_messages = [
        message for message in messages
        if message.role in QUESTION_ROLES and _message_text(message)
    ]
    answer_pairs = _answer_pairs(session)
    question_ids = {message.id for message in question_messages}
    unassigned_count = sum(
        1
        for message, _utterance in answer_pairs
        if message.parent_message_id not in question_ids
    )
    last_message = next((message for message in reversed(messages) if _message_text(message)), None)
    return MemoirInterviewEditorSessionSummaryResponse(
        id=session.id,
        project_id=session.project_id,
        status=session.status,
        guidance_mode=session.guidance_mode,
        interview_type=session.interview_type,
        current_guidance_module=session.current_guidance_module,
        started_at=session.started_at,
        stopped_at=session.stopped_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
        question_count=len(question_messages),
        answer_count=len(answer_pairs),
        unassigned_answer_count=unassigned_count,
        total_audio_seconds=sum(_utterance_duration_seconds(utterance) for _message, utterance in answer_pairs),
        last_message_text=(_message_text(last_message)[:120] if last_message else ""),
        last_message_role=last_message.role if last_message else None,
        last_message_at=last_message.created_at if last_message else None,
    )


def _answer_response(
    message: MemoirChatMessage,
    utterance: MemoirVoiceUtterance,
    *,
    included_by_utterance: dict[str, list],
) -> MemoirInterviewEditorAnswerResponse:
    audio_file = utterance.audio_file
    display_text = _utterance_text(utterance)
    included_chapters = included_by_utterance.get(utterance.id, [])
    return MemoirInterviewEditorAnswerResponse(
        id=utterance.id,
        message_id=message.id,
        parent_question_message_id=message.parent_message_id,
        voice_session_id=utterance.voice_session_id,
        project_id=message.project_id,
        audio_file_id=utterance.audio_file_id,
        audio_mime_type=audio_file.mime_type if audio_file else None,
        audio_size_bytes=audio_file.size_bytes if audio_file else None,
        audio_duration_seconds=audio_file.duration_seconds if audio_file else None,
        transcript_version_id=message.transcript_version_id,
        transcript_raw=utterance.transcript_raw or "",
        transcript_edited=utterance.transcript_edited or "",
        display_text=display_text,
        start_ms=utterance.start_ms,
        end_ms=utterance.end_ms,
        provider=utterance.provider,
        source_material_type=utterance.source_material_type or "",
        source_route_action=utterance.source_route_action or "",
        source_route_confidence=utterance.source_route_confidence,
        source_route_reason=utterance.source_route_reason or "",
        source_route_updated_at=utterance.source_route_updated_at,
        is_included_in_book=bool(included_chapters),
        included_chapters=included_chapters,
        turn_index=message.turn_index,
        created_at=utterance.created_at,
    )


def _question_response(
    message: MemoirChatMessage,
    *,
    answers: list[MemoirInterviewEditorAnswerResponse],
) -> MemoirInterviewEditorQuestionResponse:
    utterance = _primary_utterance(message)
    return MemoirInterviewEditorQuestionResponse(
        id=message.id,
        voice_session_id=message.voice_session_id,
        project_id=message.project_id,
        role=message.role,
        text=_message_text(message),
        audio_file_id=message.audio_file_id,
        transcript_version_id=message.transcript_version_id,
        utterance_id=utterance.id if utterance else None,
        answer_count=len(answers),
        answers=answers,
        turn_index=message.turn_index,
        created_at=message.created_at,
        updated_at=message.updated_at,
    )


def _timeline_item(message: MemoirChatMessage) -> MemoirInterviewEditorTimelineItemResponse:
    utterances = sorted(list(message.utterances), key=_utterance_order_key)
    primary = utterances[0] if utterances else None
    return MemoirInterviewEditorTimelineItemResponse(
        message_id=message.id,
        voice_session_id=message.voice_session_id,
        project_id=message.project_id,
        role=message.role,
        parent_message_id=message.parent_message_id,
        text=_message_text(message),
        audio_file_id=message.audio_file_id,
        transcript_version_id=message.transcript_version_id,
        utterance_ids=[utterance.id for utterance in utterances],
        source_material_type=primary.source_material_type if primary else "",
        source_route_action=primary.source_route_action if primary else "",
        turn_index=message.turn_index,
        created_at=message.created_at,
        updated_at=message.updated_at,
    )


async def list_interview_sessions(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    interview_type: str | None = MemoirInterviewType.HUMAN_INTERVIEW.value,
    limit: int = 50,
    offset: int = 0,
) -> MemoirInterviewEditorSessionListResponse:
    await load_memoir_project(db, project_id, user_id)
    filters = [Project.user_id == user_id, MemoirVoiceSession.project_id == project_id]
    if interview_type:
        filters.append(MemoirVoiceSession.interview_type == interview_type)
    total = await db.scalar(
        select(func.count(MemoirVoiceSession.id))
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
    )
    sessions = (
        await db.execute(
            select(MemoirVoiceSession)
            .join(Project, MemoirVoiceSession.project_id == Project.id)
            .where(*filters)
            .options(*_detail_options())
            .order_by(MemoirVoiceSession.updated_at.desc(), MemoirVoiceSession.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return MemoirInterviewEditorSessionListResponse(
        items=[_session_summary(session) for session in sessions],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def get_interview_session_detail(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    voice_session_id: str,
) -> MemoirInterviewEditorSessionDetailResponse:
    session = await _load_owned_session(
        db,
        user_id=user_id,
        project_id=project_id,
        voice_session_id=voice_session_id,
    )
    plan = await _project_book_plan(db, project_id)
    included_by_utterance = await included_chapters_by_utterance(db, plan)
    messages = sorted(list(session.messages), key=_message_order_key)
    question_messages = [
        message for message in messages
        if message.role in QUESTION_ROLES and _message_text(message)
    ]
    question_ids = {message.id for message in question_messages}
    answers_by_parent: dict[str, list[MemoirInterviewEditorAnswerResponse]] = {
        question_id: [] for question_id in question_ids
    }
    unassigned_answers: list[MemoirInterviewEditorAnswerResponse] = []
    for message, utterance in sorted(
        _answer_pairs(session),
        key=lambda item: (_message_order_key(item[0]), item[1].created_at, item[1].id),
    ):
        if not is_countable_source_utterance(utterance, _utterance_text(utterance)):
            continue
        answer = _answer_response(message, utterance, included_by_utterance=included_by_utterance)
        parent_id = answer.parent_question_message_id
        if parent_id in answers_by_parent:
            answers_by_parent[parent_id].append(answer)
        else:
            unassigned_answers.append(answer)

    return MemoirInterviewEditorSessionDetailResponse(
        session=_session_summary(session),
        questions=[
            _question_response(message, answers=answers_by_parent.get(message.id, []))
            for message in question_messages
        ],
        unassigned_answers=unassigned_answers,
        raw_timeline=[_timeline_item(message) for message in messages if _message_text(message)],
    )
