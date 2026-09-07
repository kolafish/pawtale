from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.projects.types import ProjectType
from app.models.projects import Document, DocumentGroup
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
import app.services.project_documents as project_docs
from app.services.content_storage import read_content
from app.services.projects.access import get_document, get_group, load_project


async def _load_document_project(
    db: AsyncSession,
    project_id: str,
    user_id: str,
):
    project = await load_project(db, project_id, user_id)
    if project.type == ProjectType.MEMOIR.value:
        raise HTTPException(status_code=400, detail="回忆录不使用通用文档结构")
    return project


async def create_group(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: GroupCreate,
) -> GroupResponse:
    project = await _load_document_project(db, project_id, user_id)
    max_order = max((g.sort_order for g in project.groups), default=-1)
    group = DocumentGroup(project_id=project_id, name=req.name, sort_order=max_order + 1)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return GroupResponse(id=group.id, name=group.name, sort_order=group.sort_order, documents=[])


async def reorder_groups(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    items: list[GroupReorderItem],
) -> dict[str, bool]:
    await _load_document_project(db, project_id, user_id)
    for item in items:
        result = await db.execute(
            select(DocumentGroup).where(
                DocumentGroup.id == item.id,
                DocumentGroup.project_id == project_id,
            )
        )
        group = result.scalar_one_or_none()
        if group:
            group.sort_order = item.sort_order
    await db.commit()
    return {"ok": True}


async def update_group(
    db: AsyncSession,
    project_id: str,
    group_id: str,
    user_id: str,
    req: GroupUpdate,
) -> GroupResponse:
    await _load_document_project(db, project_id, user_id)
    group = await get_group(db, project_id, group_id)
    if req.name is not None:
        group.name = req.name
    await db.commit()
    await db.refresh(group)
    return project_docs.group_response(group)


async def delete_group(db: AsyncSession, project_id: str, group_id: str, user_id: str) -> None:
    await _load_document_project(db, project_id, user_id)
    group = await get_group(db, project_id, group_id)
    await project_docs.delete_group(db, project_id, group)


async def create_document(
    db: AsyncSession,
    project_id: str,
    group_id: str,
    user_id: str,
    req: DocumentCreate,
) -> DocumentResponse:
    await _load_document_project(db, project_id, user_id)
    group = await get_group(db, project_id, group_id)
    max_order = max((d.sort_order for d in group.documents), default=-1)
    doc = Document(group_id=group_id, name=req.name, sort_order=max_order + 1, word_count=0)
    db.add(doc)
    await db.flush()
    await project_docs.commit_created_document(db, project_id, doc)
    await db.refresh(doc)
    return project_docs.document_response(doc, "")


async def reorder_documents(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    items: list[DocumentReorderItem],
) -> dict[str, bool]:
    await _load_document_project(db, project_id, user_id)
    documents = {
        doc.id: doc
        for doc in await db.scalars(
            select(Document).join(DocumentGroup).where(
                Document.id.in_({item.id for item in items}),
                DocumentGroup.project_id == project_id,
            )
        )
    }
    target_group_ids = set(await db.scalars(
        select(DocumentGroup.id).where(
            DocumentGroup.id.in_({item.group_id for item in items}),
            DocumentGroup.project_id == project_id,
        )
    ))
    if any(item.id not in documents or item.group_id not in target_group_ids for item in items):
        raise HTTPException(status_code=404, detail="文档或目标分组不属于当前项目")
    for item in items:
        documents[item.id].group_id = item.group_id
        documents[item.id].sort_order = item.sort_order
    await db.commit()
    return {"ok": True}


async def get_document_response(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
) -> DocumentResponse:
    await _load_document_project(db, project_id, user_id)
    doc = await get_document(db, project_id, document_id)
    content = await read_content(project_id, doc.id)
    return project_docs.document_response(doc, content)


async def update_document(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
    req: DocumentUpdate,
) -> DocumentResponse:
    await _load_document_project(db, project_id, user_id)
    doc = await get_document(db, project_id, document_id)
    for field in ["name", "synopsis", "status", "label", "word_count_target"]:
        val = getattr(req, field, None)
        if val is not None:
            setattr(doc, field, val)
    try:
        await project_docs.commit_document_update(
            db,
            project_id,
            doc,
            req.content,
            expected_content_revision=req.expected_content_revision,
        )
    except project_docs.ContentRevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="文档内容已在其他位置更新，请刷新后再保存",
        ) from exc
    await db.refresh(doc)
    content = await read_content(project_id, doc.id)
    return project_docs.document_response(doc, content)


async def delete_document(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    user_id: str,
) -> None:
    await _load_document_project(db, project_id, user_id)
    doc = await get_document(db, project_id, document_id)
    await project_docs.delete_document(db, project_id, doc)
