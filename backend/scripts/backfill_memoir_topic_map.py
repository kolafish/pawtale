"""Initialize memoir topic maps and enqueue Shadow matching for existing Claims."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import async_session_maker
from app.domain.projects.types import ProjectType
from app.models.projects import Project
from app.services.memoir.narrative.framework_matching import (
    schedule_project_framework_backfill,
)


async def main() -> None:
    async with async_session_maker() as db:
        project_ids = list(
            (
                await db.scalars(
                    select(Project.id)
                    .where(Project.type == ProjectType.MEMOIR.value)
                    .order_by(Project.created_at, Project.id)
                )
            ).all()
        )

    job_count = 0
    for project_id in project_ids:
        async with async_session_maker() as db:
            job_count += await schedule_project_framework_backfill(
                db,
                project_id=project_id,
                enabled=True,
            )
            await db.commit()
    print(
        f"memoir_topic_map_backfill projects={len(project_ids)} "
        f"jobs={job_count}"
    )


if __name__ == "__main__":
    asyncio.run(main())
