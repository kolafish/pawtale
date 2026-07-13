#!/usr/bin/env python3
"""Validate or restore a runtime SQLite backup after all database writers stop."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from scripts.backup_runtime_data import (
    integrity_check,
    sha256_file,
    table_counts,
    validate_backup_manifest,
)


def _sidecar_paths(database: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{database}{suffix}") for suffix in ("-wal", "-shm", "-journal"))


def _snapshot_database(source: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    snapshot = source.with_name(f"{source.stem}.pre-restore-{timestamp}{source.suffix}")
    with sqlite3.connect(source) as source_db, sqlite3.connect(snapshot) as snapshot_db:
        source_db.backup(snapshot_db)
    if integrity_check(snapshot) != "ok":
        snapshot.unlink(missing_ok=True)
        raise RuntimeError("current database snapshot failed SQLite integrity_check")
    return snapshot


def restore_database(
    manifest_path: Path,
    *,
    target_database: Path | None = None,
    execute: bool = False,
    writers_stopped: bool = False,
) -> dict[str, object]:
    manifest = validate_backup_manifest(manifest_path)
    source_database = Path(str(manifest["source_database"])).expanduser().resolve()
    backup_database = Path(str(manifest["backup_database"])).expanduser().resolve()
    target = (target_database or source_database).expanduser().resolve()
    result: dict[str, object] = {
        "executed": False,
        "manifest": str(manifest_path.resolve()),
        "backup_database": str(backup_database),
        "backup_database_sha256": manifest["backup_database_sha256"],
        "target_database": str(target),
        "current_database_snapshot": None,
        "sidecars_present": [str(path) for path in _sidecar_paths(target) if path.exists()],
        "restored_integrity_check": None,
        "restored_table_counts": None,
    }
    if not execute:
        return result
    if not writers_stopped:
        raise RuntimeError(
            "restore requires explicit confirmation that all database writers are stopped"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    restore_candidate = target.with_name(
        f".{target.name}.restore-{uuid4().hex}.tmp"
    )
    try:
        shutil.copy2(backup_database, restore_candidate)
        restored_integrity = integrity_check(restore_candidate)
        if restored_integrity != "ok":
            raise RuntimeError("restored database failed SQLite integrity_check")
        restored_sha256 = sha256_file(restore_candidate)
        if restored_sha256 != manifest["backup_database_sha256"]:
            raise RuntimeError("restored database SHA-256 does not match the backup manifest")

        if target.exists():
            result["current_database_snapshot"] = str(_snapshot_database(target))
        for path in _sidecar_paths(target):
            path.unlink(missing_ok=True)
        os.replace(restore_candidate, target)
    finally:
        restore_candidate.unlink(missing_ok=True)

    result.update(
        {
            "executed": True,
            "sidecars_present": [],
            "restored_integrity_check": restored_integrity,
            "restored_table_counts": table_counts(target),
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--target-database", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--database-writers-stopped",
        action="store_true",
        help="Required with --execute after the API and workers have been stopped.",
    )
    args = parser.parse_args()
    result = restore_database(
        args.backup_manifest,
        target_database=args.target_database,
        execute=args.execute,
        writers_stopped=args.database_writers_stopped,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
