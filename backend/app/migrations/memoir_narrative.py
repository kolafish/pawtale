from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import Column, Table

from app.database import Base, register_models


MEMOIR_NARRATIVE_TABLES = (
    "memoir_narrative_entities",
    "memoir_claims",
    "memoir_claim_evidence",
)


def _narrative_tables_from_metadata() -> tuple[Table, ...]:
    register_models()
    return tuple(Base.metadata.tables[table_name] for table_name in MEMOIR_NARRATIVE_TABLES)


def _column_schema(column: Column) -> dict[str, object]:
    return {
        "name": column.name,
        "nullable": bool(column.nullable),
        "primary_key": bool(column.primary_key),
    }


def expected_memoir_narrative_schema() -> dict[str, list[dict[str, object]]]:
    return {
        table.name: [_column_schema(column) for column in table.columns]
        for table in _narrative_tables_from_metadata()
    }


async def _table_exists(conn, table_name: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return result.scalar_one_or_none() is not None


async def _table_column_schema(conn, table_name: str) -> list[dict[str, object]]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table_name})"))).fetchall()
    return [
        {
            "name": row[1],
            "nullable": not bool(row[3]),
            "primary_key": bool(row[5]),
        }
        for row in rows
    ]


async def _table_sql(conn, table_name: str) -> str:
    result = await conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return str(result.scalar_one_or_none() or "")


async def _index_names(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA index_list({table_name})"))).fetchall()
    return {str(row[1]) for row in rows}


async def migrate_memoir_narrative_schema(engine: AsyncEngine) -> dict[str, list[str]]:
    """Create the narrative fact tables without changing existing product reads or writes."""
    changed_tables: list[str] = []
    async with engine.begin() as conn:
        for table in _narrative_tables_from_metadata():
            if not await _table_exists(conn, table.name):
                await conn.run_sync(lambda sync_conn, table=table: table.create(sync_conn, checkfirst=True))
                changed_tables.append(table.name)
    return {"changed_tables": changed_tables}


async def assert_memoir_narrative_schema(engine: AsyncEngine) -> None:
    expected_columns = {
        table_name: {str(column["name"]): column for column in columns}
        for table_name, columns in expected_memoir_narrative_schema().items()
    }
    tables = {table.name: table for table in _narrative_tables_from_metadata()}
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    column_mismatches: list[str] = []
    missing_indexes: list[str] = []
    missing_constraints: list[str] = []

    async with engine.begin() as conn:
        for table_name, expected in expected_columns.items():
            if not await _table_exists(conn, table_name):
                missing_tables.append(table_name)
                continue

            actual = {
                str(column["name"]): column
                for column in await _table_column_schema(conn, table_name)
            }
            missing_columns.extend(
                f"{table_name}.{column_name}"
                for column_name in sorted(set(expected) - set(actual))
            )
            for column_name in sorted(set(expected) & set(actual)):
                for attribute in ("nullable", "primary_key"):
                    if expected[column_name][attribute] != actual[column_name][attribute]:
                        column_mismatches.append(
                            f"{table_name}.{column_name}.{attribute} "
                            f"expected={expected[column_name][attribute]} "
                            f"actual={actual[column_name][attribute]}"
                        )

            table = tables[table_name]
            actual_indexes = await _index_names(conn, table_name)
            missing_indexes.extend(
                index.name
                for index in table.indexes
                if index.name and index.name not in actual_indexes
            )
            actual_sql = await _table_sql(conn, table_name)
            missing_constraints.extend(
                constraint.name
                for constraint in table.constraints
                if constraint.name and constraint.name not in actual_sql
            )

    if missing_tables or missing_columns or column_mismatches or missing_indexes or missing_constraints:
        details: list[str] = []
        if missing_tables:
            details.append(f"missing tables: {', '.join(sorted(missing_tables))}")
        if missing_columns:
            details.append(f"missing columns: {', '.join(sorted(missing_columns))}")
        if column_mismatches:
            details.append(f"column mismatches: {', '.join(sorted(column_mismatches))}")
        if missing_indexes:
            details.append(f"missing indexes: {', '.join(sorted(missing_indexes))}")
        if missing_constraints:
            details.append(f"missing constraints: {', '.join(sorted(missing_constraints))}")
        raise RuntimeError(
            "Memoir narrative schema mismatch; "
            + "; ".join(details)
            + "; run `PYTHONPATH=backend python backend/scripts/migrate_runtime_schema.py` before starting the API"
        )
