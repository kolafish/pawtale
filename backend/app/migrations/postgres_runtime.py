from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models
from app.migrations.memoir_client_snapshots import (
    DIRECT_PROJECT_TABLES,
    INDIRECT_PROJECT_EXPRESSIONS,
)
from app.migrations.memoir_narrative import MEMOIR_NARRATIVE_TRIGGERS
from app.migrations.memoir_voice import VOICE_INTEGRITY_TRIGGERS


POSTGRES_SCHEMA_REVISION = "3b5e639b4b76"
POSTGRES_TRANSCRIPT_INDEXES = {
    "ix_memoir_voice_utterances_transcript_edited_trgm",
    "ix_memoir_voice_utterances_transcript_raw_trgm",
}
POSTGRES_REVISION_TRIGGERS = {
    *(f"trg_memoir_revision_{table}" for table in DIRECT_PROJECT_TABLES),
    *(f"trg_memoir_revision_{table}" for table in INDIRECT_PROJECT_EXPRESSIONS),
    "trg_memoir_revision_projects_content_update",
}
POSTGRES_RUNTIME_TRIGGERS = {
    *VOICE_INTEGRITY_TRIGGERS,
    *MEMOIR_NARRATIVE_TRIGGERS,
    *POSTGRES_REVISION_TRIGGERS,
}


async def assert_postgres_runtime_schema(engine: AsyncEngine) -> None:
    register_models()
    async with engine.connect() as conn:
        actual_tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
        expected_tables = set(Base.metadata.tables)
        missing_tables = expected_tables - actual_tables
        unexpected_tables = actual_tables - expected_tables - {"alembic_version"}

        missing_columns: list[str] = []
        unexpected_columns: list[str] = []
        for table_name, table in sorted(Base.metadata.tables.items()):
            if table_name not in actual_tables:
                continue
            columns = await conn.run_sync(
                lambda sync_conn, name=table_name: {
                    column["name"] for column in inspect(sync_conn).get_columns(name)
                }
            )
            expected_columns = set(table.columns.keys())
            missing_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(expected_columns - columns)
            )
            unexpected_columns.extend(
                f"{table_name}.{column}"
                for column in sorted(columns - expected_columns)
            )

        revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        extensions = set(
            (await conn.scalars(text("SELECT extname FROM pg_extension"))).all()
        )
        triggers = set(
            (
                await conn.scalars(
                    text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
                )
            ).all()
        )
        indexes = set(
            (
                await conn.scalars(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = current_schema()"
                    )
                )
            ).all()
        )

    details: list[str] = []
    if revision != POSTGRES_SCHEMA_REVISION:
        details.append(
            f"alembic revision expected={POSTGRES_SCHEMA_REVISION} actual={revision}"
        )
    if missing_tables:
        details.append("missing tables: " + ", ".join(sorted(missing_tables)))
    if unexpected_tables:
        details.append("unexpected tables: " + ", ".join(sorted(unexpected_tables)))
    if missing_columns:
        details.append("missing columns: " + ", ".join(missing_columns))
    if unexpected_columns:
        details.append("unexpected columns: " + ", ".join(unexpected_columns))
    if "pg_trgm" not in extensions:
        details.append("missing extension: pg_trgm")
    missing_indexes = POSTGRES_TRANSCRIPT_INDEXES - indexes
    if missing_indexes:
        details.append("missing indexes: " + ", ".join(sorted(missing_indexes)))
    missing_triggers = POSTGRES_RUNTIME_TRIGGERS - triggers
    if missing_triggers:
        details.append("missing triggers: " + ", ".join(sorted(missing_triggers)))
    if details:
        raise RuntimeError(
            "PostgreSQL runtime schema mismatch; "
            + "; ".join(details)
            + "; run `PYTHONPATH=. alembic upgrade head` before starting the API"
        )
