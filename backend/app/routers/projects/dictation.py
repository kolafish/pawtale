from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.domain.projects.types import ProjectType
from app.models.projects import Document, DocumentGroup, Project
from app.routers.dictation_gateway import run_dictation_gateway
from app.routers.websocket_auth import authenticate_websocket, reject_websocket


router = APIRouter()


@router.websocket("/{project_id}/documents/{document_id}/dictation")
async def stream_article_dictation(
    websocket: WebSocket,
    project_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
):
    current_user = await authenticate_websocket(websocket, db)
    if current_user is None:
        await reject_websocket(websocket, "voice_gateway_auth_failed")
        return
    if not await _article_document_is_available(
        db,
        project_id=project_id,
        document_id=document_id,
        user_id=current_user.id,
    ):
        await reject_websocket(websocket, "article_dictation_unavailable")
        return

    await run_dictation_gateway(
        websocket,
        user_id=current_user.id,
        capacity_prefix="article-dictation",
        diagnostics_context={
            "gateway": "article_dictation",
            "project_id": project_id,
            "document_id": document_id,
        },
    )


async def _article_document_is_available(
    db: AsyncSession,
    *,
    project_id: str,
    document_id: str,
    user_id: str,
) -> bool:
    result = await db.execute(
        select(Document.id)
        .join(DocumentGroup, Document.group_id == DocumentGroup.id)
        .join(Project, DocumentGroup.project_id == Project.id)
        .where(
            Project.id == project_id,
            Project.user_id == user_id,
            Project.type == ProjectType.ARTICLE.value,
            Document.id == document_id,
        )
    )
    return result.scalar_one_or_none() is not None
