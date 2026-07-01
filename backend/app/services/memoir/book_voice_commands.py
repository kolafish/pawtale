from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.memoir import MemoirBookChapterDraft, MemoirBookPlan, MemoirBookPlanChapter
from app.schemas.memoir_book import (
    MemoirBookVoiceCommandExecuteRequest,
    MemoirBookVoiceCommandExecuteResponse,
    MemoirBookVoiceCommandParseRequest,
    MemoirBookVoiceCommandParseResponse,
)
from app.services.ai_types import AITextClient
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_source_links import source_count_by_chapter
from app.services.memoir.book_voice_command_executor import execute_confirmed_book_voice_command
from app.services.memoir.book_voice_command_parser import (
    parse_payload_with_ai,
    response_from_payload,
)


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

    source_counts = await source_count_by_chapter(db, plan)
    payload = await parse_payload_with_ai(
        req,
        plan,
        source_count_by_chapter=source_counts,
        ai_client=ai_client,
    )
    return response_from_payload(
        req.text,
        plan,
        req.current_chapter_id,
        payload,
        req.book_context,
        source_count_by_chapter=source_counts,
    )


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
