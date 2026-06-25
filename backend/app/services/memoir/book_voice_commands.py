from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.memoir import MemoirBookChapterDraft, MemoirBookPlan, MemoirBookPlanChapter
from app.schemas.memoir_book import (
    MemoirBookAudioResponse,
    MemoirBookVoiceAssistantTTSRequest,
    MemoirBookVoiceCommandExecuteRequest,
    MemoirBookVoiceCommandExecuteResponse,
    MemoirBookVoiceCommandParseRequest,
    MemoirBookVoiceCommandParseResponse,
)
from app.services.ai_types import AITextClient
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_audio import audio_response, discard_staged_audio_files, synthesize_text_audio
from app.services.memoir.book_voice_command_executor import execute_confirmed_book_voice_command
from app.services.memoir.book_voice_command_parser import (
    parse_payload_with_ai,
    response_from_payload,
)
from app.services.memoir.voice_tts import TTSClient


async def parse_book_voice_command(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookVoiceCommandParseRequest,
    ai_client: AITextClient | None = None,
) -> MemoirBookVoiceCommandParseResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await load_book_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")

    payload = await parse_payload_with_ai(req, plan, ai_client=ai_client)
    return response_from_payload(req.text, plan, req.current_chapter_id, payload, req.book_context)


async def execute_book_voice_command(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookVoiceCommandExecuteRequest,
) -> MemoirBookVoiceCommandExecuteResponse:
    await load_memoir_project(db, project_id, user_id)
    return await execute_confirmed_book_voice_command(
        db,
        user_id=user_id,
        project_id=project_id,
        req=req,
    )


async def synthesize_book_voice_assistant_audio(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookVoiceAssistantTTSRequest,
    tts_client: TTSClient | None = None,
) -> MemoirBookAudioResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await load_book_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")

    synthesis = await synthesize_text_audio(
        db,
        user_id=user_id,
        text=req.text,
        voice=req.voice,
        speed_ratio=req.speed_ratio,
        tts_client=tts_client,
    )
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        await discard_staged_audio_files(synthesis.staged_audio)
        raise
    return audio_response(synthesis)


async def load_book_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions)
        )
        .execution_options(populate_existing=True)
    )
