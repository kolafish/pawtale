from __future__ import annotations

import os
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations.postgres_runtime import assert_postgres_runtime_schema


POSTGRES_DATABASE_URL = os.getenv("TEST_POSTGRES_DATABASE_URL", "").strip()


def _alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", POSTGRES_DATABASE_URL)
    return config


def _assert_dedicated_test_database() -> None:
    database_name = (make_url(POSTGRES_DATABASE_URL).database or "").lower()
    if not (database_name.startswith("test_") or database_name.endswith("_test")):
        raise RuntimeError(
            "TEST_POSTGRES_DATABASE_URL must name a dedicated test database "
            "starting with test_ or ending with _test"
        )


async def _table_names() -> set[str]:
    engine = create_async_engine(POSTGRES_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
    finally:
        await engine.dispose()


async def _assert_runtime_schema() -> None:
    engine = create_async_engine(POSTGRES_DATABASE_URL)
    try:
        await assert_postgres_runtime_schema(engine)
    finally:
        await engine.dispose()


async def _retrieval_index_names() -> set[str]:
    engine = create_async_engine(POSTGRES_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = 'memoir_retrieval_entries'"
                )
            )
            return set(rows.scalars())
    finally:
        await engine.dispose()


def test_postgres_baseline_round_trip_matches_models(postgres_schema):
    _assert_dedicated_test_database()
    config = _alembic_config()

    command.upgrade(config, "head")
    command.check(config)
    asyncio.run(_assert_runtime_schema())

    tables = asyncio.run(_table_names())
    assert "users" in tables
    assert "projects" in tables
    assert "memoir_voice_utterances" in tables
    assert "memoir_book_outputs" in tables
    assert "memoir_project_revision_events" in tables

    retrieval_indexes = asyncio.run(_retrieval_index_names())
    assert "ix_memoir_retrieval_entries_bm25" in retrieval_indexes
    assert "ix_memoir_retrieval_entries_embedding_hnsw" not in retrieval_indexes

    # The current head permanently removes retired interview-policy data.  A
    # downgrade cannot restore those rows, so the migration fails explicitly
    # instead of pretending the schema can be rolled back safely.
    with pytest.raises(RuntimeError, match="c9f4b7e2a601 is irreversible"):
        command.downgrade(config, "base")
