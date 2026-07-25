from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import register_models


REVISION_COLUMN = "memoir_data_revision"
DIRECT_PROJECT_TABLES = (
    "memoir_automation_events",
    "memoir_book_chapter_draft_versions",
    "memoir_book_chapter_drafts",
    "memoir_book_image_placements",
    "memoir_book_output_versions",
    "memoir_book_outputs",
    "memoir_book_plan_chapters",
    "memoir_book_plans",
    "memoir_book_reviews",
    "memoir_chapter_digests",
    "memoir_chat_messages",
    "memoir_completion_issues",
    "memoir_editing_jobs",
    "memoir_image_assets",
    "memoir_material_share_videos",
    "memoir_project_frameworks",
    "memoir_section_digests",
    "memoir_section_materials",
    "memoir_sections",
    "memoir_share_video_outputs",
    "memoir_voice_sessions",
)
INDIRECT_PROJECT_EXPRESSIONS = {
    "memoir_framework_item_materials": (
        "SELECT project_id FROM memoir_project_frameworks "
        "WHERE id = (SELECT project_framework_id FROM memoir_framework_items "
        "WHERE id = {row}.framework_item_id)"
    ),
    "memoir_framework_items": (
        "SELECT project_id FROM memoir_project_frameworks "
        "WHERE id = {row}.project_framework_id"
    ),
    "memoir_interview_policy_decisions": (
        "SELECT project_id FROM memoir_voice_sessions WHERE id = {row}.session_id"
    ),
    "memoir_interview_policy_evaluations": (
        "SELECT project_id FROM memoir_voice_sessions "
        "WHERE id = (SELECT session_id FROM memoir_interview_policy_decisions "
        "WHERE id = {row}.decision_id)"
    ),
    "memoir_transcript_alignments": (
        "SELECT project_id FROM memoir_voice_sessions "
        "WHERE id = (SELECT voice_session_id FROM memoir_voice_utterances "
        "WHERE id = {row}.utterance_id)"
    ),
    "memoir_transcript_versions": (
        "SELECT project_id FROM memoir_voice_sessions WHERE id = {row}.voice_session_id"
    ),
    "memoir_voice_authorization_consents": (
        "SELECT project_id FROM memoir_voice_sessions WHERE id = {row}.source_session_id"
    ),
    "memoir_voice_utterances": (
        "SELECT project_id FROM memoir_voice_sessions WHERE id = {row}.voice_session_id"
    ),
}


def _trigger_name(table_name: str, operation: str) -> str:
    return f"trg_memoir_revision_{table_name}_{operation.lower()}"


def _revision_trigger_sql(
    table_name: str,
    operation: str,
    project_expression: str,
) -> str:
    row_name = "OLD" if operation == "DELETE" else "NEW"
    expression = project_expression.format(row=row_name)
    if operation == "UPDATE":
        old_expression = project_expression.format(row="OLD")
        targets = f"({old_expression}), ({expression})"
    else:
        targets = f"({expression})"
    return (
        f'CREATE TRIGGER IF NOT EXISTS "{_trigger_name(table_name, operation)}" '
        f'AFTER {operation} ON "{table_name}" '
        "BEGIN "
        f"UPDATE projects SET {REVISION_COLUMN} = {REVISION_COLUMN} + 1 "
        f"WHERE id IN ({targets}) AND type = 'memoir'; "
        "END"
    )


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )


async def migrate_memoir_client_snapshot_schema(
    engine: AsyncEngine,
) -> dict[str, list[str]]:
    register_models()
    changed_columns: list[str] = []
    changed_triggers: list[str] = []
    async with engine.begin() as conn:
        project_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns("projects")
            }
        )
        if REVISION_COLUMN not in project_columns:
            await conn.execute(
                text(
                    f"ALTER TABLE projects ADD COLUMN {REVISION_COLUMN} "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
            changed_columns.append(f"projects.{REVISION_COLUMN}")

        tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
        trigger_rows = (
            await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        ).scalars()
        existing_triggers = {str(name) for name in trigger_rows}
        expressions = {
            **{
                table_name: "{row}.project_id"
                for table_name in DIRECT_PROJECT_TABLES
            },
            **INDIRECT_PROJECT_EXPRESSIONS,
        }
        for table_name, expression in expressions.items():
            if table_name not in tables:
                continue
            for operation in ("INSERT", "UPDATE", "DELETE"):
                trigger_name = _trigger_name(table_name, operation)
                if trigger_name not in existing_triggers:
                    await conn.execute(
                        text(_revision_trigger_sql(table_name, operation, expression))
                    )
                    changed_triggers.append(trigger_name)

        project_trigger_name = _trigger_name("projects", "content_update")
        if project_trigger_name not in existing_triggers:
            await conn.execute(
                text(
                    f'CREATE TRIGGER IF NOT EXISTS "{project_trigger_name}" '
                    "AFTER UPDATE OF title, description, metadata_json ON projects "
                    "WHEN NEW.type = 'memoir' "
                    "BEGIN "
                    f"UPDATE projects SET {REVISION_COLUMN} = {REVISION_COLUMN} + 1 "
                    "WHERE id = NEW.id; "
                    "END"
                )
            )
            changed_triggers.append(project_trigger_name)
    return {
        "changed_columns": changed_columns,
        "changed_triggers": changed_triggers,
    }


async def assert_memoir_client_snapshot_schema(engine: AsyncEngine) -> None:
    tables = await _table_names(engine)
    async with engine.connect() as conn:
        project_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns("projects")
            }
        )
        trigger_rows = (
            await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        ).scalars()
        triggers = {str(name) for name in trigger_rows}
    if REVISION_COLUMN not in project_columns:
        raise RuntimeError(f"projects is missing required column: {REVISION_COLUMN}")

    expected_triggers = {
        _trigger_name(table_name, operation)
        for table_name in set(DIRECT_PROJECT_TABLES) | set(INDIRECT_PROJECT_EXPRESSIONS)
        if table_name in tables
        for operation in ("INSERT", "UPDATE", "DELETE")
    }
    expected_triggers.add(_trigger_name("projects", "content_update"))
    missing_triggers = expected_triggers - triggers
    if missing_triggers:
        raise RuntimeError(
            "memoir client snapshot revision triggers are missing: "
            + ", ".join(sorted(missing_triggers))
        )
