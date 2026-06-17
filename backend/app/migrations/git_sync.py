from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _table_exists(conn, table: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": table},
    )
    return result.scalar_one_or_none() is not None


async def migrate_git_sync_schema(engine: AsyncEngine) -> dict:
    changed_tables: list[str] = []

    async with engine.begin() as conn:
        if not await _table_exists(conn, "github_installations"):
            await conn.execute(
                text(
                    """
                    CREATE TABLE github_installations (
                        id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        installation_id BIGINT NOT NULL,
                        account_login VARCHAR(255),
                        account_type VARCHAR(50),
                        repository_selection VARCHAR(50),
                        created_at DATETIME,
                        updated_at DATETIME,
                        PRIMARY KEY (id),
                        FOREIGN KEY(user_id) REFERENCES users (id),
                        CONSTRAINT uq_github_installations_user_installation
                            UNIQUE (user_id, installation_id)
                    )
                    """
                )
            )
            changed_tables.append("github_installations")

        if not await _table_exists(conn, "project_git_sync"):
            await conn.execute(
                text(
                    """
                    CREATE TABLE project_git_sync (
                        project_id VARCHAR(36) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        installation_id BIGINT NOT NULL,
                        repository_owner VARCHAR(255) NOT NULL,
                        repository_name VARCHAR(255) NOT NULL,
                        branch VARCHAR(255) NOT NULL DEFAULT 'main',
                        base_path VARCHAR(500) NOT NULL DEFAULT '',
                        last_synced_at DATETIME,
                        last_commit_sha VARCHAR(64),
                        last_error TEXT,
                        created_at DATETIME,
                        updated_at DATETIME,
                        PRIMARY KEY (project_id),
                        FOREIGN KEY(project_id) REFERENCES projects (id),
                        FOREIGN KEY(user_id) REFERENCES users (id)
                    )
                    """
                )
            )
            changed_tables.append("project_git_sync")

        if not await _table_exists(conn, "github_setup_states"):
            await conn.execute(
                text(
                    """
                    CREATE TABLE github_setup_states (
                        state VARCHAR(120) NOT NULL,
                        user_id VARCHAR(36) NOT NULL,
                        created_at DATETIME NOT NULL,
                        expires_at DATETIME NOT NULL,
                        used_at DATETIME,
                        PRIMARY KEY (state),
                        FOREIGN KEY(user_id) REFERENCES users (id)
                    )
                    """
                )
            )
            changed_tables.append("github_setup_states")

        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_github_installations_user_id ON github_installations (user_id)")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_github_installations_installation_id "
                "ON github_installations (installation_id)"
            )
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_project_git_sync_user_id ON project_git_sync (user_id)"))
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_project_git_sync_installation_id ON project_git_sync (installation_id)")
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_github_setup_states_user_id ON github_setup_states (user_id)")
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_github_setup_states_expires_at ON github_setup_states (expires_at)")
        )

    return {"changed_tables": changed_tables}
