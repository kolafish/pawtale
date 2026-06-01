"""
Migrate local Pawtale data to the user-owned auth schema.

By default this creates a full backup before changing the database.

Usage:
  PYTHONPATH=backend python backend/scripts/migrate_auth.py
  PYTHONPATH=backend python backend/scripts/migrate_auth.py --skip-backup
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import Base, engine
from app.migrations.auth import migrate_auth_schema
from backup_before_auth import create_backup


async def run(skip_backup: bool) -> None:
    backup_dir = None
    if not skip_backup:
        backup_dir = create_backup("before_auth_migration")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    result = await migrate_auth_schema(engine)
    await engine.dispose()

    print("Auth migration complete.")
    if backup_dir:
        print(f"Backup: {backup_dir}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-backup", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.skip_backup))


if __name__ == "__main__":
    main()
