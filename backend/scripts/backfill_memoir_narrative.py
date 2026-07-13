"""Queue narrative-state ingestion for existing memoir transcripts.

Default mode is dry-run and performs no writes or provider calls.

Usage:
  PYTHONPATH=backend python backend/scripts/backfill_memoir_narrative.py
  PYTHONPATH=backend python backend/scripts/backfill_memoir_narrative.py --execute --limit 20
  PYTHONPATH=backend python backend/scripts/backfill_memoir_narrative.py --project-id <project_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.database import async_session_maker, engine
from app.migrations.runtime import assert_runtime_schema
from app.services.memoir.narrative.backfill import run_narrative_ingestion_backfill


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", help="Only inspect one memoir project.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Enqueue ingestion jobs.")
    mode.add_argument("--dry-run", action="store_true", help="Report candidates only. This is the default.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of candidates.")
    parser.add_argument("--ai-model", help="Optional AI model override stored on queued jobs.")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    try:
        await assert_runtime_schema(engine)
        async with async_session_maker() as db:
            result = await run_narrative_ingestion_backfill(
                db,
                project_id=args.project_id,
                dry_run=not args.execute,
                limit=args.limit,
                ai_model=args.ai_model,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
