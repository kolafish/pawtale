from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    MemoirBookPlan,
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_interview_editor import (
    MemoirInterviewEditorAnswerResponse,
    MemoirInterviewEditorAnswerParentUpdateRequest,
    MemoirInterviewEditorQuestionConvertToAnswerRequest,
    MemoirInterviewEditorQuestionCreateRequest,
    MemoirInterviewEditorQuestionResponse,
    MemoirInterviewEditorQuestionTextUpdateRequest,
    MemoirInterviewEditorReviewIssue,
    MemoirInterviewEditorSessionDetailResponse,
    MemoirInterviewEditorSessionListResponse,
    MemoirInterviewEditorSessionReviewResponse,
    MemoirInterviewEditorSessionSummaryResponse,
    MemoirInterviewEditorTimelineItemResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_source_links import included_chapters_by_utterance
from app.services.memoir.voice_transcripts import prune_transcript_versions_for_utterance
from app.services.memoir.material_roles import (
    is_countable_material,
    narrator_material_clause,
)
from app.services.memoir.utterance_editing_pipeline import (
    schedule_material_organization_for_ready_utterance,
)


QUESTION_ROLES = {
    MemoirChatMessageRole.INTERVIEWER.value,
    MemoirChatMessageRole.ASSISTANT.value,
}
MUTATION_SOURCE = "web_interview_editor"
QUESTION_CONVERTED_SOURCE = "interview_question_converted_to_answer"


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
            if text and is_countable_material(utterance, text):
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


def _mutation_actor(allow_admin: bool) -> str:
    return "admin" if allow_admin else "user"


async def _load_owned_session(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    voice_session_id: str,
    allow_admin: bool = False,
) -> MemoirVoiceSession:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    session = await db.scalar(
        select(MemoirVoiceSession)
        .where(
            MemoirVoiceSession.id == voice_session_id,
            MemoirVoiceSession.project_id == project_id,
        )
        .options(*_detail_options())
        .execution_options(populate_existing=True)
    )
    if not session:
        raise HTTPException(status_code=404, detail="访谈记录不存在")
    return session


async def _project_book_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id))


async def _load_owned_message(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    message_id: str,
    allow_admin: bool = False,
) -> MemoirChatMessage:
    filters = [
        MemoirChatMessage.id == message_id,
        MemoirChatMessage.project_id == project_id,
    ]
    if not allow_admin:
        filters.append(Project.user_id == user_id)
    message = await db.scalar(
        select(MemoirChatMessage)
        .join(Project, MemoirChatMessage.project_id == Project.id)
        .where(*filters)
        .options(
            selectinload(MemoirChatMessage.utterances),
            selectinload(MemoirChatMessage.voice_session),
        )
    )
    if not message:
        raise HTTPException(status_code=404, detail="访谈消息不存在")
    return message


def _metadata_with_update(metadata: dict | None, **values: object) -> dict:
    updated = dict(metadata or {})
    updated.update(values)
    return updated


def _now_iso(now: datetime) -> str:
    return now.isoformat(timespec="seconds") + "Z"


