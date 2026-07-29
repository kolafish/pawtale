from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations.projects import assert_project_schema, migrate_project_schema


async def test_project_migration_adds_content_revision_idempotently():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE documents (
                        id VARCHAR(36) NOT NULL PRIMARY KEY,
                        name VARCHAR(200) NOT NULL
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO documents (id, name) "
                    "VALUES ('document-1', '已有文章')"
                )
            )

        result = await migrate_project_schema(engine)
        second_result = await migrate_project_schema(engine)
        await assert_project_schema(engine)

        async with engine.begin() as conn:
            columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(documents)"))
                ).fetchall()
            }
            revision = await conn.scalar(
                text(
                    "SELECT content_revision FROM documents "
                    "WHERE id = 'document-1'"
                )
            )

        assert result["changed_columns"] == ["documents.content_revision"]
        assert second_result["changed_columns"] == []
        assert "content_revision" in columns
        assert revision == 0
    finally:
        await engine.dispose()
