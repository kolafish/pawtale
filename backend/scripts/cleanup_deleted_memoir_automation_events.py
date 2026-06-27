"""Hide memoir automation events that reference deleted voice utterances.

Usage:
  PYTHONPATH=backend python backend/scripts/cleanup_deleted_memoir_automation_events.py
  PYTHONPATH=backend python backend/scripts/cleanup_deleted_memoir_automation_events.py --dry-run
  PYTHONPATH=backend python backend/scripts/cleanup_deleted_memoir_automation_events.py --project-id <project_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.database import async_session_maker, engine, init_db
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.services.memoir import automation_events


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", help="Only clean events for one project.")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without committing.")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    try:
        await init_db()
        await migrate_memoir_voice_schema(engine)
        async with async_session_maker() as db:
            result = await automation_events.cleanup_dangling_automation_event_utterances(
                db,
                project_id=args.project_id,
            )
            if args.dry_run:
                await db.rollback()
            else:
                await db.commit()
            print(
                json.dumps(
                    {
                        "dry_run": args.dry_run,
                        "project_id": args.project_id,
                        "updated_event_count": result.updated_event_count,
                        "deleted_utterance_ids": result.deleted_utterance_ids,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
