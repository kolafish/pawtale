from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import Column, Table

from app.database import Base, register_models


REALTIME_VOICE_TABLES = (
    "audio_files",
    "memoir_voice_sessions",
    "memoir_chat_messages",
    "memoir_voice_utterances",
    "memoir_transcript_versions",
)


def _memoir_voice_tables_from_metadata() -> tuple[Table, ...]:
    register_models()
    return tuple(Base.metadata.tables[table_name] for table_name in REALTIME_VOICE_TABLES)


def _column_schema(column: Column) -> dict[str, object]:
    return {
        "name": column.name,
        "nullable": bool(column.nullable),
        "primary_key": bool(column.primary_key),
    }


def expected_memoir_voice_schema() -> dict[str, list[dict[str, object]]]:
    return {
        table.name: [_column_schema(column) for column in table.columns]
        for table in _memoir_voice_tables_from_metadata()
    }


async def _table_columns(conn, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}


async def _table_exists(conn, table: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": table},
    )
    return result.scalar_one_or_none() is not None


async def _table_column_schema(conn, table: str) -> list[dict[str, object]]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return [
        {
            "name": row[1],
            "nullable": not bool(row[3]),
            "primary_key": bool(row[5]),
        }
        for row in result.fetchall()
    ]


async def describe_memoir_voice_schema(engine: AsyncEngine) -> dict[str, list[dict[str, object]]]:
    actual: dict[str, list[dict[str, object]]] = {}
    async with engine.begin() as conn:
        for table_name in REALTIME_VOICE_TABLES:
            if not await _table_exists(conn, table_name):
                actual[table_name] = []
                continue
            actual[table_name] = await _table_column_schema(conn, table_name)
    return actual


async def rebuild_memoir_voice_schema(engine: AsyncEngine) -> None:
    """Drop and recreate only realtime voice tables for development resets."""
    tables = _memoir_voice_tables_from_metadata()
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        for table in reversed(tables):
            await conn.run_sync(lambda sync_conn, table=table: table.drop(sync_conn, checkfirst=True))
        for table in tables:
            await conn.run_sync(lambda sync_conn, table=table: table.create(sync_conn, checkfirst=True))
        await conn.execute(text("PRAGMA foreign_keys=ON"))


async def migrate_memoir_voice_schema(engine: AsyncEngine) -> dict[str, list[str]]:
    """Repair known realtime voice schema drift in existing SQLite databases."""
    changed_columns: list[str] = []
    changed_indexes: list[str] = []
    async with engine.begin() as conn:
        if not await _table_exists(conn, "memoir_transcript_versions"):
            raise RuntimeError("Missing required table: memoir_transcript_versions")

        columns = await _table_columns(conn, "memoir_transcript_versions")
        if "voice_session_id" not in columns:
            await conn.execute(
                text("ALTER TABLE memoir_transcript_versions ADD COLUMN voice_session_id VARCHAR(36)")
            )
            changed_columns.append("memoir_transcript_versions.voice_session_id")

        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_memoir_transcript_versions_voice_session_id "
                "ON memoir_transcript_versions (voice_session_id)"
            )
        )
        changed_indexes.append("ix_memoir_transcript_versions_voice_session_id")

    return {"changed_columns": changed_columns, "changed_indexes": changed_indexes}


async def assert_memoir_voice_schema(engine: AsyncEngine) -> None:
    expected_schema = {
        table: {str(column["name"]): column for column in columns}
        for table, columns in expected_memoir_voice_schema().items()
    }
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    unexpected_columns: list[str] = []
    column_mismatches: list[str] = []

    async with engine.begin() as conn:
        for table_name, expected_columns in expected_schema.items():
            if not await _table_exists(conn, table_name):
                missing_tables.append(table_name)
                continue
            actual_schema = {
                str(column["name"]): column
                for column in await _table_column_schema(conn, table_name)
            }
            expected_names = set(expected_columns)
            actual_names = set(actual_schema)
            missing_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(expected_names - actual_names)
            )
            unexpected_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(actual_names - expected_names)
            )
            for column_name in sorted(expected_names & actual_names):
                expected = expected_columns[column_name]
                actual = actual_schema[column_name]
                for attribute in ("nullable", "primary_key"):
                    if expected[attribute] != actual[attribute]:
                        column_mismatches.append(
                            f"{table_name}.{column_name}.{attribute} "
                            f"expected={expected[attribute]} actual={actual[attribute]}"
                        )

    if missing_tables or missing_columns or unexpected_columns or column_mismatches:
        details: list[str] = []
        if missing_tables:
            details.append(f"missing tables: {', '.join(sorted(missing_tables))}")
        if missing_columns:
            details.append(f"missing columns: {', '.join(sorted(missing_columns))}")
        if unexpected_columns:
            details.append(f"unexpected columns: {', '.join(sorted(unexpected_columns))}")
        if column_mismatches:
            details.append(f"column mismatches: {', '.join(sorted(column_mismatches))}")
        raise RuntimeError(
            "Memoir realtime voice schema mismatch; "
            + "; ".join(details)
            + "; for development databases without data to preserve, run "
            "`python scripts/rebuild_memoir_voice_schema.py --yes`"
        )