def _markdown_line(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _markdown_inline(value: str) -> str:
    return " ".join(_markdown_line(value).split())


def _markdown_list_block(value: str) -> str:
    return _markdown_line(value).replace("\n", "\n  ")


def _visible_utterance_exists_clause():
    return MemoirVoiceSession.utterances.any(
        (
            or_(
                func.trim(MemoirVoiceUtterance.transcript_raw) != "",
                func.trim(MemoirVoiceUtterance.transcript_edited) != "",
            )
            & narrator_material_clause()
        )
    )


def _session_summary_text(
    messages: list[MemoirChatMessage],
    answer_pairs: list[tuple[MemoirChatMessage, MemoirVoiceUtterance]],
) -> str:
    excerpts: list[str] = []
    for _message, utterance in sorted(
        answer_pairs,
        key=lambda item: (_message_order_key(item[0]), item[1].created_at, item[1].id),
    ):
        text = _markdown_inline(_utterance_text(utterance))
        if text:
            excerpts.append(text)
        if len(excerpts) >= 2:
            break
    if excerpts:
        return " / ".join(excerpts)[:180]

    last_message = next((message for message in reversed(messages) if _message_text(message)), None)
    return (_markdown_inline(_message_text(last_message))[:180] if last_message else "")


def _review_issue(code: str, severity: str, message: str, count: int) -> MemoirInterviewEditorReviewIssue:
    return MemoirInterviewEditorReviewIssue(
        code=code,
        severity=severity,
        message=message,
        count=count,
    )


def _answer_markdown(answer: MemoirInterviewEditorAnswerResponse, index: int) -> list[str]:
    labels: list[str] = [f"A{index}"]
    if answer.audio_duration_seconds:
        labels.append(f"音频 {answer.audio_duration_seconds} 秒")
    if answer.included_chapters:
        labels.append("章节：" + "、".join(chapter.title for chapter in answer.included_chapters))
    text = _markdown_list_block(answer.display_text)
    return [
        f"- {' / '.join(labels)}",
        "",
        f"  {text or '（暂无文字）'}",
    ]


def _session_review_markdown(
    *,
    project_title: str,
    detail: MemoirInterviewEditorSessionDetailResponse,
    generated_at: datetime,
) -> str:
    session = detail.session
    lines = [
        f"# {project_title} - 真人访谈整理稿",
        "",
        f"- 访谈 ID：{session.id}",
        f"- 生成时间：{_now_iso(generated_at)}",
        f"- 问题数：{session.question_count}",
        f"- 回答数：{session.answer_count}",
        f"- 未归属回答：{session.unassigned_answer_count}",
        "",
    ]
    for question_index, question in enumerate(detail.questions, start=1):
        lines.extend([
            f"## Q{question_index}. {_markdown_inline(question.text) or '（未命名问题）'}",
            "",
        ])
        if not question.answers:
            lines.extend(["- （暂无回答）", ""])
            continue
        for answer_index, answer in enumerate(question.answers, start=1):
            lines.extend(_answer_markdown(answer, answer_index))
            lines.append("")

    if detail.unassigned_answers:
        lines.extend(["## 未归属回答", ""])
        for answer_index, answer in enumerate(detail.unassigned_answers, start=1):
            lines.extend(_answer_markdown(answer, answer_index))
            lines.append("")

    return "\n".join(lines).strip() + "\n"


async def _next_turn_index(db: AsyncSession, voice_session_id: str) -> int:
    current = await db.scalar(
        select(func.max(MemoirChatMessage.turn_index)).where(
            MemoirChatMessage.voice_session_id == voice_session_id
        )
    )
    return 0 if current is None else int(current) + 1


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
        summary_text=_session_summary_text(messages, answer_pairs),
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
        turn_index=message.turn_index,
        created_at=message.created_at,
        updated_at=message.updated_at,
    )


