from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.projects import (
    AnalyzeApplyRequest,
    AnalyzeApplyResponse,
    DocumentAIContinueRequest,
    DocumentAIContinueResponse,
    DocumentAIReviewRequest,
    DocumentAIReviewResponse,
    DocumentAIReviseMultiRequest,
    DocumentAIReviseMultiResponse,
    DocumentAIReviseRequest,
    DocumentAIReviseResponse,
    ProjectAnalyzeRequest,
    ProjectAnalyzeResponse,
)
from app.services.projects import ai as project_ai


router = APIRouter()


@router.post("/{project_id}/analyze", response_model=ProjectAnalyzeResponse, response_model_exclude_none=True)
async def analyze_project(
    project_id: str,
    req: ProjectAnalyzeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_ai.analyze_project(db, project_id, current_user.id, req)


@router.post("/{project_id}/analyze/apply", response_model=AnalyzeApplyResponse)
async def apply_analysis(
    project_id: str,
    req: AnalyzeApplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_ai.apply_analysis(db, project_id, current_user.id, req)


@router.post(
    "/{project_id}/documents/{document_id}/ai-continue",
    response_model=DocumentAIContinueResponse,
    response_model_exclude_none=True,
)
async def ai_continue_document(
    project_id: str,
    document_id: str,
    req: DocumentAIContinueRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_ai.ai_continue_document(db, project_id, document_id, current_user.id, req)


@router.post(
    "/{project_id}/documents/{document_id}/ai-revise",
    response_model=DocumentAIReviseResponse,
    response_model_exclude_none=True,
)
async def ai_revise_document(
    project_id: str,
    document_id: str,
    req: DocumentAIReviseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_ai.ai_revise_document(db, project_id, document_id, current_user.id, req)


@router.post(
    "/{project_id}/documents/{document_id}/ai-review",
    response_model=DocumentAIReviewResponse,
    response_model_exclude_none=True,
)
async def ai_review_document(
    project_id: str,
    document_id: str,
    req: DocumentAIReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_ai.ai_review_document(
        db,
        project_id,
        document_id,
        current_user.id,
        req,
    )


@router.post(
    "/{project_id}/documents/{document_id}/ai-revise-multi",
    response_model=DocumentAIReviseMultiResponse,
    response_model_exclude_none=True,
)
async def ai_revise_document_multi(
    project_id: str,
    document_id: str,
    req: DocumentAIReviseMultiRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_ai.ai_revise_document_multi(db, project_id, document_id, current_user.id, req)
