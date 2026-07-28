from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application_errors import ResourceNotFoundError
from app.models.projects import Document, DocumentGroup, DocumentReference, Project, ReferenceItem


async def load_project(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    *,
    allow_admin: bool = False,
) -> Project:
    filters = [Project.id == project_id]
    if not allow_admin:
        filters.append(Project.user_id == user_id)
    result = await db.execute(
        select(Project)
        .where(*filters)
        .options(
            selectinload(Project.groups).selectinload(DocumentGroup.documents),
            selectinload(Project.references),
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise ResourceNotFoundError("项目不存在")
    return project


async def get_group(db: AsyncSession, project_id: str, group_id: str) -> DocumentGroup:
    result = await db.execute(
        select(DocumentGroup)
        .where(DocumentGroup.id == group_id, DocumentGroup.project_id == project_id)
        .options(selectinload(DocumentGroup.documents))
    )
    group = result.scalar_one_or_none()
    if not group:
        raise ResourceNotFoundError("分组不存在")
    return group


async def get_document(
    db: AsyncSession,
    project_id: str,
    document_id: str,
    *,
    with_references: bool = False,
) -> Document:
    query = (
        select(Document)
        .join(DocumentGroup)
        .where(Document.id == document_id, DocumentGroup.project_id == project_id)
    )
    if with_references:
        query = query.options(
            selectinload(Document.reference_links).selectinload(DocumentReference.reference)
        )
    result = await db.execute(query)
    doc = result.scalar_one_or_none()
    if not doc:
        raise ResourceNotFoundError("文档不存在")
    return doc


async def get_reference(db: AsyncSession, project_id: str, ref_id: str) -> ReferenceItem:
    result = await db.execute(
        select(ReferenceItem).where(
            ReferenceItem.id == ref_id,
            ReferenceItem.project_id == project_id,
        )
    )
    ref = result.scalar_one_or_none()
    if not ref:
        raise ResourceNotFoundError("参考资料不存在")
    return ref
