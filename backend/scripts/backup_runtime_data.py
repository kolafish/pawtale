"""
Create a full local Pawtale data backup before a runtime schema migration.

The project stores structured data in data/pawtale.db and document bodies in
data/projects/{project_id}/{document_id}.md, so both must be backed up.

Usage:
  PYTHONPATH=backend python backend/scripts/backup_runtime_data.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
BACKEND_DATA_DIR = REPO_ROOT / "backend" / "data"
DB_PATH = DATA_DIR / "pawtale.db"
PROJECTS_DIR = DATA_DIR / "projects"
BACKUPS_DIR = DATA_DIR / "backups"


TABLES_TO_COUNT = [
    "users",
    "projects",
    "document_groups",
    "documents",
    "reference_items",
    "document_references",
    "writing_sessions",
    "characters",
    "scenes",
    "audio_files",
    "memoir_voice_sessions",
    "memoir_chat_messages",
    "memoir_voice_utterances",
    "memoir_transcript_versions",
    "memoir_book_plans",
    "memoir_book_plan_chapters",
    "memoir_book_chapter_drafts",
    "memoir_book_chapter_draft_versions",
    "memoir_book_outputs",
    "memoir_book_output_versions",
    "memoir_chapter_contracts",
    "memoir_chapter_nodes",
    "memoir_stories",
    "memoir_claims",
    "memoir_claim_evidence",
    "memoir_draft_blocks",
    "memoir_draft_block_versions",
]
OBJECT_REFERENCE_COLUMNS = {
    "audio_files": ("storage_path",),
    "memoir_image_assets": ("storage_path", "thumbnail_storage_path"),
    "memoir_material_share_videos": ("video_storage_path",),
}


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


def backup_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)


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


def write_object_references(db_path: Path, output_path: Path) -> dict[str, int]:
    references: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    with sqlite3.connect(db_path) as conn:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table_name, columns in OBJECT_REFERENCE_COLUMNS.items():
            if table_name not in existing:
                counts[table_name] = 0
                continue
            column_sql = ", ".join(("id", *columns))
            table_count = 0
            for row in conn.execute(f"SELECT {column_sql} FROM {table_name}"):
                record_id = str(row[0])
                for column_name, value in zip(columns, row[1:]):
                    path = str(value or "").strip()
                    if not path:
                        continue
                    references.append(
                        {
                            "table": table_name,
                            "id": record_id,
                            "column": column_name,
                            "path": path,
                        }
                    )
                    table_count += 1
            counts[table_name] = table_count
    output_path.write_text(
        json.dumps(references, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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


def sqlite_database_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database).expanduser().resolve()


def validate_backup_manifest(
    manifest_path: Path,
    *,
    expected_source_database: Path | None = None,
) -> dict[str, object]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("sqlite_integrity_check") != "ok":
        raise RuntimeError("backup manifest does not contain a successful integrity check")
    source_database = Path(str(payload.get("source_database") or "")).expanduser().resolve()
    if expected_source_database and source_database != expected_source_database.resolve():
        raise RuntimeError(
            "backup manifest source database does not match the runtime database"
        )
    backup_database = Path(str(payload.get("backup_database") or "")).expanduser().resolve()
    if not backup_database.is_file():
        raise RuntimeError("backup database referenced by the manifest is missing")
    expected_sha256 = str(payload.get("backup_database_sha256") or "").strip()
    if not expected_sha256:
        raise RuntimeError("backup manifest does not contain the backup database SHA-256")
    if sha256_file(backup_database) != expected_sha256:
        raise RuntimeError("backup database SHA-256 does not match the manifest")
    if integrity_check(backup_database) != "ok":
        raise RuntimeError("backup database failed SQLite integrity_check")
    return payload


def create_backup(
    label: str | None = None,
    prefix: str = "runtime_data_backup",
    *,
    db_path: Path = DB_PATH,
    projects_dir: Path = PROJECTS_DIR,
    backend_data_dir: Path = BACKEND_DATA_DIR,
    backups_dir: Path = BACKUPS_DIR,
) -> Path:
    db_path = db_path.expanduser().resolve()
    projects_dir = projects_dir.expanduser().resolve()
    backend_data_dir = backend_data_dir.expanduser().resolve()
    backups_dir = backups_dir.expanduser().resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {db_path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    backup_dir = backups_dir / f"{prefix}_{timestamp}{suffix}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    db_backup = backup_dir / "pawtale.db"
    backup_database(db_path, db_backup)
    dump_database(db_backup, backup_dir / "pawtale.dump.sql")
    write_schema(db_backup, backup_dir / "schema.sql")
    object_reference_counts = write_object_references(
        db_backup,
        backup_dir / "object-references.json",
    )

    projects_copied = copy_optional_tree(projects_dir, backup_dir / "projects")
    backend_data_copied = copy_optional_tree(
        backend_data_dir,
        backup_dir / "backend_data",
    )

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "repo_root": str(REPO_ROOT),
        "source_database": str(db_path),
        "backup_database": str(db_backup),
        "backup_database_sha256": sha256_file(db_backup),
        "sqlite_integrity_check": integrity_check(db_backup),
        "table_counts": table_counts(db_backup),
        "object_reference_counts": object_reference_counts,
        "object_reference_count": sum(object_reference_counts.values()),
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
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--projects-dir", type=Path, default=PROJECTS_DIR)
    parser.add_argument("--backend-data-dir", type=Path, default=BACKEND_DATA_DIR)
    parser.add_argument("--backup-root", type=Path, default=BACKUPS_DIR)
    args = parser.parse_args()

    backup_dir = create_backup(
        args.label,
        db_path=args.database,
        projects_dir=args.projects_dir,
        backend_data_dir=args.backend_data_dir,
        backups_dir=args.backup_root,
    )
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
