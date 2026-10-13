from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import MemoirFrameworkUserResolution
from app.domain.memoir.types import (
    MemoirChatMessageRole,
    MemoirGuidanceMode,
    MemoirSessionGoal,
    MemoirVoiceSessionStatus,
)
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.memoir_narrative import MemoirFrameworkItem, MemoirProjectFramework
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirVoiceProgressResponse,
    MemoirVoiceSessionListResponse,
    MemoirVoiceSessionSummaryResponse,
    MemoirVoiceSessionCreate,
    MemoirVoiceSessionDetailResponse,
    MemoirVoiceSessionStopRequest,
)
from app.services.memoir import (
    completion_interviews,
    completion_issues,
    progress_snapshot,
    story_tree_growth,
    share_video_candidate_scheduling,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir.material_roles import (
    is_countable_material,
    narrator_material_clause,
)
from app.services.memoir.transcript_projection import current_utterance_text


def _detail_options():
    return (
        selectinload(MemoirVoiceSession.messages),
        selectinload(MemoirVoiceSession.utterances),
    )


async def _load_voice_session(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
) -> MemoirVoiceSession:
    result = await db.execute(
        select(MemoirVoiceSession)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(MemoirVoiceSession.id == voice_session_id, Project.user_id == user_id)
        .options(*_detail_options())
    )
    voice_session = result.scalar_one_or_none()
    if not voice_session:
        raise HTTPException(status_code=404, detail="本次语音记录不存在")
    return voice_session


async def create_voice_session(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: MemoirVoiceSessionCreate,
) -> MemoirVoiceSessionDetailResponse:
    project = await load_memoir_project(db, project_id, user_id)

    completion_issue = None
    if req.session_goal == MemoirSessionGoal.BOOK_COMPLETION:
        if not req.current_completion_issue_id:
            raise HTTPException(
                status_code=409,
                detail="本次要补的问题尚未准备好，请从首页重新进入补全成书",
            )
        completion_issue = await completion_issues.get_issue(
            db,
            project_id=project.id,
            issue_id=req.current_completion_issue_id,
        )

    anchor_item = None
    if req.anchor_framework_item_id:
        anchor_item = await db.scalar(
            select(MemoirFrameworkItem)
            .join(
                MemoirProjectFramework,
                MemoirProjectFramework.id
                == MemoirFrameworkItem.project_framework_id,
            )
            .where(
                MemoirProjectFramework.project_id == project.id,
                MemoirFrameworkItem.id == req.anchor_framework_item_id,
            )
        )
        if anchor_item is None:
            raise HTTPException(status_code=404, detail="话题方向不存在")
        if (
            anchor_item.user_resolution
            != MemoirFrameworkUserResolution.NONE.value
        ):
            raise HTTPException(
                status_code=409,
                detail="这个话题方向当前已被收起，请先修改您的选择",
            )
        if (
            req.guidance_mode != MemoirGuidanceMode.GUIDED_INTERVIEW
            or req.session_goal != MemoirSessionGoal.OPEN_NARRATION
        ):
            raise HTTPException(
                status_code=409,
                detail="话题方向只能用于按照主题来问的自由访谈",
            )

    now = datetime.now(UTC).replace(tzinfo=None)
    voice_session = MemoirVoiceSession(
        project_id=project.id,
        status=MemoirVoiceSessionStatus.CREATED.value,
        guidance_mode=req.guidance_mode.value,
        interview_type=req.interview_type.value,
        session_goal=req.session_goal.value,
        anchor_framework_item_id=(
            anchor_item.id if anchor_item is not None else None
        ),
        current_guidance_module=req.current_guidance_module,
        provider=req.provider,
        input_format=req.input_format,
        started_at=now,
        metadata_json=req.metadata_json,
    )
    db.add(voice_session)
    await db.flush()
    if completion_issue is not None:
        await completion_interviews.start_issue_for_session(
            db,
            issue=completion_issue,
            voice_session=voice_session,
        )
    await db.commit()
    return await get_voice_session_detail(db, voice_session.id, user_id)


async def get_voice_session_detail(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
) -> MemoirVoiceSessionDetailResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    return MemoirVoiceSessionDetailResponse(
        session=voice_session,
        messages=list(voice_session.messages),
        utterances=list(voice_session.utterances),
    )


def _char_count(text: str) -> int:
    return len("".join(str(text or "").split()))


def _visible_utterances(voice_session: MemoirVoiceSession) -> list[MemoirVoiceUtterance]:
    return [
        utterance
        for utterance in voice_session.utterances
        if is_countable_material(utterance, current_utterance_text(utterance))
    ]


def _assistant_message_expects_answer(message: MemoirChatMessage) -> bool:
    metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
    return metadata.get("expects_answer") is not False


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


def _utterance_duration_seconds(utterance: MemoirVoiceUtterance) -> int:
    if utterance.start_ms is None or utterance.end_ms is None:
        return 0
    return max(0, round((utterance.end_ms - utterance.start_ms) / 1000))


def _session_summary(voice_session: MemoirVoiceSession) -> MemoirVoiceSessionSummaryResponse:
    messages = list(voice_session.messages)
    utterances = _visible_utterances(voice_session)
    user_messages = [
        message for message in messages
        if message.role == MemoirChatMessageRole.USER.value and message.content_text.strip()
    ]
    assistant_messages = [
        message for message in messages
        if message.role == MemoirChatMessageRole.ASSISTANT.value and message.content_text.strip()
        and _assistant_message_expects_answer(message)
    ]
    ordered_messages = sorted(messages, key=lambda message: (message.turn_index, message.created_at, message.id))
    last_message = ordered_messages[-1] if ordered_messages else None
    return MemoirVoiceSessionSummaryResponse(
        id=voice_session.id,
        project_id=voice_session.project_id,
        status=voice_session.status,
        guidance_mode=voice_session.guidance_mode,
        interview_type=voice_session.interview_type,
        session_goal=voice_session.session_goal,
        current_completion_issue_id=voice_session.current_completion_issue_id,
        anchor_framework_item_id=voice_session.anchor_framework_item_id,
        current_guidance_module=voice_session.current_guidance_module,
        started_at=voice_session.started_at,
        stopped_at=voice_session.stopped_at,
        created_at=voice_session.created_at,
        updated_at=voice_session.updated_at,
        user_message_count=len(user_messages),
        assistant_message_count=len(assistant_messages),
        utterance_count=len(utterances),
        total_user_char_count=sum(_char_count(current_utterance_text(utterance)) for utterance in utterances),
        total_audio_seconds=sum(_utterance_duration_seconds(utterance) for utterance in utterances),
        last_message_text=(last_message.content_text if last_message else "")[:120],
        last_message_role=last_message.role if last_message else None,
        last_message_at=last_message.created_at if last_message else None,
    )


async def list_voice_sessions(
    db: AsyncSession,
    user_id: str,
    *,
    project_id: str | None = None,
    effective_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> MemoirVoiceSessionListResponse:
    filters = [Project.user_id == user_id]
    if project_id:
        await load_memoir_project(db, project_id, user_id)
        filters.append(Project.id == project_id)
    if effective_only:
        filters.append(_visible_utterance_exists_clause())
    total = await db.scalar(
        select(func.count(MemoirVoiceSession.id))
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
    )
    result = await db.execute(
        select(MemoirVoiceSession)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
        .options(*_detail_options())
        .order_by(MemoirVoiceSession.updated_at.desc(), MemoirVoiceSession.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return MemoirVoiceSessionListResponse(
        items=[_session_summary(voice_session) for voice_session in result.scalars().all()],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def get_voice_progress(
    db: AsyncSession,
    user_id: str,
    *,
    project_id: str | None = None,
) -> MemoirVoiceProgressResponse:
    return await progress_snapshot.get_voice_progress_from_snapshot(
        db,
        user_id=user_id,
        project_id=project_id,
    )


async def mark_voice_session_listening(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
) -> MemoirVoiceSessionDetailResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    if voice_session.status == MemoirVoiceSessionStatus.CREATED.value:
        voice_session.status = MemoirVoiceSessionStatus.LISTENING.value
        voice_session.error_message = ""
    await db.commit()
    return await get_voice_session_detail(db, voice_session.id, user_id)


async def stop_voice_session(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceSessionStopRequest,
) -> MemoirVoiceSessionDetailResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    if voice_session.status not in {
        MemoirVoiceSessionStatus.STOPPED.value,
        MemoirVoiceSessionStatus.FAILED.value,
    }:
        await completion_interviews.release_session_issue(
            db,
            voice_session=voice_session,
        )
        voice_session.status = (
            MemoirVoiceSessionStatus.FAILED.value
            if req.error_message
            else MemoirVoiceSessionStatus.STOPPED.value
        )
        voice_session.error_message = req.error_message or ""
        voice_session.stopped_at = datetime.now(UTC).replace(tzinfo=None)
        if not req.error_message:
            await story_tree_growth.schedule_story_tree_growth(
                db,
                project_id=voice_session.project_id,
                voice_session_id=voice_session.id,
                source="voice_session_stopped",
            )
            await db.flush()
            await share_video_candidate_scheduling.schedule_share_video_candidate_evaluation(
                db,
                project_id=voice_session.project_id,
                voice_session_id=voice_session.id,
            )
    await db.commit()
    return await get_voice_session_detail(db, voice_session.id, user_id)
