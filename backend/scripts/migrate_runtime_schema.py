"""
Run Pawtale runtime schema migrations explicitly.

By default this creates a full backup before changing an existing local database.

Usage:
  PYTHONPATH=backend python backend/scripts/migrate_runtime_schema.py
  PYTHONPATH=backend python backend/scripts/migrate_runtime_schema.py --skip-backup
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

from app.config import get_settings
from app.database import create_database_engine
from app.migrations.runtime import migrate_runtime_schema
from backup_runtime_data import create_backup, sqlite_database_path


async def run(skip_backup: bool) -> None:
    settings = get_settings()
    backup_dir = None
    if not skip_backup:
        database_path = sqlite_database_path(settings.database_url)
        if database_path and database_path.exists():
            backup_dir = create_backup(
                prefix="runtime_schema_migration",
                db_path=database_path,
            )

    migration_engine = create_database_engine(
        settings,
        enforce_sqlite_foreign_keys=False,
    )
    try:
        result = await migrate_runtime_schema(migration_engine)
    finally:
        await migration_engine.dispose()

    print("Runtime schema migration complete.")
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
