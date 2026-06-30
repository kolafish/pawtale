"""Backfill memoir chapter source links from legacy chapter source refs.

Usage:
  PYTHONPATH=backend python backend/scripts/backfill_memoir_chapter_source_links.py
  PYTHONPATH=backend python backend/scripts/backfill_memoir_chapter_source_links.py --execute
  PYTHONPATH=backend python backend/scripts/backfill_memoir_chapter_source_links.py --project-id <project_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, engine, init_db
from app.domain.projects.types import ProjectType
from app.migrations.memoir_semantic import migrate_memoir_semantic_schema
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.models.projects import Project
from app.services.memoir.chapter_source_links import backfill_project_chapter_source_links


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", help="Only backfill one memoir project.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Apply changes. Default is dry-run.")
    mode.add_argument("--dry-run", action="store_true", help="Report changes without committing. This is the default.")
    return parser.parse_args()


async def memoir_project_ids(db: AsyncSession, project_id: str | None = None) -> list[str]:
    if project_id:
        return [project_id]
    rows = await db.execute(
        select(Project.id)
        .where(Project.type == ProjectType.MEMOIR.value)
        .order_by(Project.created_at, Project.id)
    )
    return list(rows.scalars().all())


async def run_chapter_source_link_backfill(
    db: AsyncSession,
    *,
    project_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, object]:
    project_ids = await memoir_project_ids(db, project_id)
    failures: list[dict[str, str]] = []
    totals = {
        "created_count": 0,
        "activated_count": 0,
        "updated_count": 0,
        "removed_count": 0,
    }
    for current_project_id in project_ids:
        try:
            result = await backfill_project_chapter_source_links(db, project_id=current_project_id)
            totals["created_count"] += result.created_count
            totals["activated_count"] += result.activated_count
            totals["updated_count"] += result.updated_count
            totals["removed_count"] += result.removed_count
        except Exception as exc:
            failures.append({"project_id": current_project_id, "error": str(exc)})

    if dry_run:
        await db.rollback()
    else:
        await db.commit()
    return {
        "dry_run": dry_run,
        "project_id": project_id,
        "scanned_project_count": len(project_ids),
        **totals,
        "failure_count": len(failures),
        "failures": failures,
    }


async def _main() -> None:
    args = _parse_args()
    try:
        await init_db()
        await migrate_memoir_voice_schema(engine)
        await migrate_memoir_semantic_schema(engine)
        async with async_session_maker() as db:
            result = await run_chapter_source_link_backfill(
                db,
                project_id=args.project_id,
                dry_run=not args.execute,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
