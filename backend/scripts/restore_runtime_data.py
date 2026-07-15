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


def _snapshot_path(target: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return target.with_name(f"{target.name}.pre-restore-{timestamp}")


def _manifest_path(manifest: dict[str, object], key: str) -> Path:
    value = str(manifest.get(key) or "").strip()
    if not value:
        raise RuntimeError(f"backup manifest does not contain {key}")
    return Path(value).expanduser().resolve()


def _stage_tree(source: Path, target: Path) -> Path:
    if not source.is_dir():
        raise RuntimeError(f"backup data tree is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    candidate = target.with_name(f".{target.name}.restore-{uuid4().hex}.tmp")
    shutil.copytree(source, candidate)
    return candidate


def _restore_tree(
    *,
    target: Path,
    candidate: Path | None,
) -> Path | None:
    snapshot = None
    if target.exists():
        snapshot = _snapshot_path(target)
        os.replace(target, snapshot)
    if candidate is not None:
        try:
            os.replace(candidate, target)
        except Exception:
            if snapshot is not None and snapshot.exists():
                os.replace(snapshot, target)
            raise
    return snapshot


def _rollback_tree(*, target: Path, snapshot: Path | None) -> None:
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    if snapshot is not None and snapshot.exists():
        os.replace(snapshot, target)


def _rollback_database(*, target: Path, snapshot: Path | None) -> None:
    for path in _sidecar_paths(target):
        path.unlink(missing_ok=True)
    if snapshot is None:
        target.unlink(missing_ok=True)
        return
    candidate = target.with_name(f".{target.name}.rollback-{uuid4().hex}.tmp")
    try:
        shutil.copy2(snapshot, candidate)
        os.replace(candidate, target)
    finally:
        candidate.unlink(missing_ok=True)


def restore_database(
    manifest_path: Path,
    *,
    target_database: Path | None = None,
    target_projects_dir: Path | None = None,
    target_memoir_dir: Path | None = None,
    target_backend_data_dir: Path | None = None,
    restore_data_trees: bool = True,
    execute: bool = False,
    writers_stopped: bool = False,
) -> dict[str, object]:
    manifest = validate_backup_manifest(manifest_path)
    source_database = Path(str(manifest["source_database"])).expanduser().resolve()
    backup_database = Path(str(manifest["backup_database"])).expanduser().resolve()
    target = (target_database or source_database).expanduser().resolve()
    tree_specs: list[dict[str, object]] = []
    if restore_data_trees:
        for label, source_key, backup_key, copied_key, override in (
            (
                "projects",
                "source_projects_dir",
                "backup_projects_dir",
                "projects_dir_copied",
                target_projects_dir,
            ),
            (
                "memoir",
                "source_memoir_dir",
                "backup_memoir_dir",
                "memoir_dir_copied",
                target_memoir_dir,
            ),
            (
                "backend_data",
                "source_backend_data_dir",
                "backup_backend_data_dir",
                "backend_data_dir_copied",
                target_backend_data_dir,
            ),
        ):
            tree_specs.append(
                {
                    "label": label,
                    "source": _manifest_path(manifest, backup_key),
                    "target": (
                        override.expanduser().resolve()
                        if override is not None
                        else _manifest_path(manifest, source_key)
                    ),
                    "copied": bool(manifest.get(copied_key)),
                }
            )
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
        "restore_data_trees": restore_data_trees,
        "data_trees": {
            str(spec["label"]): {
                "backup": str(spec["source"]),
                "target": str(spec["target"]),
                "copied": bool(spec["copied"]),
                "current_snapshot": None,
            }
            for spec in tree_specs
        },
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
    staged_trees: dict[str, Path | None] = {}
    applied_trees: list[tuple[Path, Path | None]] = []
    current_database_snapshot: Path | None = None
    database_replaced = False
    try:
        shutil.copy2(backup_database, restore_candidate)
        restored_integrity = integrity_check(restore_candidate)
        if restored_integrity != "ok":
            raise RuntimeError("restored database failed SQLite integrity_check")
        restored_sha256 = sha256_file(restore_candidate)
        if restored_sha256 != manifest["backup_database_sha256"]:
            raise RuntimeError("restored database SHA-256 does not match the backup manifest")

        for spec in tree_specs:
            label = str(spec["label"])
            staged_trees[label] = (
                _stage_tree(Path(spec["source"]), Path(spec["target"]))
                if spec["copied"]
                else None
            )

        if target.exists():
            current_database_snapshot = _snapshot_database(target)
            result["current_database_snapshot"] = str(current_database_snapshot)
        for spec in tree_specs:
            label = str(spec["label"])
            tree_target = Path(spec["target"])
            snapshot = _restore_tree(
                target=tree_target,
                candidate=staged_trees[label],
            )
            staged_trees[label] = None
            applied_trees.append((tree_target, snapshot))
            result["data_trees"][label]["current_snapshot"] = (
                str(snapshot) if snapshot else None
            )
        os.replace(restore_candidate, target)
        database_replaced = True
        for path in _sidecar_paths(target):
            path.unlink(missing_ok=True)
    except Exception:
        if database_replaced:
            _rollback_database(target=target, snapshot=current_database_snapshot)
        for tree_target, snapshot in reversed(applied_trees):
            _rollback_tree(target=tree_target, snapshot=snapshot)
        raise
    finally:
        restore_candidate.unlink(missing_ok=True)
        for candidate in staged_trees.values():
            if candidate is not None and candidate.exists():
                shutil.rmtree(candidate)

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
    parser.add_argument("--target-projects-dir", type=Path)
    parser.add_argument("--target-memoir-dir", type=Path)
    parser.add_argument("--target-backend-data-dir", type=Path)
    parser.add_argument(
        "--database-only",
        action="store_true",
        help="Restore only SQLite and leave projects and memoir media untouched.",
    )
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
        target_projects_dir=args.target_projects_dir,
        target_memoir_dir=args.target_memoir_dir,
        target_backend_data_dir=args.target_backend_data_dir,
        restore_data_trees=not args.database_only,
        execute=args.execute,
        writers_stopped=args.database_writers_stopped,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
