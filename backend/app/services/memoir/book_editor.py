from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.memoir_book_editor import MemoirBookEditorResponse
from app.services.memoir import book_drafts, book_outputs, book_plans, chapter_source_editor


T = TypeVar("T")


async def _optional_resource(resource: Awaitable[T]) -> T | None:
    try:
        return await resource
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return None


async def get_book_editor(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    allow_admin: bool = False,
) -> MemoirBookEditorResponse:
    source_editor = await chapter_source_editor.get_chapter_source_editor(
        db,
        user_id=user_id,
        project_id=project_id,
        allow_admin=allow_admin,
    )
    plan = await _optional_resource(
        book_plans.get_book_plan(
            db,
            user_id=user_id,
            project_id=project_id,
            allow_admin=allow_admin,
        )
    )
    drafts = []
    if plan:
        for chapter in plan.chapters:
            draft = await _optional_resource(
                book_drafts.get_chapter_draft(
                    db,
                    user_id=user_id,
                    project_id=project_id,
                    chapter_id=chapter.id,
                    allow_admin=allow_admin,
                )
            )
            if draft:
                drafts.append(draft)
    output = await _optional_resource(
        book_outputs.get_book_output(
            db,
            user_id=user_id,
            project_id=project_id,
            allow_admin=allow_admin,
        )
    )
    return MemoirBookEditorResponse(
        project_id=project_id,
        plan=plan,
        chapter_source_editor=source_editor,
        chapter_drafts=drafts,
        output=output,
    )
