from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import Document, DocumentGroup, Project
import app.services.project_documents as project_docs


async def initialize_memoir_project(db: AsyncSession, project: Project) -> None:
    group = DocumentGroup(project_id=project.id, name="回忆片段", sort_order=0)
    db.add(group)
    await db.flush()

    doc = Document(group_id=group.id, name="第一次记录", sort_order=0, word_count=0)
    db.add(doc)
    await db.flush()

    await project_docs.commit_created_document(db, project.id, doc)
