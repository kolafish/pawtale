"""Create review-only contract and node suggestions for legacy memoir chapters.

Default mode is dry-run and performs no writes or provider calls. Execute mode
creates draft suggestions only; users must confirm them through the API.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.database import async_session_maker, engine
from app.migrations.runtime import assert_runtime_schema
from app.services.memoir.narrative.chapter_structure_backfill import (
    run_chapter_structure_backfill,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    try:
        await assert_runtime_schema(engine)
        async with async_session_maker() as db:
            result = await run_chapter_structure_backfill(
                db,
                project_id=args.project_id,
                dry_run=not args.execute,
                limit=args.limit,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
