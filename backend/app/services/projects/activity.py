from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import WritingSession
from app.schemas.projects import (
    ProjectExportResponse,
    WritingSessionCreate,
    WritingSessionResponse,
)
from app.services.projects.access import load_project
from app.services.projects.export import export_project_data


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
    return await export_project_data(db, project_id, user_id)
