"""
Create a full local backup before auth/user migration.

The project stores structured data in data/pawtale.db and document bodies in
data/projects/{project_id}/{document_id}.md, so both must be backed up.

Usage:
  PYTHONPATH=backend python backend/scripts/backup_before_auth.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
BACKEND_DATA_DIR = REPO_ROOT / "backend" / "data"
DB_PATH = DATA_DIR / "pawtale.db"
PROJECTS_DIR = DATA_DIR / "projects"
BACKUPS_DIR = DATA_DIR / "backups"


TABLES_TO_COUNT = [
    "projects",
    "document_groups",
    "documents",
    "reference_items",
    "document_references",
    "writing_sessions",
    "characters",
    "scenes",
    "stories",
    "story_workspaces",
    "story_outlines",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_optional_tree(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    shutil.copytree(src, dst)
    return True


def dump_database(db_path: Path, dump_path: Path) -> None:
    with sqlite3.connect(db_path) as conn, dump_path.open("w", encoding="utf-8") as f:
        for line in conn.iterdump():
            f.write(f"{line}\n")


def write_schema(db_path: Path, schema_path: Path) -> None:
    query = """
        SELECT sql
        FROM sqlite_master
        WHERE sql IS NOT NULL
          AND type IN ('table', 'index', 'trigger', 'view')
        ORDER BY type, name
    """
    with sqlite3.connect(db_path) as conn, schema_path.open("w", encoding="utf-8") as f:
        for (sql,) in conn.execute(query):
            f.write(f"{sql};\n\n")


def table_counts(db_path: Path) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    with sqlite3.connect(db_path) as conn:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table in TABLES_TO_COUNT:
            if table not in existing:
                counts[table] = None
                continue
            (count,) = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            counts[table] = int(count)
    return counts


def integrity_check(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        (result,) = conn.execute("PRAGMA integrity_check").fetchone()
    return str(result)


def write_checksums(backup_dir: Path) -> None:
    checksum_path = backup_dir / "checksums.sha256"
    rows = []
    for path in sorted(p for p in backup_dir.rglob("*") if p.is_file()):
        if path.name == checksum_path.name:
            continue
        rel = path.relative_to(backup_dir)
        rows.append(f"{sha256_file(path)}  {rel.as_posix()}")
    checksum_path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def create_backup(label: str | None = None) -> Path:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database does not exist: {DB_PATH}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    backup_dir = BACKUPS_DIR / f"auth_migration_{timestamp}{suffix}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    db_backup = backup_dir / "pawtale.db"
    shutil.copy2(DB_PATH, db_backup)
    dump_database(DB_PATH, backup_dir / "pawtale.dump.sql")
    write_schema(DB_PATH, backup_dir / "schema.sql")

    projects_copied = copy_optional_tree(PROJECTS_DIR, backup_dir / "projects")
    backend_data_copied = copy_optional_tree(BACKEND_DATA_DIR, backup_dir / "backend_data")

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "repo_root": str(REPO_ROOT),
        "source_database": str(DB_PATH),
        "backup_database": str(db_backup),
        "sqlite_integrity_check": integrity_check(db_backup),
        "table_counts": table_counts(db_backup),
        "projects_dir_copied": projects_copied,
        "backend_data_dir_copied": backend_data_copied,
        "project_markdown_file_count": (
            len(list((backup_dir / "projects").rglob("*.md"))) if projects_copied else 0
        ),
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    write_checksums(backup_dir)
    return backup_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", help="Optional suffix for the backup directory.")
    args = parser.parse_args()

    backup_dir = create_backup(args.label)
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    print(f"Backup created: {backup_dir}")
    print(f"SQLite integrity_check: {manifest['sqlite_integrity_check']}")
    print(f"Project markdown files: {manifest['project_markdown_file_count']}")
    print("Table counts:")
    for table, count in manifest["table_counts"].items():
        if count is not None:
            print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
