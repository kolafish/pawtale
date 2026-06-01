from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.projects import (
    DocumentCreate,
    DocumentReorderItem,
    DocumentResponse,
    DocumentUpdate,
    GroupCreate,
    GroupReorderItem,
    GroupResponse,
    GroupUpdate,
)
from app.services.projects import structure as project_structure


router = APIRouter()


@router.post("/{project_id}/groups", response_model=GroupResponse, status_code=201)
async def create_group(
    project_id: str,
    req: GroupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_structure.create_group(db, project_id, current_user.id, req)


@router.put("/{project_id}/groups/reorder", status_code=200)
async def reorder_groups(
    project_id: str,
    items: list[GroupReorderItem],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_structure.reorder_groups(db, project_id, current_user.id, items)


@router.put("/{project_id}/groups/{group_id}", response_model=GroupResponse)
async def update_group(
    project_id: str,
    group_id: str,
    req: GroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_structure.update_group(db, project_id, group_id, current_user.id, req)


@router.delete("/{project_id}/groups/{group_id}", status_code=204)
async def delete_group(
    project_id: str,
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await project_structure.delete_group(db, project_id, group_id, current_user.id)


@router.post(
    "/{project_id}/groups/{group_id}/documents",
    response_model=DocumentResponse,
    status_code=201,
)
async def create_document(
    project_id: str,
    group_id: str,
    req: DocumentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_structure.create_document(db, project_id, group_id, current_user.id, req)


@router.put("/{project_id}/documents/reorder", status_code=200)
async def reorder_documents(
    project_id: str,
    items: list[DocumentReorderItem],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_structure.reorder_documents(db, project_id, current_user.id, items)


@router.get("/{project_id}/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    project_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_structure.get_document_response(db, project_id, document_id, current_user.id)


@router.put("/{project_id}/documents/{document_id}", response_model=DocumentResponse)
async def update_document(
    project_id: str,
    document_id: str,
    req: DocumentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_structure.update_document(db, project_id, document_id, current_user.id, req)


@router.delete("/{project_id}/documents/{document_id}", status_code=204)
async def delete_document(
    project_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await project_structure.delete_document(db, project_id, document_id, current_user.id)
