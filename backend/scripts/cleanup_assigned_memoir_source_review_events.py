"""Hide pending source review events whose source is already assigned to a chapter.

Usage:
  PYTHONPATH=backend python backend/scripts/cleanup_assigned_memoir_source_review_events.py
  PYTHONPATH=backend python backend/scripts/cleanup_assigned_memoir_source_review_events.py --dry-run
  PYTHONPATH=backend python backend/scripts/cleanup_assigned_memoir_source_review_events.py --project-id <project_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from app.database import async_session_maker, engine, init_db
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.models.projects import Project
from app.services.memoir import automation_events


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", help="Only clean events for one project.")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without committing.")
    return parser.parse_args()


async def _project_ids(project_id: str | None) -> list[str]:
    if project_id:
        return [project_id]
    async with async_session_maker() as db:
        result = await db.execute(select(Project.id))
        return [item for item in result.scalars().all()]


async def _main() -> None:
    args = _parse_args()
    try:
        await init_db()
        await migrate_memoir_voice_schema(engine)
        project_ids = await _project_ids(args.project_id)
        total_updated = 0
        resolved_utterance_ids: list[str] = []
        async with async_session_maker() as db:
            for project_id in project_ids:
                result = await automation_events.resolve_assigned_source_review_events(
                    db,
                    project_id=project_id,
                    confirmed_plan_only=True,
                )
                total_updated += result.updated_event_count
                resolved_utterance_ids.extend(result.resolved_utterance_ids)
            if args.dry_run:
                await db.rollback()
            else:
                await db.commit()
            print(
                json.dumps(
                    {
                        "dry_run": args.dry_run,
                        "project_id": args.project_id,
                        "updated_event_count": total_updated,
                        "resolved_utterance_ids": sorted(set(resolved_utterance_ids)),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
