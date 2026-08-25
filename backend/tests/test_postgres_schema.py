from __future__ import annotations

import os
import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine


POSTGRES_DATABASE_URL = os.getenv("TEST_POSTGRES_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not POSTGRES_DATABASE_URL,
    reason="TEST_POSTGRES_DATABASE_URL is not configured",
)


def _alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", POSTGRES_DATABASE_URL)
    return config


async def _table_names() -> set[str]:
    engine = create_async_engine(POSTGRES_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
    finally:
        await engine.dispose()


def test_postgres_baseline_round_trip_matches_models():
    config = _alembic_config()

    command.upgrade(config, "head")
    command.check(config)

    tables = asyncio.run(_table_names())
    assert "users" in tables
    assert "projects" in tables
    assert "memoir_voice_utterances" in tables
    assert "memoir_book_outputs" in tables

    command.downgrade(config, "base")
    command.upgrade(config, "head")
