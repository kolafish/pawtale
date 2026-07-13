from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

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


def _legacy_block_id(draft_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"pawtale:memoir-draft-block:legacy:{draft_id}"))


def _legacy_block_version_id(draft_version_id: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"pawtale:memoir-draft-block-version:legacy:{draft_version_id}",
        )
    )


async def backfill_legacy_chapter_drafts(conn) -> dict[str, int]:
    if not await _table_exists(conn, "memoir_book_chapter_drafts") or not await _table_exists(
        conn, "memoir_book_chapter_draft_versions"
    ):
        return {"blocks": 0, "versions": 0, "drafts_without_versions": 0}

    invalid_drafts = (
        await conn.execute(
            text(
                """
                SELECT draft.id
                FROM memoir_book_chapter_drafts AS draft
                LEFT JOIN memoir_book_plan_chapters AS chapter
                  ON chapter.id = draft.chapter_id
                 AND chapter.project_id = draft.project_id
                WHERE chapter.id IS NULL
                ORDER BY draft.id
                LIMIT 20
                """
            )
        )
    ).scalars().all()
    invalid_versions = (
        await conn.execute(
            text(
                """
                SELECT version.id
                FROM memoir_book_chapter_draft_versions AS version
                LEFT JOIN memoir_book_chapter_drafts AS draft
                  ON draft.id = version.draft_id
                 AND draft.project_id = version.project_id
                 AND draft.chapter_id = version.chapter_id
                WHERE draft.id IS NULL
                ORDER BY version.id
                LIMIT 20
                """
            )
        )
    ).scalars().all()
    if invalid_drafts or invalid_versions:
        details = []
        if invalid_drafts:
            details.append(f"drafts={','.join(str(item) for item in invalid_drafts)}")
        if invalid_versions:
            details.append(
                f"versions={','.join(str(item) for item in invalid_versions)}"
            )
        raise RuntimeError(
            "legacy chapter draft ownership mismatch; repair before migration: "
            + "; ".join(details)
        )

    drafts = (
        await conn.execute(
            text(
                """
                SELECT draft.id, draft.project_id, draft.chapter_id, draft.status,
                       draft.created_at, draft.updated_at
                FROM memoir_book_chapter_drafts AS draft
                WHERE EXISTS (
                    SELECT 1
                    FROM memoir_book_chapter_draft_versions AS version
                    WHERE version.draft_id = draft.id
                )
                ORDER BY draft.project_id, draft.chapter_id, draft.id
                """
            )
        )
    ).mappings().all()
    drafts_without_versions = int(
        (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM memoir_book_chapter_drafts AS draft
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM memoir_book_chapter_draft_versions AS version
                        WHERE version.draft_id = draft.id
                    )
                    """
                )
            )
        ).scalar_one()
    )
    inserted_blocks = 0
    inserted_versions = 0
    for draft in drafts:
        block_id = _legacy_block_id(str(draft["id"]))
        existing_block = (
            await conn.execute(
                text(
                    """
                    SELECT id, current_version_id
                    FROM memoir_draft_blocks
                    WHERE legacy_draft_id = :draft_id
                    """
                ),
                {"draft_id": draft["id"]},
            )
        ).mappings().one_or_none()
        if existing_block is None:
            next_sort_order = int(
                (
                    await conn.execute(
                        text(
                            """
                            SELECT COALESCE(MAX(sort_order), -1) + 1
                            FROM memoir_draft_blocks
                            WHERE project_id = :project_id AND chapter_id = :chapter_id
                            """
                        ),
                        {
                            "project_id": draft["project_id"],
                            "chapter_id": draft["chapter_id"],
                        },
                    )
                ).scalar_one()
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO memoir_draft_blocks (
                        id, project_id, chapter_id, node_id, block_role, sort_order,
                        editorial_status, freshness_status, locked, current_version_id,
                        legacy_draft_id, created_at, updated_at
                    ) VALUES (
                        :id, :project_id, :chapter_id, NULL, 'legacy_snapshot', :sort_order,
                        :editorial_status, 'current', 0, NULL,
                        :legacy_draft_id, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": block_id,
                    "project_id": draft["project_id"],
                    "chapter_id": draft["chapter_id"],
                    "sort_order": next_sort_order,
                    "editorial_status": (
                        "confirmed" if draft["status"] == "confirmed" else "draft"
                    ),
                    "legacy_draft_id": draft["id"],
                    "created_at": draft["created_at"],
                    "updated_at": draft["updated_at"],
                },
            )
            inserted_blocks += 1
            current_version_id = None
        else:
            block_id = str(existing_block["id"])
            current_version_id = existing_block["current_version_id"]

        legacy_versions = (
            await conn.execute(
                text(
                    """
                    SELECT id, version, content, created_by, model, prompt_snapshot,
                           feedback, created_at
                    FROM memoir_book_chapter_draft_versions
                    WHERE draft_id = :draft_id
                    ORDER BY version, id
                    """
                ),
                {"draft_id": draft["id"]},
            )
        ).mappings().all()
        latest_legacy_version_id: str | None = None
        for legacy_version in legacy_versions:
            existing_version = (
                await conn.execute(
                    text(
                        """
                        SELECT id
                        FROM memoir_draft_block_versions
                        WHERE legacy_draft_version_id = :legacy_draft_version_id
                        """
                    ),
                    {"legacy_draft_version_id": legacy_version["id"]},
                )
            ).scalar_one_or_none()
            if existing_version is not None:
                latest_legacy_version_id = str(existing_version)
                continue

            version_id = _legacy_block_version_id(str(legacy_version["id"]))
            preferred_version_available = (
                await conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM memoir_draft_block_versions
                        WHERE project_id = :project_id
                          AND block_id = :block_id
                          AND version = :version
                        """
                    ),
                    {
                        "project_id": draft["project_id"],
                        "block_id": block_id,
                        "version": legacy_version["version"],
                    },
                )
            ).scalar_one_or_none() is None
            block_version_number = int(legacy_version["version"])
            if not preferred_version_available:
                block_version_number = int(
                    (
                        await conn.execute(
                            text(
                                """
                                SELECT COALESCE(MAX(version), 0) + 1
                                FROM memoir_draft_block_versions
                                WHERE project_id = :project_id AND block_id = :block_id
                                """
                            ),
                            {
                                "project_id": draft["project_id"],
                                "block_id": block_id,
                            },
                        )
                    ).scalar_one()
                )
            await conn.execute(
                text(
                    """
                    INSERT INTO memoir_draft_block_versions (
                        id, project_id, block_id, version, content, created_by, model,
                        prompt_snapshot, feedback, legacy_draft_version_id, created_at
                    ) VALUES (
                        :id, :project_id, :block_id, :version, :content, :created_by, :model,
                        :prompt_snapshot, :feedback, :legacy_draft_version_id, :created_at
                    )
                    """
                ),
                {
                    "id": version_id,
                    "project_id": draft["project_id"],
                    "block_id": block_id,
                    "version": block_version_number,
                    "content": legacy_version["content"],
                    "created_by": (
                        legacy_version["created_by"]
                        if legacy_version["created_by"] in {"ai", "user", "system"}
                        else "system"
                    ),
                    "model": legacy_version["model"],
                    "prompt_snapshot": legacy_version["prompt_snapshot"] or "",
                    "feedback": legacy_version["feedback"],
                    "legacy_draft_version_id": legacy_version["id"],
                    "created_at": legacy_version["created_at"],
                },
            )
            inserted_versions += 1
            latest_legacy_version_id = version_id

        current_is_legacy = False
        if current_version_id is not None:
            current_is_legacy = (
                await conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM memoir_draft_block_versions
                        WHERE id = :version_id AND legacy_draft_version_id IS NOT NULL
                        """
                    ),
                    {"version_id": current_version_id},
                )
            ).scalar_one_or_none() is not None
        if latest_legacy_version_id and (current_version_id is None or current_is_legacy):
            has_editorial_decision = (
                await conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM memoir_narrative_decisions
                        WHERE project_id = :project_id
                          AND target_type = 'draft_block'
                          AND target_id = :block_id
                          AND action IN ('confirm', 'exclude')
                        LIMIT 1
                        """
                    ),
                    {
                        "project_id": draft["project_id"],
                        "block_id": block_id,
                    },
                )
            ).scalar_one_or_none() is not None
            await conn.execute(
                text(
                    """
                    UPDATE memoir_draft_blocks
                    SET current_version_id = :current_version_id,
                        editorial_status = CASE
                            WHEN :has_editorial_decision = 1 THEN editorial_status
                            ELSE :editorial_status
                        END,
                        updated_at = CASE
                            WHEN updated_at > :updated_at THEN updated_at
                            ELSE :updated_at
                        END
                    WHERE id = :block_id
                    """
                ),
                {
                    "current_version_id": latest_legacy_version_id,
                    "editorial_status": (
                        "confirmed" if draft["status"] == "confirmed" else "draft"
                    ),
                    "has_editorial_decision": int(has_editorial_decision),
                    "updated_at": draft["updated_at"],
                    "block_id": block_id,
                },
            )

    return {
        "blocks": inserted_blocks,
        "versions": inserted_versions,
        "drafts_without_versions": drafts_without_versions,
    }


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
              OR NEW.legacy_draft_id IS NOT OLD.legacy_draft_id
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
            BEGIN
                SELECT RAISE(ABORT, 'draft block story sources are immutable');
            END
        """,
    }
    for trigger_name, statement in statements.items():
        if await _trigger_exists(conn, trigger_name):
            continue
        await conn.execute(text(statement))
        changed.append(trigger_name)
    return changed
