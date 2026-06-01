from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.projects import (
    DocumentReferencesUpdate,
    ExportGlobalRequest,
    ExportGlobalResponse,
    ImportGlobalRequest,
    ImportPreviewResponse,
    ReferenceItemCreate,
    ReferenceItemResponse,
    ReferenceItemUpdate,
)
from app.services.projects import references as project_references


router = APIRouter()


@router.get("/{project_id}/references", response_model=list[ReferenceItemResponse])
async def list_references(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_references.list_references(db, project_id, current_user.id)


@router.post("/{project_id}/references", response_model=ReferenceItemResponse, status_code=201)
async def create_reference(
    project_id: str,
    req: ReferenceItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_references.create_reference(db, project_id, current_user.id, req)


@router.post("/{project_id}/references/import-global", response_model=list[ReferenceItemResponse])
async def import_from_global(
    project_id: str,
    req: ImportGlobalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_references.import_from_global(db, project_id, current_user.id, req)


@router.post("/{project_id}/references/import-preview", response_model=ImportPreviewResponse)
async def import_preview(
    project_id: str,
    req: ImportGlobalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_references.import_preview(db, project_id, current_user.id, req)


@router.post("/{project_id}/references/{ref_id}/export-global", response_model=ExportGlobalResponse)
async def export_to_global(
    project_id: str,
    ref_id: str,
    req: ExportGlobalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_references.export_to_global(db, project_id, ref_id, current_user.id, req)


@router.post("/{project_id}/references/{ref_id}/sync-global", response_model=ReferenceItemResponse)
async def sync_reference_from_global(
    project_id: str,
    ref_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_references.sync_reference_from_global(db, project_id, ref_id, current_user.id)


@router.put("/{project_id}/references/{ref_id}", response_model=ReferenceItemResponse)
async def update_reference(
    project_id: str,
    ref_id: str,
    req: ReferenceItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_references.update_reference(db, project_id, ref_id, current_user.id, req)


@router.delete("/{project_id}/references/{ref_id}", status_code=204)
async def delete_reference(
    project_id: str,
    ref_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await project_references.delete_reference(db, project_id, ref_id, current_user.id)


@router.get(
    "/{project_id}/documents/{document_id}/references",
    response_model=list[ReferenceItemResponse],
)
async def get_document_references(
    project_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_references.get_document_references(db, project_id, document_id, current_user.id)


@router.put("/{project_id}/documents/{document_id}/references", status_code=200)
async def set_document_references(
    project_id: str,
    document_id: str,
    req: DocumentReferencesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await project_references.set_document_references(
        db,
        project_id,
        document_id,
        current_user.id,
        req,
    )
