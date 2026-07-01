"""Inspect memoir semantic backfill state without modifying data.

Usage:
  PYTHONPATH=backend python backend/scripts/inspect_memoir_semantic_state.py
  PYTHONPATH=backend python backend/scripts/inspect_memoir_semantic_state.py --project-id <project_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.database import async_session_maker, engine, init_db
from app.migrations.memoir_semantic import migrate_memoir_semantic_schema
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.services.memoir.semantic_inspector import run_semantic_state_inspection


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", help="Only inspect one memoir project.")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    try:
        await init_db()
        await migrate_memoir_voice_schema(engine)
        await migrate_memoir_semantic_schema(engine)
        async with async_session_maker() as db:
            result = await run_semantic_state_inspection(db, project_id=args.project_id)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
