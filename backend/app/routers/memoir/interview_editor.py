from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.memoir_interview_editor import (
    MemoirInterviewEditorAnswerParentUpdateRequest,
    MemoirInterviewEditorQuestionCreateRequest,
    MemoirInterviewEditorQuestionTextUpdateRequest,
    MemoirInterviewEditorSessionDetailResponse,
    MemoirInterviewEditorSessionListResponse,
    MemoirInterviewEditorSessionReviewResponse,
)
from app.services.memoir import interview_editor as memoir_interview_editor


router = APIRouter(tags=["memoir-interview-editor"])


@router.get(
    "/projects/{project_id}/interview-editor/sessions",
    response_model=MemoirInterviewEditorSessionListResponse,
)
async def list_memoir_interview_editor_sessions(
    project_id: str,
    interview_type: Literal["human_interview", "ai_interview"] | None = "human_interview",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_interview_editor.list_interview_sessions(
        db,
        user_id=current_user.id,
        project_id=project_id,
        interview_type=interview_type,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/projects/{project_id}/interview-editor/sessions/{voice_session_id}",
    response_model=MemoirInterviewEditorSessionDetailResponse,
)
async def get_memoir_interview_editor_session(
    project_id: str,
    voice_session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_interview_editor.get_interview_session_detail(
        db,
        user_id=current_user.id,
        project_id=project_id,
        voice_session_id=voice_session_id,
    )


@router.get(
    "/projects/{project_id}/interview-editor/sessions/{voice_session_id}/review",
    response_model=MemoirInterviewEditorSessionReviewResponse,
)
async def get_memoir_interview_editor_session_review(
    project_id: str,
    voice_session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_interview_editor.get_interview_session_review(
        db,
        user_id=current_user.id,
        project_id=project_id,
        voice_session_id=voice_session_id,
    )


@router.post(
    "/projects/{project_id}/interview-editor/questions",
    response_model=MemoirInterviewEditorSessionDetailResponse,
    status_code=201,
)
async def create_memoir_interview_editor_question(
    project_id: str,
    req: MemoirInterviewEditorQuestionCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_interview_editor.create_interview_question(
        db,
        user_id=current_user.id,
        project_id=project_id,
        req=req,
    )


@router.patch(
    "/projects/{project_id}/interview-editor/questions/{message_id}",
    response_model=MemoirInterviewEditorSessionDetailResponse,
)
async def update_memoir_interview_editor_question_text(
    project_id: str,
    message_id: str,
    req: MemoirInterviewEditorQuestionTextUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_interview_editor.update_interview_question_text(
        db,
        user_id=current_user.id,
        project_id=project_id,
        message_id=message_id,
        req=req,
    )


@router.patch(
    "/projects/{project_id}/interview-editor/answers/{message_id}/parent",
    response_model=MemoirInterviewEditorSessionDetailResponse,
)
async def update_memoir_interview_editor_answer_parent(
    project_id: str,
    message_id: str,
    req: MemoirInterviewEditorAnswerParentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await memoir_interview_editor.update_answer_parent_question(
        db,
        user_id=current_user.id,
        project_id=project_id,
        message_id=message_id,
        req=req,
    )
