from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models
from app.migrations.audit import migrate_audit_schema
from app.migrations.auth import migrate_auth_schema
from app.migrations.git_sync import migrate_git_sync_schema
from app.migrations.memoir_images import assert_memoir_image_schema, migrate_memoir_image_schema
from app.migrations.memoir_semantic import migrate_memoir_semantic_schema
from app.migrations.memoir_voice import assert_memoir_voice_schema, migrate_memoir_voice_schema
from app.migrations.runtime_base import assert_base_runtime_schema


async def migrate_runtime_schema(engine: AsyncEngine) -> dict[str, object]:
    """Apply explicit schema migrations for the current application version."""
    register_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    auth = await migrate_auth_schema(engine)
    audit = await migrate_audit_schema(engine)
    git_sync = await migrate_git_sync_schema(engine)
    memoir_voice = await migrate_memoir_voice_schema(engine)
    memoir_semantic = await migrate_memoir_semantic_schema(engine)
    memoir_images = await migrate_memoir_image_schema(engine)
    await assert_runtime_schema(engine)
    return {
        "auth": auth,
        "audit": audit,
        "git_sync": git_sync,
        "memoir_voice": memoir_voice,
        "memoir_semantic": memoir_semantic,
        "memoir_images": memoir_images,
    }


async def assert_runtime_schema(engine: AsyncEngine) -> None:
    """Validate runtime schema without creating tables or altering columns."""
    await assert_base_runtime_schema(engine)
    await assert_memoir_voice_schema(engine)
    await assert_memoir_image_schema(engine)