async def list_interview_sessions(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    interview_type: str | None = None,
    effective_only: bool = True,
    limit: int = 50,
    offset: int = 0,
    allow_admin: bool = False,
) -> MemoirInterviewEditorSessionListResponse:
    await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    filters = [MemoirVoiceSession.project_id == project_id]
    if not allow_admin:
        filters.append(Project.user_id == user_id)
    if interview_type:
        filters.append(MemoirVoiceSession.interview_type == interview_type)
    if effective_only:
        filters.append(_visible_utterance_exists_clause())
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
    allow_admin: bool = False,
) -> MemoirInterviewEditorSessionDetailResponse:
    session = await _load_owned_session(
        db,
        user_id=user_id,
        project_id=project_id,
        voice_session_id=voice_session_id,
        allow_admin=allow_admin,
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
        if not is_countable_material(utterance, _utterance_text(utterance)):
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


async def get_interview_session_review(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    voice_session_id: str,
    allow_admin: bool = False,
) -> MemoirInterviewEditorSessionReviewResponse:
    project = await load_memoir_project(db, project_id, user_id, allow_admin=allow_admin)
    detail = await get_interview_session_detail(
        db,
        user_id=user_id,
        project_id=project_id,
        voice_session_id=voice_session_id,
        allow_admin=allow_admin,
    )
    answers = [
        *[answer for question in detail.questions for answer in question.answers],
        *detail.unassigned_answers,
    ]
    missing_audio_count = sum(1 for answer in answers if not answer.audio_file_id)
    not_included_answer_count = sum(1 for answer in answers if not answer.is_included_in_book)
    issues: list[MemoirInterviewEditorReviewIssue] = []
    if detail.session.question_count == 0:
        issues.append(_review_issue("no_questions", "warning", "这场访谈还没有可整理的问题。", 1))
    if detail.session.answer_count == 0:
        issues.append(_review_issue("no_answers", "warning", "这场访谈还没有可整理的被访者回答。", 1))
    if detail.session.unassigned_answer_count:
        issues.append(
            _review_issue(
                "unassigned_answers",
                "warning",
                "仍有回答没有归属到问题。",
                detail.session.unassigned_answer_count,
            )
        )
    if missing_audio_count:
        issues.append(
            _review_issue("missing_audio", "info", "部分回答缺少可回听音频。", missing_audio_count)
        )
    if not_included_answer_count:
        issues.append(
            _review_issue(
                "not_included_in_book",
                "info",
                "部分回答尚未归入章节素材。",
                not_included_answer_count,
            )
        )

    generated_at = utcnow_naive()
    return MemoirInterviewEditorSessionReviewResponse(
        project_id=project_id,
        voice_session_id=voice_session_id,
        project_title=project.title,
        generated_at=generated_at,
        question_count=detail.session.question_count,
        answer_count=detail.session.answer_count,
        unassigned_answer_count=detail.session.unassigned_answer_count,
        missing_audio_count=missing_audio_count,
        not_included_answer_count=not_included_answer_count,
        issues=issues,
        markdown=_session_review_markdown(
            project_title=project.title,
            detail=detail,
            generated_at=generated_at,
        ),
    )


async def create_interview_question(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirInterviewEditorQuestionCreateRequest,
    allow_admin: bool = False,
) -> MemoirInterviewEditorSessionDetailResponse:
    session = await _load_owned_session(
        db,
        user_id=user_id,
        project_id=project_id,
        voice_session_id=req.voice_session_id,
        allow_admin=allow_admin,
    )
    now = utcnow_naive()
    message = MemoirChatMessage(
        project_id=project_id,
        voice_session_id=session.id,
        role=MemoirChatMessageRole.INTERVIEWER.value,
        content_text=req.text,
        content_status=MemoirChatContentStatus.CORRECTED.value,
        guidance_intent="manual_question",
        turn_index=await _next_turn_index(db, session.id),
        metadata_json={
            "source": "web_interview_editor.manual_question",
            "speaker_role": "interviewer",
            "interview_type": session.interview_type,
            "created_by": _mutation_actor(allow_admin),
            "created_user_id": user_id,
            "created_from": MUTATION_SOURCE,
            "created_at": _now_iso(now),
        },
    )
    db.add(message)
    await db.commit()
    return await get_interview_session_detail(
        db,
        user_id=user_id,
        project_id=project_id,
        voice_session_id=session.id,
        allow_admin=allow_admin,
    )


async def update_interview_question_text(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    message_id: str,
    req: MemoirInterviewEditorQuestionTextUpdateRequest,
    allow_admin: bool = False,
) -> MemoirInterviewEditorSessionDetailResponse:
    message = await _load_owned_message(
        db,
        user_id=user_id,
        project_id=project_id,
        message_id=message_id,
        allow_admin=allow_admin,
    )
    if message.role not in QUESTION_ROLES:
        raise HTTPException(status_code=400, detail="只能编辑可整理的问题")
    now = utcnow_naive()
    original_text = message.content_text
    message.content_text = req.text
    message.content_status = MemoirChatContentStatus.CORRECTED.value
    metadata = dict(message.metadata_json or {})
    message.metadata_json = _metadata_with_update(
        metadata,
        text_updated_by=_mutation_actor(allow_admin),
        text_updated_user_id=user_id,
        text_updated_from=MUTATION_SOURCE,
        text_updated_at=_now_iso(now),
        text_original=metadata.get("text_original") or original_text,
    )

    for utterance in sorted(list(message.utterances), key=_utterance_order_key):
        version = MemoirTranscriptVersion(
            voice_session_id=message.voice_session_id,
            source_artifact_id=utterance.id,
            version_type=MemoirTranscriptVersionType.USER_EDITED.value,
            content=req.text,
            provider=None,
            created_by=MemoirCreatedBy.USER.value,
        )
        db.add(version)
        await db.flush()
        utterance.transcript_edited = req.text
        message.transcript_version_id = version.id
        await prune_transcript_versions_for_utterance(
            db,
            utterance_id=utterance.id,
            version_type=MemoirTranscriptVersionType.USER_EDITED,
        )

    await db.commit()
    return await get_interview_session_detail(
        db,
        user_id=user_id,
        project_id=project_id,
        voice_session_id=message.voice_session_id,
        allow_admin=allow_admin,
    )


async def convert_interview_question_to_answer(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    message_id: str,
    req: MemoirInterviewEditorQuestionConvertToAnswerRequest,
    allow_admin: bool = False,
) -> MemoirInterviewEditorSessionDetailResponse:
    message = await _load_owned_message(
        db,
        user_id=user_id,
        project_id=project_id,
        message_id=message_id,
        allow_admin=allow_admin,
    )
    if message.role not in QUESTION_ROLES:
        raise HTTPException(status_code=400, detail="只能把问题转成回答")

    child_answer_count = await db.scalar(
        select(func.count(MemoirChatMessage.id)).where(
            MemoirChatMessage.parent_message_id == message.id,
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
        )
    )
    if child_answer_count:
        raise HTTPException(status_code=400, detail="这个问题下还有回答，请先移动这些回答")

    utterances = sorted(list(message.utterances), key=_utterance_order_key)
    if not any(_utterance_text(utterance) for utterance in utterances):
        raise HTTPException(status_code=400, detail="这条问题没有可转成回答的识别文字")

    parent_id = req.parent_question_message_id
    if parent_id == message.id:
        raise HTTPException(status_code=400, detail="不能把回答归属到自己")
    if parent_id:
        parent_message = await _load_owned_message(
            db,
            user_id=user_id,
            project_id=project_id,
            message_id=parent_id,
            allow_admin=allow_admin,
        )
        if parent_message.voice_session_id != message.voice_session_id:
            raise HTTPException(status_code=400, detail="问题和回答必须属于同一次访谈")
        if parent_message.role not in QUESTION_ROLES:
            raise HTTPException(status_code=400, detail="目标消息不是可归属的问题")

    now = utcnow_naive()
    previous_role = message.role
    previous_parent_id = message.parent_message_id
    mutation_source = req.mutation_source or MUTATION_SOURCE
    text_for_answer = _message_text(message)
    message.role = MemoirChatMessageRole.USER.value
    message.parent_message_id = parent_id
    message.content_status = MemoirChatContentStatus.CORRECTED.value
    message.guidance_intent = "manual_converted_answer"
    metadata = dict(message.metadata_json or {})
    metadata.update(
        {
            "converted_from_role": previous_role,
            "converted_from_question": True,
            "converted_by": _mutation_actor(allow_admin),
            "converted_user_id": user_id,
            "converted_from": mutation_source,
            "converted_at": _now_iso(now),
            "previous_parent_message_id": previous_parent_id,
            "parent_message_id": parent_id,
            "speaker_role": "narrator",
        }
    )
    if req.reason:
        metadata["conversion_reason"] = req.reason
    message.metadata_json = metadata

    for utterance in utterances:
        if text_for_answer and not (utterance.transcript_edited or "").strip():
            version = MemoirTranscriptVersion(
                voice_session_id=message.voice_session_id,
                source_artifact_id=utterance.id,
                version_type=MemoirTranscriptVersionType.USER_EDITED.value,
                content=text_for_answer,
                provider=None,
                created_by=MemoirCreatedBy.USER.value,
            )
            db.add(version)
            await db.flush()
            utterance.transcript_edited = text_for_answer
            message.transcript_version_id = version.id
            await prune_transcript_versions_for_utterance(
                db,
                utterance_id=utterance.id,
                version_type=MemoirTranscriptVersionType.USER_EDITED,
            )

    for utterance in utterances:
        await schedule_material_organization_for_ready_utterance(
            db,
            project_id=project_id,
            utterance=utterance,
            source=QUESTION_CONVERTED_SOURCE,
        )

    await db.commit()
    return await get_interview_session_detail(
        db,
        user_id=user_id,
        project_id=project_id,
        voice_session_id=message.voice_session_id,
        allow_admin=allow_admin,
    )


async def update_answer_parent_question(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    message_id: str,
    req: MemoirInterviewEditorAnswerParentUpdateRequest,
    allow_admin: bool = False,
) -> MemoirInterviewEditorSessionDetailResponse:
    message = await _load_owned_message(
        db,
        user_id=user_id,
        project_id=project_id,
        message_id=message_id,
        allow_admin=allow_admin,
    )
    if message.role != MemoirChatMessageRole.USER.value:
        raise HTTPException(status_code=400, detail="只能调整被访者回答的归属")

    parent_id = req.parent_question_message_id
    if parent_id:
        parent_message = await _load_owned_message(
            db,
            user_id=user_id,
            project_id=project_id,
            message_id=parent_id,
            allow_admin=allow_admin,
        )
        if parent_message.voice_session_id != message.voice_session_id:
            raise HTTPException(status_code=400, detail="问题和回答必须属于同一次访谈")
        if parent_message.role not in QUESTION_ROLES:
            raise HTTPException(status_code=400, detail="目标消息不是可归属的问题")

    now = utcnow_naive()
    previous_parent_id = message.parent_message_id
    message.parent_message_id = parent_id
    metadata = dict(message.metadata_json or {})
    if "captured_parent_message_id" not in metadata:
        metadata["captured_parent_message_id"] = previous_parent_id
    metadata.update(
        {
            "relation_updated_by": _mutation_actor(allow_admin),
            "relation_updated_user_id": user_id,
            "relation_updated_from": MUTATION_SOURCE,
            "relation_updated_at": _now_iso(now),
            "previous_parent_message_id": previous_parent_id,
            "parent_message_id": parent_id,
        }
    )
    if req.reason:
        metadata["relation_update_reason"] = req.reason
    message.metadata_json = metadata

    await db.commit()
    return await get_interview_session_detail(
        db,
        user_id=user_id,
        project_id=project_id,
        voice_session_id=message.voice_session_id,
        allow_admin=allow_admin,
    )
