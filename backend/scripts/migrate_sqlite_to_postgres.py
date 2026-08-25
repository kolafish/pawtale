#!/usr/bin/env python3
"""Import a validated Pawtale SQLite backup into an empty PostgreSQL database."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Table, and_, exists, func, inspect, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.database import Base, register_models
from app.migrations.postgres_runtime import assert_postgres_runtime_schema
from scripts.backup_runtime_data import sha256_file, validate_backup_manifest


def _normalized_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat(timespec="microseconds")}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat(timespec="microseconds")}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$bytes": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"$float": str(value)}
    if isinstance(value, dict):
        return {
            str(key): _normalized_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalized_value(item) for item in value]
    return value


def _update_digest(digest, table: Table, row: dict[str, Any]) -> None:
    payload = [_normalized_value(row[column.name]) for column in table.columns]
    digest.update(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    digest.update(b"\n")


def _ordered_table_select(table: Table):
    primary_key = tuple(table.primary_key.columns)
    if not primary_key:
        raise RuntimeError(f"table has no primary key: {table.name}")
    return select(table).order_by(*primary_key)


async def _table_fingerprint(
    connection: AsyncConnection,
    table: Table,
) -> dict[str, int | str]:
    digest = hashlib.sha256()
    count = 0
    async with connection.stream(_ordered_table_select(table)) as result:
        async for row in result.mappings():
            _update_digest(digest, table, dict(row))
            count += 1
    return {"count": count, "sha256": digest.hexdigest()}


async def _source_table_names(connection: AsyncConnection) -> set[str]:
    return await connection.run_sync(
        lambda sync_connection: set(inspect(sync_connection).get_table_names())
    )


async def _validate_sqlite_source(connection: AsyncConnection) -> None:
    if connection.dialect.name != "sqlite":
        raise RuntimeError("migration source must be SQLite")
    expected_tables = set(Base.metadata.tables)
    missing_tables = expected_tables - await _source_table_names(connection)
    if missing_tables:
        raise RuntimeError(
            "SQLite backup is missing model tables: "
            + ", ".join(sorted(missing_tables))
        )
    violations = (await connection.execute(text("PRAGMA foreign_key_check"))).all()
    if violations:
        first = violations[0]
        raise RuntimeError(
            "SQLite backup failed foreign_key_check: "
            f"{len(violations)} violation(s), first={tuple(first)}"
        )


async def _target_nonempty_tables(connection: AsyncConnection) -> dict[str, int]:
    nonempty: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        count = int(await connection.scalar(select(func.count()).select_from(table)) or 0)
        if count:
            nonempty[table.name] = count
    return nonempty


async def _set_user_triggers(
    connection: AsyncConnection,
    *,
    enabled: bool,
) -> None:
    action = "ENABLE" if enabled else "DISABLE"
    quote = connection.dialect.identifier_preparer.quote
    tables = reversed(Base.metadata.sorted_tables) if enabled else Base.metadata.sorted_tables
    for table in tables:
        await connection.execute(
            text(f"ALTER TABLE {quote(table.name)} {action} TRIGGER USER")
        )


async def _copy_table(
    source: AsyncConnection,
    target: AsyncConnection,
    table: Table,
    *,
    chunk_size: int,
) -> dict[str, int | str]:
    digest = hashlib.sha256()
    count = 0
    chunk: list[dict[str, Any]] = []
    async with source.stream(_ordered_table_select(table)) as result:
        async for mapping in result.mappings():
            row = dict(mapping)
            _update_digest(digest, table, row)
            chunk.append(row)
            count += 1
            if len(chunk) >= chunk_size:
                await target.execute(insert(table), chunk)
                chunk.clear()
    if chunk:
        await target.execute(insert(table), chunk)
    return {"count": count, "sha256": digest.hexdigest()}


async def _foreign_key_violations(
    connection: AsyncConnection,
) -> dict[str, int]:
    violations: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        for index, constraint in enumerate(table.foreign_key_constraints):
            elements = tuple(constraint.elements)
            parent_table = elements[0].column.table
            child = table.alias("child")
            parent = parent_table.alias("parent")
            child_columns = [child.c[element.parent.name] for element in elements]
            parent_match = and_(
                *(
                    child.c[element.parent.name]
                    == parent.c[element.column.name]
                    for element in elements
                )
            )
            statement = (
                select(func.count())
                .select_from(child)
                .where(
                    and_(*(column.is_not(None) for column in child_columns)),
                    ~exists(select(1).select_from(parent).where(parent_match)),
                )
            )
            name = constraint.name or f"{table.name}:fk:{index}"
            violations[name] = int(await connection.scalar(statement) or 0)
    return violations


async def _source_fingerprints(
    connection: AsyncConnection,
) -> dict[str, dict[str, int | str]]:
    return {
        table.name: await _table_fingerprint(connection, table)
        for table in Base.metadata.sorted_tables
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def migrate_sqlite_backup_to_postgres(
    manifest_path: Path,
    target_database_url: str,
    *,
    execute: bool = False,
    writers_stopped: bool = False,
    chunk_size: int = 200,
    report_path: Path | None = None,
) -> dict[str, object]:
    if execute and not writers_stopped:
        raise RuntimeError("database writers must be stopped before executing migration")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    register_models()
    manifest_path = manifest_path.expanduser().resolve()
    manifest = validate_backup_manifest(manifest_path)
    source_database = Path(str(manifest["backup_database"])).expanduser().resolve()
    target_url = make_url(target_database_url)
    if target_url.get_backend_name() != "postgresql":
        raise RuntimeError("migration target must be PostgreSQL")

    source_engine = create_async_engine(f"sqlite+aiosqlite:///{source_database}")
    target_engine = create_async_engine(target_database_url, pool_pre_ping=True)
    started_at = datetime.now(timezone.utc)
    try:
        async with source_engine.connect() as source:
            await _validate_sqlite_source(source)
            await assert_postgres_runtime_schema(target_engine)
            async with target_engine.connect() as target:
                nonempty = await _target_nonempty_tables(target)
            if nonempty:
                details = ", ".join(
                    f"{table}={count}" for table, count in sorted(nonempty.items())
                )
                raise RuntimeError(f"PostgreSQL target is not empty: {details}")

            table_results: dict[str, dict[str, int | str]]
            foreign_keys: dict[str, int] = {}
            if execute:
                table_results = {}
                async with target_engine.begin() as target:
                    await _set_user_triggers(target, enabled=False)
                    for table in Base.metadata.sorted_tables:
                        source_result = await _copy_table(
                            source,
                            target,
                            table,
                            chunk_size=chunk_size,
                        )
                        table_results[table.name] = source_result
                    await _set_user_triggers(target, enabled=True)
                    for table in Base.metadata.sorted_tables:
                        source_result = table_results[table.name]
                        target_result = await _table_fingerprint(target, table)
                        if target_result != source_result:
                            raise RuntimeError(
                                f"migration verification failed for {table.name}: "
                                f"source={source_result} target={target_result}"
                            )
                    foreign_keys = await _foreign_key_violations(target)
                    broken_foreign_keys = {
                        name: count for name, count in foreign_keys.items() if count
                    }
                    if broken_foreign_keys:
                        raise RuntimeError(
                            "PostgreSQL foreign-key verification failed: "
                            + json.dumps(broken_foreign_keys, sort_keys=True)
                        )
                await assert_postgres_runtime_schema(target_engine)
            else:
                table_results = await _source_fingerprints(source)

        finished_at = datetime.now(timezone.utc)
        report: dict[str, object] = {
            "executed": execute,
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "manifest": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "source_database": str(source_database),
            "source_database_sha256": manifest["backup_database_sha256"],
            "target_database": target_url.render_as_string(hide_password=True),
            "table_count": len(table_results),
            "row_count": sum(int(item["count"]) for item in table_results.values()),
            "tables": table_results,
            "foreign_key_violations": foreign_keys,
        }
        if report_path is not None:
            report["report_path"] = str(report_path.expanduser().resolve())
            _write_report(report_path, report)
        return report
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--target-database-url",
        default=os.getenv("POSTGRES_DATABASE_URL", ""),
    )
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    if not args.target_database_url:
        parser.error(
            "--target-database-url or POSTGRES_DATABASE_URL is required"
        )
    report = asyncio.run(
        migrate_sqlite_backup_to_postgres(
            args.manifest,
            args.target_database_url,
            execute=args.execute,
            writers_stopped=args.writers_stopped,
            chunk_size=args.chunk_size,
            report_path=args.report,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
