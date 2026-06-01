from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth import hash_password, normalize_email
from app.config import get_settings


OWNED_TABLES = ["projects", "characters", "scenes"]


async def _table_columns(conn, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}


async def _table_exists(conn, table: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": table},
    )
    return result.scalar_one_or_none() is not None


async def ensure_default_user(conn) -> str:
    settings = get_settings()
    email = normalize_email(settings.local_default_user_email)
    result = await conn.execute(text("SELECT id FROM users WHERE email = :email"), {"email": email})
    existing_id = result.scalar_one_or_none()
    if existing_id:
        return str(existing_id)

    user_id = str(uuid.uuid4())
    now = datetime.utcnow()
    await conn.execute(
        text(
            """
            INSERT INTO users (id, email, password_hash, display_name, avatar_url, created_at, updated_at)
            VALUES (:id, :email, :password_hash, :display_name, NULL, :created_at, :updated_at)
            """
        ),
        {
            "id": user_id,
            "email": email,
            "password_hash": hash_password(settings.local_default_user_password),
            "display_name": settings.local_default_user_display_name,
            "created_at": now,
            "updated_at": now,
        },
    )
    return user_id


async def migrate_auth_schema(engine: AsyncEngine) -> dict:
    """Add auth ownership columns and assign existing local data to a default user."""
    async with engine.begin() as conn:
        default_user_id = await ensure_default_user(conn)
        changed_columns: list[str] = []

        for table in OWNED_TABLES:
            if not await _table_exists(conn, table):
                continue
            columns = await _table_columns(conn, table)
            if "user_id" not in columns:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id VARCHAR(36)"))
                changed_columns.append(f"{table}.user_id")
            await conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table} (user_id)")
            )

        for table in ("projects",):
            if await _table_exists(conn, table):
                await conn.execute(
                    text(f"UPDATE {table} SET user_id = :user_id WHERE user_id IS NULL"),
                    {"user_id": default_user_id},
                )

        for table in ("characters", "scenes"):
            if await _table_exists(conn, table):
                columns = await _table_columns(conn, table)
                if "is_preset" in columns:
                    await conn.execute(
                        text(
                            f"""
                            UPDATE {table}
                            SET user_id = :user_id
                            WHERE user_id IS NULL AND COALESCE(is_preset, 0) = 0
                            """
                        ),
                        {"user_id": default_user_id},
                    )
                else:
                    await conn.execute(
                        text(f"UPDATE {table} SET user_id = :user_id WHERE user_id IS NULL"),
                        {"user_id": default_user_id},
                    )

    return {
        "default_user_id": default_user_id,
        "changed_columns": changed_columns,
    }
