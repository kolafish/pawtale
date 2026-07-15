from __future__ import annotations

from sqlalchemy import text


MEMOIR_DRAFT_BLOCK_TRIGGERS = (
    "memoir_draft_blocks_identity_immutable",
    "memoir_draft_blocks_current_version_insert",
    "memoir_draft_blocks_current_version_update",
    "memoir_draft_blocks_factual_sources_insert",
    "memoir_draft_blocks_factual_sources_update",
    "memoir_draft_blocks_locked_version_update",
    "memoir_draft_block_versions_no_update",
    "memoir_draft_block_versions_no_delete",
    "memoir_draft_block_claims_no_update",
    "memoir_draft_block_claims_no_delete",
    "memoir_draft_block_stories_no_update",
    "memoir_draft_block_stories_no_delete",
)


async def _table_exists(conn, table_name: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return result.scalar_one_or_none() is not None


async def _trigger_exists(conn, trigger_name: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = :trigger_name"),
        {"trigger_name": trigger_name},
    )
    return result.scalar_one_or_none() is not None


async def _trigger_sql(conn, trigger_name: str) -> str:
    result = await conn.execute(
        text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'trigger' AND name = :trigger_name"
        ),
        {"trigger_name": trigger_name},
    )
    return str(result.scalar_one_or_none() or "")


async def _replace_unguarded_delete_trigger(conn, trigger_name: str) -> None:
    current_sql = await _trigger_sql(conn, trigger_name)
    if current_sql and "__delete_in_progress" not in current_sql:
        await conn.execute(text(f"DROP TRIGGER {trigger_name}"))


