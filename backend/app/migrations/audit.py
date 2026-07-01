from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _table_exists(conn, table: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": table},
    )
    return result.scalar_one_or_none() is not None


async def migrate_audit_schema(engine: AsyncEngine) -> dict:
    changed_tables: list[str] = []

    async with engine.begin() as conn:
        if not await _table_exists(conn, "audit_sessions"):
            await conn.execute(
                text(
                    """
                    CREATE TABLE audit_sessions (
                        id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        client_session_id VARCHAR(120) NOT NULL,
                        platform VARCHAR(30) NOT NULL,
                        started_at DATETIME NOT NULL,
                        last_seen_at DATETIME NOT NULL,
                        ended_at DATETIME,
                        active_ms BIGINT NOT NULL DEFAULT 0,
                        exit_page_path VARCHAR(255) NOT NULL DEFAULT '',
                        exit_feature_code VARCHAR(120) NOT NULL DEFAULT '',
                        exit_reason VARCHAR(60) NOT NULL DEFAULT '',
                        app_version VARCHAR(80) NOT NULL DEFAULT '',
                        device_summary VARCHAR(255) NOT NULL DEFAULT '',
                        metadata_json JSON NOT NULL DEFAULT '{}',
                        created_at DATETIME,
                        updated_at DATETIME,
                        PRIMARY KEY (id),
                        FOREIGN KEY(user_id) REFERENCES users (id),
                        CONSTRAINT uq_audit_sessions_user_client_session
                            UNIQUE (user_id, client_session_id)
                    )
                    """
                )
            )
            changed_tables.append("audit_sessions")

        if not await _table_exists(conn, "audit_events"):
            await conn.execute(
                text(
                    """
                    CREATE TABLE audit_events (
                        id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        session_id VARCHAR(36) NOT NULL,
                        client_event_id VARCHAR(120) NOT NULL,
                        event_type VARCHAR(40) NOT NULL,
                        event_date DATE NOT NULL,
                        platform VARCHAR(30) NOT NULL,
                        page_path VARCHAR(255) NOT NULL DEFAULT '',
                        feature_code VARCHAR(120) NOT NULL DEFAULT '',
                        project_id VARCHAR(36),
                        voice_session_id VARCHAR(36),
                        occurred_at DATETIME NOT NULL,
                        duration_ms INTEGER NOT NULL DEFAULT 0,
                        active_ms INTEGER NOT NULL DEFAULT 0,
                        exit_reason VARCHAR(60) NOT NULL DEFAULT '',
                        metadata_json JSON NOT NULL DEFAULT '{}',
                        created_at DATETIME,
                        PRIMARY KEY (id),
                        FOREIGN KEY(user_id) REFERENCES users (id),
                        FOREIGN KEY(session_id) REFERENCES audit_sessions (id),
                        CONSTRAINT uq_audit_events_user_client_event
                            UNIQUE (user_id, client_event_id)
                    )
                    """
                )
            )
            changed_tables.append("audit_events")

        if not await _table_exists(conn, "audit_daily_user_usage"):
            await conn.execute(
                text(
                    """
                    CREATE TABLE audit_daily_user_usage (
                        id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        date DATE NOT NULL,
                        platform VARCHAR(30) NOT NULL,
                        active_ms BIGINT NOT NULL DEFAULT 0,
                        session_count INTEGER NOT NULL DEFAULT 0,
                        event_count INTEGER NOT NULL DEFAULT 0,
                        last_exit_page_path VARCHAR(255) NOT NULL DEFAULT '',
                        last_exit_feature_code VARCHAR(120) NOT NULL DEFAULT '',
                        last_exit_reason VARCHAR(60) NOT NULL DEFAULT '',
                        last_seen_at DATETIME,
                        updated_at DATETIME,
                        PRIMARY KEY (id),
                        FOREIGN KEY(user_id) REFERENCES users (id),
                        CONSTRAINT uq_audit_daily_user_usage_user_date_platform
                            UNIQUE (user_id, date, platform)
                    )
                    """
                )
            )
            changed_tables.append("audit_daily_user_usage")

        if not await _table_exists(conn, "audit_daily_feature_usage"):
            await conn.execute(
                text(
                    """
                    CREATE TABLE audit_daily_feature_usage (
                        id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        date DATE NOT NULL,
                        platform VARCHAR(30) NOT NULL,
                        feature_code VARCHAR(120) NOT NULL,
                        active_ms BIGINT NOT NULL DEFAULT 0,
                        event_count INTEGER NOT NULL DEFAULT 0,
                        last_seen_at DATETIME,
                        metadata_json JSON NOT NULL DEFAULT '{}',
                        updated_at DATETIME,
                        PRIMARY KEY (id),
                        FOREIGN KEY(user_id) REFERENCES users (id),
                        CONSTRAINT uq_audit_daily_feature_usage_user_date_platform_feature
                            UNIQUE (user_id, date, platform, feature_code)
                    )
                    """
                )
            )
            changed_tables.append("audit_daily_feature_usage")

        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_sessions_user_id ON audit_sessions (user_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_sessions_platform ON audit_sessions (platform)"))
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_audit_sessions_last_seen_at ON audit_sessions (last_seen_at)")
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_events_user_id ON audit_events (user_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_events_session_id ON audit_events (session_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_events_event_type ON audit_events (event_type)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_events_event_date ON audit_events (event_date)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_events_platform ON audit_events (platform)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_events_feature_code ON audit_events (feature_code)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_events_project_id ON audit_events (project_id)"))
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_audit_events_voice_session_id ON audit_events (voice_session_id)")
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_events_occurred_at ON audit_events (occurred_at)"))
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_audit_daily_user_usage_user_id ON audit_daily_user_usage (user_id)")
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_daily_user_usage_date ON audit_daily_user_usage (date)"))
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_audit_daily_user_usage_platform ON audit_daily_user_usage (platform)")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_audit_daily_feature_usage_user_id "
                "ON audit_daily_feature_usage (user_id)"
            )
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_audit_daily_feature_usage_date ON audit_daily_feature_usage (date)")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_audit_daily_feature_usage_platform "
                "ON audit_daily_feature_usage (platform)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_audit_daily_feature_usage_feature_code "
                "ON audit_daily_feature_usage (feature_code)"
            )
        )

    return {"changed_tables": changed_tables}
