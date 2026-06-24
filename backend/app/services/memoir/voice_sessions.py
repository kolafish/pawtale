from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import MemoirChatMessageRole, MemoirVoiceSessionStatus
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirVoiceModuleProgressResponse,
    MemoirVoiceProgressResponse,
    MemoirVoiceSessionListResponse,
    MemoirVoiceSessionSummaryResponse,
    MemoirVoiceSessionCreate,
    MemoirVoiceSessionDetailResponse,
    MemoirVoiceSessionStopRequest,
)
from app.services.memoir.access import load_memoir_project


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

    now = datetime.utcnow()
    voice_session = MemoirVoiceSession(
        project_id=project.id,
        status=MemoirVoiceSessionStatus.CREATED.value,
        guidance_mode=req.guidance_mode.value,
        current_guidance_module=req.current_guidance_module,
        provider=req.provider,
        input_format=req.input_format,
        started_at=now,
        metadata_json=req.metadata_json,
    )
    db.add(voice_session)
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


def _utterance_text(utterance: MemoirVoiceUtterance) -> str:
    return (utterance.transcript_edited or "").strip() or (utterance.transcript_raw or "").strip()


def _visible_utterances(voice_session: MemoirVoiceSession) -> list[MemoirVoiceUtterance]:
    return [utterance for utterance in voice_session.utterances if _utterance_text(utterance)]


def _assistant_message_expects_answer(message: MemoirChatMessage) -> bool:
    metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
    return metadata.get("expects_answer") is not False


def _visible_utterance_exists_clause():
    return MemoirVoiceSession.utterances.any(
        or_(
            func.trim(MemoirVoiceUtterance.transcript_raw) != "",
            func.trim(MemoirVoiceUtterance.transcript_edited) != "",
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
        current_guidance_module=voice_session.current_guidance_module,
        started_at=voice_session.started_at,
        stopped_at=voice_session.stopped_at,
        created_at=voice_session.created_at,
        updated_at=voice_session.updated_at,
        user_message_count=len(user_messages),
        assistant_message_count=len(assistant_messages),
        utterance_count=len(utterances),
        total_user_char_count=sum(_char_count(_utterance_text(utterance)) for utterance in utterances),
        total_audio_seconds=sum(_utterance_duration_seconds(utterance) for utterance in utterances),
        last_message_text=(last_message.content_text if last_message else "")[:120],
        last_message_role=last_message.role if last_message else None,
        last_message_at=last_message.created_at if last_message else None,
    )


def _is_effective_session(summary: MemoirVoiceSessionSummaryResponse) -> bool:
    return summary.utterance_count > 0


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
    filters = [Project.user_id == user_id]
    if project_id:
        await load_memoir_project(db, project_id, user_id)
        filters.append(Project.id == project_id)
    result = await db.execute(
        select(MemoirVoiceSession)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(*filters)
        .options(*_detail_options())
        .order_by(MemoirVoiceSession.updated_at.desc(), MemoirVoiceSession.created_at.desc())
    )
    sessions = result.scalars().all()
    module_stats: dict[str, dict[str, int]] = {}
    active_statuses = {
        MemoirVoiceSessionStatus.CREATED.value,
        MemoirVoiceSessionStatus.LISTENING.value,
        MemoirVoiceSessionStatus.SPEAKING.value,
    }
    effective_entries: list[tuple[MemoirVoiceSession, MemoirVoiceSessionSummaryResponse]] = []
    for voice_session in sessions:
        summary = _session_summary(voice_session)
        if _is_effective_session(summary):
            effective_entries.append((voice_session, summary))
    user_message_count = sum(summary.user_message_count for _, summary in effective_entries)
    assistant_message_count = sum(summary.assistant_message_count for _, summary in effective_entries)
    utterance_count = sum(summary.utterance_count for _, summary in effective_entries)
    total_user_char_count = sum(summary.total_user_char_count for _, summary in effective_entries)
    total_audio_seconds = sum(summary.total_audio_seconds for _, summary in effective_entries)
    for voice_session, summary in effective_entries:
        module = voice_session.current_guidance_module or (
            "free_narration" if voice_session.guidance_mode == "free_narration" else "unassigned"
        )
        stats = module_stats.setdefault(module, {"sessions": 0, "utterances": 0, "chars": 0})
        stats["sessions"] += 1
        stats["utterances"] += summary.utterance_count
        stats["chars"] += summary.total_user_char_count

    return MemoirVoiceProgressResponse(
        project_id=project_id,
        session_count=len(effective_entries),
        active_session_count=sum(1 for session, _ in effective_entries if session.status in active_statuses),
        stopped_session_count=sum(1 for session, _ in effective_entries if session.status == MemoirVoiceSessionStatus.STOPPED.value),
        failed_session_count=sum(1 for session, _ in effective_entries if session.status == MemoirVoiceSessionStatus.FAILED.value),
        utterance_count=utterance_count,
        user_message_count=user_message_count,
        assistant_message_count=assistant_message_count,
        total_user_char_count=total_user_char_count,
        total_audio_seconds=total_audio_seconds,
        last_session_at=effective_entries[0][0].updated_at if effective_entries else None,
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
        voice_session.status = (
            MemoirVoiceSessionStatus.FAILED.value
            if req.error_message
            else MemoirVoiceSessionStatus.STOPPED.value
        )
        voice_session.error_message = req.error_message or ""
        voice_session.stopped_at = datetime.utcnow()
    await db.commit()
    return await get_voice_session_detail(db, voice_session.id, user_id)
