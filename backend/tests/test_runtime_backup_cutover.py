import hashlib
import json
import sqlite3
from argparse import Namespace

import pytest

from scripts.backup_runtime_data import create_backup
from scripts.cutover_memoir_narrative import (
    _validate_backup_manifest,
    _validate_run_args,
)
from scripts.restore_runtime_data import restore_database


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_final_cutover_check_cannot_be_scoped_to_one_project():
    args = Namespace(stage="check", project_id="project-1", execute=False)

    with pytest.raises(RuntimeError, match="all active memoir projects"):
        _validate_run_args(args)


def test_process_ingestion_is_an_executable_cutover_stage():
    args = Namespace(stage="process-ingestion", project_id=None, execute=True)

    _validate_run_args(args)


def test_runtime_backup_manifest_is_bound_to_its_source_database(tmp_path):
    database = tmp_path / "pawtale.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO projects (id) VALUES ('project-1')")

    backup_dir = create_backup(
        "cutover",
        db_path=database,
        projects_dir=tmp_path / "projects",
        backend_data_dir=tmp_path / "backend-data",
        backups_dir=tmp_path / "backups",
    )
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["source_database"] == str(database.resolve())
    assert manifest["backup_database_sha256"] == _sha256(
        backup_dir / "pawtale.db"
    )
    assert manifest["table_counts"]["projects"] == 1
    assert _validate_backup_manifest(
        manifest_path,
        runtime_database=database,
    ) == manifest


def test_cutover_rejects_wrong_or_modified_backup(tmp_path):
    database = tmp_path / "pawtale.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")

    backup_dir = create_backup(
        db_path=database,
        projects_dir=tmp_path / "projects",
        backend_data_dir=tmp_path / "backend-data",
        backups_dir=tmp_path / "backups",
    )
    manifest_path = backup_dir / "manifest.json"

    with pytest.raises(RuntimeError, match="does not match the runtime database"):
        _validate_backup_manifest(
            manifest_path,
            runtime_database=tmp_path / "another.db",
        )

    with (backup_dir / "pawtale.db").open("ab") as file:
        file.write(b"modified")
    with pytest.raises(RuntimeError, match="SHA-256 does not match"):
        _validate_backup_manifest(
            manifest_path,
            runtime_database=database,
        )


def test_restore_preserves_current_database_then_restores_validated_backup(tmp_path):
    database = tmp_path / "pawtale.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO projects (id) VALUES ('before-cutover')")
    backup_dir = create_backup(
        db_path=database,
        projects_dir=tmp_path / "projects",
        backend_data_dir=tmp_path / "backend-data",
        backups_dir=tmp_path / "backups",
    )
    manifest_path = backup_dir / "manifest.json"
    with sqlite3.connect(database) as conn:
        conn.execute("DELETE FROM projects")
        conn.execute("INSERT INTO projects (id) VALUES ('after-cutover')")

    dry_run = restore_database(manifest_path, target_database=database)
    assert dry_run["executed"] is False
    with pytest.raises(RuntimeError, match="writers are stopped"):
        restore_database(
            manifest_path,
            target_database=database,
            execute=True,
        )

    restored = restore_database(
        manifest_path,
        target_database=database,
        execute=True,
        writers_stopped=True,
    )

    assert restored["executed"] is True
    assert restored["restored_integrity_check"] == "ok"
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT id FROM projects").fetchall() == [
            ("before-cutover",)
        ]
    with sqlite3.connect(restored["current_database_snapshot"]) as conn:
        assert conn.execute("SELECT id FROM projects").fetchall() == [
            ("after-cutover",)
        ]


def test_restore_copy_failure_leaves_current_database_untouched(tmp_path, monkeypatch):
    database = tmp_path / "pawtale.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO projects (id) VALUES ('before-cutover')")
    backup_dir = create_backup(
        db_path=database,
        projects_dir=tmp_path / "projects",
        backend_data_dir=tmp_path / "backend-data",
        backups_dir=tmp_path / "backups",
    )
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE projects SET id = 'current-production'")

    def fail_copy(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("scripts.restore_runtime_data.shutil.copy2", fail_copy)

    with pytest.raises(OSError, match="disk full"):
        restore_database(
            backup_dir / "manifest.json",
            target_database=database,
            execute=True,
            writers_stopped=True,
        )

    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT id FROM projects").fetchall() == [
            ("current-production",)
        ]
    assert not list(tmp_path.glob(".pawtale.db.restore-*.tmp"))
