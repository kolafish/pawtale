import re
import shutil
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.projects.types import ProjectType
from app.models.projects import Document, DocumentGroup, Project
from app.models.users import User
from app.schemas.projects import (
    DocumentMeta,
    DocumentResponse,
    GroupResponse,
    ProjectResponse,
    ReferenceItemBrief,
)
import app.services.content_storage as storage


class ContentRevisionConflict(Exception):
    pass


def count_words(text: str) -> int:
    if not text:
        return 0
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    english = len(re.findall(r"[a-zA-Z]+", text))
    return chinese + english


def project_total_words(project: Project) -> int:
    return sum(
        doc.word_count
        for group in (project.groups or [])
        for doc in (group.documents or [])
    )


def document_meta(doc: Document) -> DocumentMeta:
    return DocumentMeta(
        id=doc.id,
        group_id=doc.group_id,
        name=doc.name,
        status=doc.status or "draft",
        label=doc.label,
        word_count=doc.word_count,
        content_revision=doc.content_revision,
        sort_order=doc.sort_order,
        updated_at=doc.updated_at,
    )


def group_response(group: DocumentGroup) -> GroupResponse:
    return GroupResponse(
        id=group.id,
        name=group.name,
        sort_order=group.sort_order,
        documents=[document_meta(doc) for doc in (group.documents or [])],
    )


def project_response(
    project: Project,
    *,
    owner: User | None = None,
    viewer_user_id: str | None = None,
    viewer_is_admin: bool = False,
) -> ProjectResponse:
    can_edit = True if viewer_user_id is None else project.user_id == viewer_user_id
    can_edit_memoir_materials = True if viewer_user_id is None else (
        can_edit or (viewer_is_admin and project.type == ProjectType.MEMOIR.value)
    )
    return ProjectResponse(
        id=project.id,
        type=project.type,
        title=project.title,
        description=project.description,
        word_count_target=project.word_count_target,
        daily_word_goal=project.daily_word_goal,
        owner_user_id=owner.id if owner else project.user_id,
        owner_email=owner.email if owner else None,
        owner_display_name=owner.display_name if owner else None,
        can_edit=can_edit,
        can_delete=can_edit or viewer_is_admin,
        can_edit_memoir_materials=can_edit_memoir_materials,
        metadata_json=project.metadata_json or {},
        total_word_count=project_total_words(project),
        groups=[group_response(group) for group in (project.groups or [])],
        references=[
            ReferenceItemBrief(
                id=ref.id,
                type=ref.type,
                name=ref.name,
                sort_order=ref.sort_order,
            )
            for ref in (project.references or [])
        ],
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def document_response(doc: Document, content: str) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        group_id=doc.group_id,
        name=doc.name,
        content=content,
        synopsis=doc.synopsis,
        status=doc.status,
        label=doc.label,
        word_count_target=doc.word_count_target,
        word_count=doc.word_count,
        content_revision=doc.content_revision,
        sort_order=doc.sort_order,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@dataclass
class ContentSnapshot:
    exists: bool
    content: str


async def _snapshot_content(project_id: str, document_id: str) -> ContentSnapshot:
    exists = storage.content_exists(project_id, document_id)
    content = await storage.read_content(project_id, document_id) if exists else ""
    return ContentSnapshot(exists=exists, content=content)


async def _restore_content(project_id: str, document_id: str, snapshot: ContentSnapshot) -> None:
    if snapshot.exists:
        await storage.write_content(project_id, document_id, snapshot.content)
    else:
        await storage.delete_content(project_id, document_id)


async def commit_created_document(db: AsyncSession, project_id: str, doc: Document, content: str = "") -> None:
    file_written = False
    try:
        await storage.write_content(project_id, doc.id, content)
        file_written = True
        await db.commit()
    except Exception:
        await db.rollback()
        if file_written:
            await storage.delete_content(project_id, doc.id)
        raise


async def commit_document_update(
    db: AsyncSession,
    project_id: str,
    doc: Document,
    content: str | None,
    *,
    expected_content_revision: int | None = None,
) -> None:
    async with storage.project_content_lock(project_id):
        document_id = doc.id
        snapshot: ContentSnapshot | None = None
        try:
            if content is not None:
                statement = update(Document).where(Document.id == document_id)
                if expected_content_revision is not None:
                    statement = statement.where(Document.content_revision == expected_content_revision)
                result = await db.execute(
                    statement.values(content_revision=Document.content_revision + 1)
                    .returning(Document.content_revision)
                    .execution_options(synchronize_session=False)
                )
                revision = result.scalar_one_or_none()
                if revision is None:
                    raise ContentRevisionConflict
                doc.content_revision = revision
                snapshot = await _snapshot_content(project_id, document_id)
                await storage.write_content(project_id, document_id, content)
                doc.word_count = count_words(content)
            await db.commit()
        except Exception:
            await db.rollback()
            if snapshot is not None:
                await _restore_content(project_id, document_id, snapshot)
            raise


async def delete_document(db: AsyncSession, project_id: str, doc: Document) -> None:
    async with storage.project_content_lock(project_id):
        snapshot = await _snapshot_content(project_id, doc.id)
        try:
            await storage.delete_content(project_id, doc.id)
            await db.delete(doc)
            await db.commit()
        except Exception:
            await db.rollback()
            await _restore_content(project_id, doc.id, snapshot)
            raise


async def delete_group(db: AsyncSession, project_id: str, group: DocumentGroup) -> None:
    async with storage.project_content_lock(project_id):
        snapshots = {
            doc.id: await _snapshot_content(project_id, doc.id)
            for doc in (group.documents or [])
        }
        try:
            for doc_id in snapshots:
                await storage.delete_content(project_id, doc_id)
            await db.delete(group)
            await db.commit()
        except Exception:
            await db.rollback()
            for doc_id, snapshot in snapshots.items():
                await _restore_content(project_id, doc_id, snapshot)
            raise


async def delete_project(db: AsyncSession, project: Project) -> None:
    async with storage.project_content_lock(project.id):
        source = storage.PROJECTS_DIR / project.id
        backup = source.parent / f".{project.id}.{uuid4().hex}.delete-backup"

        try:
            if source.exists():
                shutil.copytree(source, backup)
                await storage.delete_project_dir(project.id)
            await db.delete(project)
            await db.commit()
        except Exception:
            await db.rollback()
            if backup.exists() and not source.exists():
                shutil.copytree(backup, source)
            raise
        finally:
            if backup.exists():
                shutil.rmtree(backup)
