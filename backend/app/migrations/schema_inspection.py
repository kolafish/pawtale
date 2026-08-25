from __future__ import annotations

from sqlalchemy import inspect, text


async def table_columns(conn, table: str) -> set[str]:
    columns = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_columns(table))
    return {str(column["name"]) for column in columns}


async def table_exists(conn, table: str) -> bool:
    return await schema_object_exists(conn, object_type="table", name=table)


async def trigger_exists(conn, trigger: str) -> bool:
    return await schema_object_exists(conn, object_type="trigger", name=trigger)


async def schema_object_exists(conn, *, object_type: str, name: str) -> bool:
    if object_type == "table":
        return bool(
            await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table(name))
        )
    if object_type != "trigger":
        raise ValueError(f"Unsupported schema object type: {object_type}")
    if conn.dialect.name == "postgresql":
        result = await conn.execute(
            text(
                """
                SELECT 1
                FROM pg_trigger AS trigger
                JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE trigger.tgname = :name
                  AND NOT trigger.tgisinternal
                  AND namespace.nspname = current_schema()
                """
            ),
            {"name": name},
        )
        return result.scalar_one_or_none() is not None
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = :object_type AND name = :name"),
        {"object_type": object_type, "name": name},
    )
    return result.scalar_one_or_none() is not None
