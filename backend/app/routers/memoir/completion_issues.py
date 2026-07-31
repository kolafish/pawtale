from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, is_admin_user
from app.database import get_db
from app.models.projects import Project
from app.models.users import User
from app.schemas.memoir_completion_issues import (
    MemoirCompletionIssueCloseRequest,
    MemoirCompletionIssueCollectionResponse,
    MemoirCompletionIssueResponse,
)
from app.services.memoir.access import load_memoir_project
from app.services.memoir import completion_issues


router = APIRouter(tags=["memoir-completion-issues"])


async def _authorize(
    db: AsyncSession,
    *,
    project_id: str,
    current_user: User,
) -> Project:
    return await load_memoir_project(
        db,
        project_id,
        current_user.id,
        allow_admin=is_admin_user(current_user),
    )


@router.get(
    "/projects/{project_id}/completion-issues",
    response_model=MemoirCompletionIssueCollectionResponse,
)
async def list_memoir_completion_issues(
    project_id: str,
    chapter_id: str | None = Query(default=None),
    include_closed: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _authorize(db, project_id=project_id, current_user=current_user)
    return await completion_issues.collection_response(
        db,
        project_id=project_id,
        chapter_id=chapter_id,
        include_closed=include_closed,
    )


@router.post(
    "/projects/{project_id}/completion-issues/{issue_id}/close",
    response_model=MemoirCompletionIssueResponse,
)
async def close_memoir_completion_issue(
    project_id: str,
    issue_id: str,
    req: MemoirCompletionIssueCloseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _authorize(db, project_id=project_id, current_user=current_user)
    issue = await completion_issues.close_issue(
        db,
        project_id=project_id,
        issue_id=issue_id,
        outcome=req.outcome,
        note=req.note,
        answered_material_id=req.answered_material_id,
    )
    await db.commit()
    return completion_issues.issue_response(issue)
