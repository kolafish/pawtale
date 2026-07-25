from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models
from app.migrations.audit import migrate_audit_schema
from app.migrations.auth import migrate_auth_schema
from app.migrations.git_sync import migrate_git_sync_schema
from app.migrations.memoir_completion import (
    assert_memoir_completion_schema,
    migrate_memoir_completion_schema,
)
from app.migrations.memoir_chapters import (
    assert_memoir_chapter_schema,
    migrate_memoir_chapter_schema,
)
from app.migrations.memoir_client_snapshots import (
    assert_memoir_client_snapshot_schema,
    migrate_memoir_client_snapshot_schema,
)
from app.migrations.memoir_books import (
    assert_memoir_book_schema,
    migrate_memoir_book_schema,
)
from app.migrations.memoir_images import assert_memoir_image_schema, migrate_memoir_image_schema
from app.migrations.memoir_narrative import (
    assert_memoir_narrative_schema,
    migrate_memoir_narrative_schema,
)
from app.migrations.memoir_schema_cutover import migrate_memoir_schema_cutover
from app.migrations.memoir_sections import (
    assert_memoir_section_schema,
    migrate_memoir_section_schema,
)
from app.migrations.memoir_voice import assert_memoir_voice_schema, migrate_memoir_voice_schema
from app.migrations.runtime_base import assert_base_runtime_schema
from app.migrations.sqlite_integrity import assert_sqlite_foreign_key_integrity


async def migrate_runtime_schema(engine: AsyncEngine) -> dict[str, object]:
    """Apply explicit schema migrations for the current application version."""
    register_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    memoir_schema_cutover = await migrate_memoir_schema_cutover(engine)
    auth = await migrate_auth_schema(engine)
    audit = await migrate_audit_schema(engine)
    git_sync = await migrate_git_sync_schema(engine)
    memoir_voice = await migrate_memoir_voice_schema(engine)
    memoir_sections = await migrate_memoir_section_schema(engine)
    memoir_completion = await migrate_memoir_completion_schema(engine)
    memoir_chapters = await migrate_memoir_chapter_schema(engine)
    memoir_client_snapshots = await migrate_memoir_client_snapshot_schema(engine)
    memoir_books = await migrate_memoir_book_schema(engine)
    memoir_images = await migrate_memoir_image_schema(engine)
    memoir_narrative = await migrate_memoir_narrative_schema(engine)
    await assert_runtime_schema(engine, require_sqlite_foreign_keys=False)
    return {
        "memoir_schema_cutover": memoir_schema_cutover,
        "auth": auth,
        "audit": audit,
        "git_sync": git_sync,
        "memoir_voice": memoir_voice,
        "memoir_sections": memoir_sections,
        "memoir_completion": memoir_completion,
        "memoir_chapters": memoir_chapters,
        "memoir_client_snapshots": memoir_client_snapshots,
        "memoir_books": memoir_books,
        "memoir_images": memoir_images,
        "memoir_narrative": memoir_narrative,
    }


async def assert_runtime_schema(
    engine: AsyncEngine,
    *,
    require_sqlite_foreign_keys: bool = True,
) -> None:
    """Validate runtime schema without creating tables or altering columns."""
    await assert_base_runtime_schema(engine)
    await assert_memoir_voice_schema(engine)
    await assert_memoir_image_schema(engine)
    await assert_memoir_section_schema(engine)
    await assert_memoir_completion_schema(engine)
    await assert_memoir_chapter_schema(engine)
    await assert_memoir_client_snapshot_schema(engine)
    await assert_memoir_book_schema(engine)
    await assert_memoir_narrative_schema(engine)
    await assert_sqlite_foreign_key_integrity(
        engine,
        require_enforcement=require_sqlite_foreign_keys,
    )
