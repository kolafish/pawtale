from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import WritingSession
from app.schemas.projects import (
    ProjectExportResponse,
    WritingSessionCreate,
    WritingSessionResponse,
)
from app.services import project_documents as project_docs
from app.services.content_storage import read_content
from app.services.projects.access import load_project


async def list_sessions(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> list[WritingSessionResponse]:
    await load_project(db, project_id, user_id)
    result = await db.execute(
        select(WritingSession)
        .where(WritingSession.project_id == project_id)
        .order_by(WritingSession.date.desc())
        .limit(90)
    )
    return [WritingSessionResponse.model_validate(s) for s in result.scalars().all()]


async def create_session(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    req: WritingSessionCreate,
) -> WritingSessionResponse:
    await load_project(db, project_id, user_id)
    session = WritingSession(
        project_id=project_id,
        date=req.date,
        words_written=req.words_written,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return WritingSessionResponse.model_validate(session)


async def export_project(db: AsyncSession, project_id: str, user_id: str) -> ProjectExportResponse:
    project = await load_project(db, project_id, user_id)
    sections = []
    total = 0
    for group in project.groups:
        docs = []
        for doc in group.documents:
            content = await read_content(project_id, doc.id)
            word_count = project_docs.count_words(content)
            total += word_count
            docs.append({"name": doc.name, "content": content, "word_count": word_count})
        sections.append({"group": group.name, "documents": docs})
    return ProjectExportResponse(title=project.title, sections=sections, total_words=total)
