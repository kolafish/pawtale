from __future__ import annotations

import os
from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from app.database import Base, register_models
from app.models.projects import Project
from app.models.users import AuthIdentity, User
from scripts.backup_runtime_data import create_backup
from scripts.migrate_sqlite_to_postgres import migrate_sqlite_backup_to_postgres


POSTGRES_DATABASE_URL = os.getenv("TEST_POSTGRES_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not POSTGRES_DATABASE_URL,
    reason="TEST_POSTGRES_DATABASE_URL is not configured",
)


def _source_backup(tmp_path, *, orphaned_project: bool = False):
    register_models()
    database = tmp_path / "pawtale.db"
    engine = create_engine(f"sqlite:///{database}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            if not orphaned_project:
                session.add(
                    User(
                        id="user-1",
                        email="laoyu@example.com",
                        password_hash="hash",
                        password_login_enabled=False,
                        role="user",
                        display_name="老余",
                        created_at=datetime(2026, 8, 1, 9, 30),
                        updated_at=datetime(2026, 8, 2, 10, 45),
                    )
                )
                session.add(
                    AuthIdentity(
                        id="identity-1",
                        user_id="user-1",
                        provider="wechat",
                        provider_user_id="openid-1",
                        profile_json={"nickname": "老余", "tags": ["讲述者"]},
                    )
                )
            session.add(
                Project(
                    id="project-1",
                    user_id="missing-user" if orphaned_project else "user-1",
                    type="memoir",
                    title="老余口述",
                    description="一段真实的人生。",
                    metadata_json={"preferences": {"pace": "slow"}},
                    memoir_data_revision=9,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    return create_backup(
        "postgres",
        db_path=database,
        projects_dir=tmp_path / "projects",
        memoir_dir=tmp_path / "memoir",
        backend_data_dir=tmp_path / "backend-data",
        backups_dir=tmp_path / "backups",
    )


async def test_migrates_validated_sqlite_backup_with_exact_content(test_db, tmp_path):
    backup_dir = _source_backup(tmp_path)
    report_path = tmp_path / "migration-report.json"

    report = await migrate_sqlite_backup_to_postgres(
        backup_dir / "manifest.json",
        POSTGRES_DATABASE_URL,
        execute=True,
        writers_stopped=True,
        chunk_size=1,
        report_path=report_path,
    )

    assert report["executed"] is True
    assert report["table_count"] == len(Base.metadata.tables)
    assert report["row_count"] == 3
    assert report["tables"]["users"]["count"] == 1
    assert report["tables"]["projects"]["count"] == 1
    assert all(count == 0 for count in report["foreign_key_violations"].values())
    assert report_path.is_file()

    engine = create_async_engine(POSTGRES_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            user = (
                await connection.execute(select(User.__table__))
            ).mappings().one()
            project = (
                await connection.execute(select(Project.__table__))
            ).mappings().one()
        assert user["display_name"] == "老余"
        assert user["password_login_enabled"] is False
        assert project["metadata_json"] == {"preferences": {"pace": "slow"}}
        assert project["memoir_data_revision"] == 9
    finally:
        await engine.dispose()


async def test_rejects_sqlite_backup_with_foreign_key_violation(test_db, tmp_path):
    backup_dir = _source_backup(tmp_path, orphaned_project=True)

    with pytest.raises(RuntimeError, match="foreign_key_check"):
        await migrate_sqlite_backup_to_postgres(
            backup_dir / "manifest.json",
            POSTGRES_DATABASE_URL,
        )


async def test_verification_failure_rolls_back_entire_import(
    test_db,
    tmp_path,
    monkeypatch,
):
    backup_dir = _source_backup(tmp_path)
    from scripts import migrate_sqlite_to_postgres as migration

    real_fingerprint = migration._table_fingerprint

    async def mismatched_project_fingerprint(connection, table):
        result = await real_fingerprint(connection, table)
        if connection.dialect.name == "postgresql" and table.name == "projects":
            return {**result, "sha256": "mismatch"}
        return result

    monkeypatch.setattr(
        migration,
        "_table_fingerprint",
        mismatched_project_fingerprint,
    )

    with pytest.raises(RuntimeError, match="verification failed for projects"):
        await migrate_sqlite_backup_to_postgres(
            backup_dir / "manifest.json",
            POSTGRES_DATABASE_URL,
            execute=True,
            writers_stopped=True,
        )

    engine = create_async_engine(POSTGRES_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(select(func.count()).select_from(User)) == 0
            assert await connection.scalar(select(func.count()).select_from(Project)) == 0
            disabled_triggers = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE NOT tgisinternal AND tgenabled != 'O'"
                )
            )
            assert disabled_triggers == 0
    finally:
        await engine.dispose()


async def test_execute_requires_stopped_writers(test_db, tmp_path):
    backup_dir = _source_backup(tmp_path)

    with pytest.raises(RuntimeError, match="writers must be stopped"):
        await migrate_sqlite_backup_to_postgres(
            backup_dir / "manifest.json",
            POSTGRES_DATABASE_URL,
            execute=True,
        )
