from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import MemoirProjectRevisionEvent, Project


async def project_data_revision(db: AsyncSession, project: Project) -> int:
    """Return the newest append-only revision, with the legacy counter as its floor."""

    latest = await db.scalar(
        select(MemoirProjectRevisionEvent.revision)
        .where(MemoirProjectRevisionEvent.project_id == project.id)
        .order_by(MemoirProjectRevisionEvent.revision.desc())
        .limit(1)
    )
    return max(int(project.memoir_data_revision or 0), int(latest or 0))
