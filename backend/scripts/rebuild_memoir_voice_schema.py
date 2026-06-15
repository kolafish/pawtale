"""Rebuild the realtime memoir voice tables in the configured SQLite database.

This is a development/reset tool. It deletes realtime voice rows and audio file
metadata in the configured backend database, then recreates the schema from the
SQLAlchemy models and runs the strict schema assertion.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.migrations.memoir_voice import (
    REALTIME_VOICE_TABLES,
    assert_memoir_voice_schema,
    describe_memoir_voice_schema,
    expected_memoir_voice_schema,
    rebuild_memoir_voice_schema,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deleting and recreating realtime memoir voice tables.",
    )
    parser.add_argument(
        "--describe-only",
        action="store_true",
        help="Print expected and actual table columns without changing the database.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    script_engine = create_async_engine(settings.database_url, echo=False)
    try:
        actual_before = await describe_memoir_voice_schema(script_engine)
        print(
            json.dumps(
                {
                    "database_url": settings.database_url,
                    "tables": list(REALTIME_VOICE_TABLES),
                    "expected": expected_memoir_voice_schema(),
                    "actual": actual_before,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        if args.describe_only:
            return
        if not args.yes:
            raise SystemExit("Refusing to rebuild without --yes")

        await rebuild_memoir_voice_schema(script_engine)
        await assert_memoir_voice_schema(script_engine)
        print("memoir realtime voice schema rebuilt")
    finally:
        await script_engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
