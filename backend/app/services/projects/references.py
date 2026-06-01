from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import DocumentReference, ReferenceItem
from app.schemas.projects import (
    DocumentReferencesUpdate,
    ReferenceItemCreate,
    ReferenceItemResponse,
    ReferenceItemUpdate,
)
from app.services.projects.access import get_document, get_reference, load_project


async def list_references(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> list[ReferenceItemResponse]:
    await load_project(db, project_id, user_id)
    result = await db.execute(
        select(ReferenceItem)
        .where(ReferenceItem.project_id == project_id)
        .order_by(ReferenceItem.sort_order)
    )
    return [ReferenceItemResponse.model_validate(r) for r in result.scalars().all()]


async def create_reference(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: ReferenceItemCreate,
) -> ReferenceItemResponse:
    await load_project(db, project_id, user_id)
    ref = ReferenceItem(
        project_id=project_id,
        type=req.type,
        name=req.name,
        content=req.content,
        metadata_json=req.metadata_json,
    )
    db.add(ref)
    await db.commit()
    await db.refresh(ref)
    return ReferenceItemResponse.model_validate(ref)


async def update_reference(
    db: AsyncSession,
    project_id: str,
    ref_id: str,
    user_id: str,
    req: ReferenceItemUpdate,
) -> ReferenceItemResponse:
    await load_project(db, project_id, user_id)
    ref = await get_reference(db, project_id, ref_id)
    for field in ["name", "content", "metadata_json"]:
        val = getattr(req, field, None)
        if val is not None:
            setattr(ref, field, val)
    await db.commit()
    await db.refresh(ref)
    return ReferenceItemResponse.model_validate(ref)


async def delete_reference(db: AsyncSession, project_id: str, ref_id: str, user_id: str) -> None:
    await load_project(db, project_id, user_id)
    ref = await get_reference(db, project_id, ref_id)
    await db.delete(ref)
    await db.commit()


async def get_document_references(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
) -> list[ReferenceItemResponse]:
    await load_project(db, project_id, user_id)
    doc = await get_document(db, project_id, document_id, with_references=True)
    return [ReferenceItemResponse.model_validate(link.reference) for link in doc.reference_links]


async def set_document_references(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    req: DocumentReferencesUpdate,
) -> dict[str, bool]:
    await load_project(db, project_id, user_id)
    doc = await get_document(db, project_id, document_id, with_references=True)

    for link in list(doc.reference_links):
        await db.delete(link)
    await db.flush()

    if req.reference_ids:
        ref_result = await db.execute(
            select(ReferenceItem.id).where(
                ReferenceItem.project_id == project_id,
                ReferenceItem.id.in_(req.reference_ids),
            )
        )
        valid_ids = {row[0] for row in ref_result.fetchall()}
        invalid_ids = set(req.reference_ids) - valid_ids
        if invalid_ids:
            raise HTTPException(status_code=404, detail="部分参考资料不存在")
    for rid in req.reference_ids:
        db.add(DocumentReference(document_id=document_id, reference_id=rid))
    await db.commit()
    return {"ok": True}
