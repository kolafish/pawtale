from __future__ import annotations

from sqlalchemy import text


def _sqlite_identifier(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


async def table_columns(conn, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({_sqlite_identifier(table)})"))
    return {str(row[1]) for row in result.fetchall()}


async def table_exists(conn, table: str) -> bool:
    return await schema_object_exists(conn, object_type="table", name=table)


async def trigger_exists(conn, trigger: str) -> bool:
    return await schema_object_exists(conn, object_type="trigger", name=trigger)


async def schema_object_exists(conn, *, object_type: str, name: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = :object_type AND name = :name"),
        {"object_type": object_type, "name": name},
    )
    return result.scalar_one_or_none() is not None
