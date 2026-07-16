import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.database import Base, register_models
from app.migrations.memoir_narrative import MEMOIR_NARRATIVE_TABLES
from scripts.backup_runtime_data import (
    SQLITE_SIDECAR_SUFFIXES,
    create_backup,
    validate_backup_checksums,
    validate_backup_manifest,
    write_object_references,
)
from scripts.restore_runtime_data import restore_database


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_runtime_backup_manifest_is_bound_to_its_source_database(tmp_path):
    database = tmp_path / "pawtale.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO projects (id) VALUES ('project-1')")

    backup_dir = create_backup(
        "cutover",
        db_path=database,
        projects_dir=tmp_path / "projects",
        memoir_dir=tmp_path / "memoir",
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
    assert validate_backup_manifest(
        manifest_path,
        expected_source_database=database,
    ) == manifest


def test_runtime_backup_checksums_are_stable_for_wal_database(tmp_path):
    database = tmp_path / "pawtale.db"
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO projects (id) VALUES ('project-1')")
        conn.commit()

        backup_dir = create_backup(
            db_path=database,
            projects_dir=tmp_path / "projects",
            memoir_dir=tmp_path / "memoir",
            backend_data_dir=tmp_path / "backend-data",
            backups_dir=tmp_path / "backups",
        )

    backup_database = backup_dir / "pawtale.db"
    with sqlite3.connect(backup_database) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT id FROM projects").fetchall() == [("project-1",)]
    assert all(
        not (backup_dir / f"pawtale.db{suffix}").exists()
        for suffix in SQLITE_SIDECAR_SUFFIXES
    )
    assert validate_backup_checksums(backup_dir) > 0

    (backup_dir / "object-references.json").write_text("[{}]\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="backup checksum does not match"):
        validate_backup_manifest(
            backup_dir / "manifest.json",
            expected_source_database=database,
        )


def test_data_preparation_rejects_wrong_or_modified_backup(tmp_path):
    database = tmp_path / "pawtale.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")

    backup_dir = create_backup(
        db_path=database,
        projects_dir=tmp_path / "projects",
        memoir_dir=tmp_path / "memoir",
        backend_data_dir=tmp_path / "backend-data",
        backups_dir=tmp_path / "backups",
    )
    manifest_path = backup_dir / "manifest.json"

    with pytest.raises(RuntimeError, match="does not match the runtime database"):
        validate_backup_manifest(
            manifest_path,
            expected_source_database=tmp_path / "another.db",
        )

    with (backup_dir / "pawtale.db").open("ab") as file:
        file.write(b"modified")
    with pytest.raises(RuntimeError, match="SHA-256 does not match"):
        validate_backup_manifest(
            manifest_path,
            expected_source_database=database,
        )


def test_restore_preserves_current_database_then_restores_validated_backup(tmp_path):
    database = tmp_path / "pawtale.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO projects (id) VALUES ('before-cutover')")
    backup_dir = create_backup(
        db_path=database,
        projects_dir=tmp_path / "projects",
        memoir_dir=tmp_path / "memoir",
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
        memoir_dir=tmp_path / "memoir",
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


def test_runtime_backup_counts_every_narrative_table(tmp_path):
    database = tmp_path / "pawtale.db"
    register_models()
    engine = create_engine(f"sqlite:///{database}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    backup_dir = create_backup(
        db_path=database,
        projects_dir=tmp_path / "projects",
        memoir_dir=tmp_path / "memoir",
        backend_data_dir=tmp_path / "backend-data",
        backups_dir=tmp_path / "backups",
    )
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))

    assert set(MEMOIR_NARRATIVE_TABLES) <= set(manifest["table_counts"])