async def ensure_draft_block_triggers(conn) -> list[str]:
    changed: list[str] = []
    statements = {
        "memoir_draft_blocks_identity_immutable": """
            CREATE TRIGGER memoir_draft_blocks_identity_immutable
            BEFORE UPDATE ON memoir_draft_blocks
            WHEN NEW.id IS NOT OLD.id
              OR NEW.project_id IS NOT OLD.project_id
              OR NEW.chapter_id IS NOT OLD.chapter_id
              OR NEW.node_id IS NOT OLD.node_id
              OR NEW.block_role IS NOT OLD.block_role
              OR NEW.created_at IS NOT OLD.created_at
            BEGIN
                SELECT RAISE(ABORT, 'draft block identity is immutable');
            END
        """,
        "memoir_draft_blocks_current_version_insert": """
            CREATE TRIGGER memoir_draft_blocks_current_version_insert
            BEFORE INSERT ON memoir_draft_blocks
            WHEN NEW.current_version_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1
                FROM memoir_draft_block_versions AS version
                WHERE version.id = NEW.current_version_id
                  AND version.project_id = NEW.project_id
                  AND version.block_id = NEW.id
              )
            BEGIN
                SELECT RAISE(ABORT, 'draft block current version must belong to the block');
            END
        """,
        "memoir_draft_blocks_current_version_update": """
            CREATE TRIGGER memoir_draft_blocks_current_version_update
            BEFORE UPDATE OF current_version_id ON memoir_draft_blocks
            WHEN NEW.current_version_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1
                FROM memoir_draft_block_versions AS version
                WHERE version.id = NEW.current_version_id
                  AND version.project_id = NEW.project_id
                  AND version.block_id = NEW.id
              )
            BEGIN
                SELECT RAISE(ABORT, 'draft block current version must belong to the block');
            END
        """,
        "memoir_draft_blocks_factual_sources_insert": """
            CREATE TRIGGER memoir_draft_blocks_factual_sources_insert
            BEFORE INSERT ON memoir_draft_blocks
            WHEN NEW.block_role = 'factual'
              AND NEW.current_version_id IS NOT NULL
              AND (
                NOT EXISTS (
                    SELECT 1
                    FROM memoir_draft_block_claims AS claim_source
                    WHERE claim_source.project_id = NEW.project_id
                      AND claim_source.block_version_id = NEW.current_version_id
                )
                OR NOT EXISTS (
                    SELECT 1
                    FROM memoir_draft_block_stories AS story_source
                    WHERE story_source.project_id = NEW.project_id
                      AND story_source.block_version_id = NEW.current_version_id
                )
              )
            BEGIN
                SELECT RAISE(ABORT, 'factual draft block requires story and claim sources');
            END
        """,
        "memoir_draft_blocks_factual_sources_update": """
            CREATE TRIGGER memoir_draft_blocks_factual_sources_update
            BEFORE UPDATE OF current_version_id ON memoir_draft_blocks
            WHEN NEW.block_role = 'factual'
              AND NEW.current_version_id IS NOT NULL
              AND (
                NOT EXISTS (
                    SELECT 1
                    FROM memoir_draft_block_claims AS claim_source
                    WHERE claim_source.project_id = NEW.project_id
                      AND claim_source.block_version_id = NEW.current_version_id
                )
                OR NOT EXISTS (
                    SELECT 1
                    FROM memoir_draft_block_stories AS story_source
                    WHERE story_source.project_id = NEW.project_id
                      AND story_source.block_version_id = NEW.current_version_id
                )
              )
            BEGIN
                SELECT RAISE(ABORT, 'factual draft block requires story and claim sources');
            END
        """,
        "memoir_draft_blocks_locked_version_update": """
            CREATE TRIGGER memoir_draft_blocks_locked_version_update
            BEFORE UPDATE OF current_version_id ON memoir_draft_blocks
            WHEN OLD.locked = 1
              AND NEW.current_version_id IS NOT OLD.current_version_id
              AND EXISTS (
                SELECT 1
                FROM memoir_draft_block_versions AS version
                WHERE version.id = NEW.current_version_id
                  AND version.project_id = NEW.project_id
                  AND version.block_id = NEW.id
                  AND version.created_by != 'user'
              )
            BEGIN
                SELECT RAISE(ABORT, 'locked draft block rejects automatic versions');
            END
        """,
        "memoir_draft_block_versions_no_update": """
            CREATE TRIGGER memoir_draft_block_versions_no_update
            BEFORE UPDATE ON memoir_draft_block_versions
            BEGIN
                SELECT RAISE(ABORT, 'draft block versions are immutable');
            END
        """,
        "memoir_draft_block_versions_no_delete": """
            CREATE TRIGGER memoir_draft_block_versions_no_delete
            BEFORE DELETE ON memoir_draft_block_versions
            WHEN EXISTS (
                SELECT 1
                FROM memoir_draft_blocks AS block
                WHERE block.project_id = OLD.project_id
                  AND block.id = OLD.block_id
            )
              AND EXISTS (
                SELECT 1
                FROM projects
                WHERE id = OLD.project_id
                  AND COALESCE(
                    json_extract(metadata_json, '$.__delete_in_progress'),
                    0
                  ) != 1
              )
            BEGIN
                SELECT RAISE(ABORT, 'draft block versions are append-only');
            END
        """,
        "memoir_draft_block_claims_no_update": """
            CREATE TRIGGER memoir_draft_block_claims_no_update
            BEFORE UPDATE ON memoir_draft_block_claims
            BEGIN
                SELECT RAISE(ABORT, 'draft block claim sources are immutable');
            END
        """,
        "memoir_draft_block_claims_no_delete": """
            CREATE TRIGGER memoir_draft_block_claims_no_delete
            BEFORE DELETE ON memoir_draft_block_claims
            WHEN EXISTS (
                SELECT 1
                FROM memoir_draft_block_versions AS version
                WHERE version.project_id = OLD.project_id
                  AND version.id = OLD.block_version_id
            )
              AND EXISTS (
                SELECT 1
                FROM projects
                WHERE id = OLD.project_id
                  AND COALESCE(
                    json_extract(metadata_json, '$.__delete_in_progress'),
                    0
                  ) != 1
              )
            BEGIN
                SELECT RAISE(ABORT, 'draft block claim sources are immutable');
            END
        """,
        "memoir_draft_block_stories_no_update": """
            CREATE TRIGGER memoir_draft_block_stories_no_update
            BEFORE UPDATE ON memoir_draft_block_stories
            BEGIN
                SELECT RAISE(ABORT, 'draft block story sources are immutable');
            END
        """,
        "memoir_draft_block_stories_no_delete": """
            CREATE TRIGGER memoir_draft_block_stories_no_delete
            BEFORE DELETE ON memoir_draft_block_stories
            WHEN EXISTS (
                SELECT 1
                FROM memoir_draft_block_versions AS version
                WHERE version.project_id = OLD.project_id
                  AND version.id = OLD.block_version_id
            )
              AND EXISTS (
                SELECT 1
                FROM projects
                WHERE id = OLD.project_id
                  AND COALESCE(
                    json_extract(metadata_json, '$.__delete_in_progress'),
                    0
                  ) != 1
              )
            BEGIN
                SELECT RAISE(ABORT, 'draft block story sources are immutable');
            END
        """,
    }
    guarded_delete_triggers = {
        "memoir_draft_block_versions_no_delete",
        "memoir_draft_block_claims_no_delete",
        "memoir_draft_block_stories_no_delete",
    }
    for trigger_name, statement in statements.items():
        if trigger_name in guarded_delete_triggers:
            await _replace_unguarded_delete_trigger(conn, trigger_name)
        if await _trigger_exists(conn, trigger_name):
            continue
        await conn.execute(text(statement))
        changed.append(trigger_name)
    return changed