def test_runtime_backup_indexes_share_video_output_files(tmp_path):
    database = tmp_path / "pawtale.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE memoir_share_video_outputs ("
            "id TEXT PRIMARY KEY, storage_path TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO memoir_share_video_outputs (id, storage_path) "
            "VALUES ('output-1', 'memoir/share-videos/user/job.mp4')"
        )
    output_path = tmp_path / "object-references.json"

    counts = write_object_references(database, output_path)
    references = json.loads(output_path.read_text(encoding="utf-8"))

    assert counts["memoir_share_video_outputs"] == 1
    assert references == [
        {
            "table": "memoir_share_video_outputs",
            "id": "output-1",
            "column": "storage_path",
            "path": "memoir/share-videos/user/job.mp4",
        }
    ]


def test_restore_replaces_projects_memoir_and_backend_data_trees(tmp_path):
    database = tmp_path / "pawtale.db"
    projects_dir = tmp_path / "projects"
    memoir_dir = tmp_path / "memoir"
    backend_data_dir = tmp_path / "backend-data"
    (projects_dir / "project-1").mkdir(parents=True)
    (memoir_dir / "audio").mkdir(parents=True)
    backend_data_dir.mkdir(parents=True)
    (projects_dir / "project-1" / "document.md").write_text(
        "backup document", encoding="utf-8"
    )
    (memoir_dir / "audio" / "utterance.pcm").write_bytes(b"backup audio")
    (backend_data_dir / "runtime.json").write_text("backup", encoding="utf-8")
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")

    backup_dir = create_backup(
        db_path=database,
        projects_dir=projects_dir,
        memoir_dir=memoir_dir,
        backend_data_dir=backend_data_dir,
        backups_dir=tmp_path / "backups",
    )
    (projects_dir / "project-1" / "document.md").write_text(
        "current document", encoding="utf-8"
    )
    (memoir_dir / "audio" / "utterance.pcm").write_bytes(b"current audio")
    (backend_data_dir / "runtime.json").write_text("current", encoding="utf-8")

    restored = restore_database(
        backup_dir / "manifest.json",
        target_database=database,
        execute=True,
        writers_stopped=True,
    )

    assert (projects_dir / "project-1" / "document.md").read_text(
        encoding="utf-8"
    ) == "backup document"
    assert (memoir_dir / "audio" / "utterance.pcm").read_bytes() == b"backup audio"
    assert (backend_data_dir / "runtime.json").read_text(
        encoding="utf-8"
    ) == "backup"
    snapshots = restored["data_trees"]
    assert Path(snapshots["projects"]["current_snapshot"]).joinpath(
        "project-1", "document.md"
    ).read_text(encoding="utf-8") == "current document"
    assert Path(snapshots["memoir"]["current_snapshot"]).joinpath(
        "audio", "utterance.pcm"
    ).read_bytes() == b"current audio"


def test_restore_tree_failure_rolls_back_database_and_applied_trees(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "pawtale.db"
    projects_dir = tmp_path / "projects"
    memoir_dir = tmp_path / "memoir"
    backend_data_dir = tmp_path / "backend-data"
    for directory in (projects_dir, memoir_dir, backend_data_dir):
        directory.mkdir()
        (directory / "value.txt").write_text("backup", encoding="utf-8")
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO projects (id) VALUES ('backup')")
    backup_dir = create_backup(
        db_path=database,
        projects_dir=projects_dir,
        memoir_dir=memoir_dir,
        backend_data_dir=backend_data_dir,
        backups_dir=tmp_path / "backups",
    )
    for directory in (projects_dir, memoir_dir, backend_data_dir):
        (directory / "value.txt").write_text("current", encoding="utf-8")
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE projects SET id = 'current'")

    real_replace = os.replace

    def fail_memoir_restore(source, destination):
        if Path(source).name.startswith(".memoir.restore-"):
            raise OSError("memoir restore failed")
        return real_replace(source, destination)

    monkeypatch.setattr("scripts.restore_runtime_data.os.replace", fail_memoir_restore)

    with pytest.raises(OSError, match="memoir restore failed"):
        restore_database(
            backup_dir / "manifest.json",
            target_database=database,
            execute=True,
            writers_stopped=True,
        )

    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT id FROM projects").fetchall() == [("current",)]
    for directory in (projects_dir, memoir_dir, backend_data_dir):
        assert (directory / "value.txt").read_text(encoding="utf-8") == "current"
