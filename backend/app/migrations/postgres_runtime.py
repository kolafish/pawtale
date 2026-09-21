from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import Base, register_models


POSTGRES_SCHEMA_REVISION = "b71e92c4a603"
POSTGRES_REQUIRED_EXTENSIONS = {
    "pg_search",
    "pg_trgm",
    "vector",
}
POSTGRES_EXTENSION_RUNTIME_TABLES = {"_typmod_cache"}
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
    "memoir_book_revision_proposals",
    "memoir_chapter_digests",
    "memoir_chat_messages",
    "memoir_completion_issues",
    "memoir_image_assets",
    "memoir_material_share_videos",
    "memoir_project_frameworks",
    "memoir_section_digests",
    "memoir_section_materials",
    "memoir_sections",
    "memoir_share_video_outputs",
    "memoir_voice_sessions",
)
INDIRECT_PROJECT_TABLES = (
    "memoir_framework_item_materials",
    "memoir_framework_items",
    "memoir_interview_policy_decisions",
    "memoir_interview_policy_evaluations",
    "memoir_transcript_alignments",
    "memoir_transcript_versions",
    "memoir_voice_authorization_consents",
    "memoir_voice_utterances",
)
MEMOIR_NARRATIVE_TRIGGERS = (
    "memoir_interview_policy_decisions_immutable_selection",
    "memoir_interview_policy_decisions_candidate_membership",
    "memoir_interview_policy_decisions_append_only_evaluation",
    "memoir_interview_policy_evaluations_failure_codes",
    "memoir_interview_policy_evaluations_no_update",
    "memoir_interview_policy_evaluations_no_delete",
)
VOICE_INTEGRITY_TRIGGERS = (
    "memoir_voice_sessions_targets_insert",
    "memoir_voice_sessions_targets_update",
    "memoir_voice_utterances_targets_insert",
    "memoir_voice_utterances_targets_update",
    "memoir_automation_events_job_insert",
    "memoir_automation_events_job_update",
)
POSTGRES_TRANSCRIPT_INDEXES = {
    "ix_memoir_voice_utterances_transcript_edited_trgm",
    "ix_memoir_voice_utterances_transcript_raw_trgm",
}
POSTGRES_RETRIEVAL_INDEXES = {
    "ix_memoir_retrieval_entries_bm25",
    "ix_memoir_retrieval_entries_scope",
}
POSTGRES_RETRIEVAL_TRIGGERS = {
    "memoir_retrieval_entries_source_insert",
    "memoir_retrieval_entries_source_update",
}
POSTGRES_REVISION_TRIGGERS = {
    *(f"trg_memoir_revision_{table}" for table in DIRECT_PROJECT_TABLES),
    *(f"trg_memoir_revision_{table}" for table in INDIRECT_PROJECT_TABLES),
    "trg_memoir_revision_projects_content_update",
}
POSTGRES_RUNTIME_TRIGGERS = {
    *VOICE_INTEGRITY_TRIGGERS,
    *MEMOIR_NARRATIVE_TRIGGERS,
    *POSTGRES_RETRIEVAL_TRIGGERS,
    *POSTGRES_REVISION_TRIGGERS,
}
POSTGRES_RUNTIME_INDEXES = {
    *POSTGRES_TRANSCRIPT_INDEXES,
    *POSTGRES_RETRIEVAL_INDEXES,
    "ix_memoir_project_revision_events_project_id",
}
POSTGRES_FORBIDDEN_TRIGGERS = {
    "trg_memoir_revision_memoir_editing_jobs",
}


async def assert_postgres_runtime_schema(engine: AsyncEngine) -> None:
    register_models()
    async with engine.connect() as conn:
        actual_tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
        expected_tables = set(Base.metadata.tables)
        missing_tables = expected_tables - actual_tables

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
        extension_tables = set(
            (
                await conn.scalars(
                    text(
                        """
                        SELECT relation.relname
                        FROM pg_class AS relation
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        JOIN pg_depend AS dependency
                          ON dependency.classid = 'pg_class'::regclass
                         AND dependency.objid = relation.oid
                         AND dependency.deptype = 'e'
                        JOIN pg_extension AS extension
                          ON extension.oid = dependency.refobjid
                        WHERE namespace.nspname = current_schema()
                        """
                    )
                )
            ).all()
        )
        triggers = set(
            (
                await conn.scalars(
                    text(
                        """
                        SELECT trigger.tgname
                        FROM pg_trigger AS trigger
                        JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE NOT trigger.tgisinternal
                          AND namespace.nspname = current_schema()
                        """
                    )
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
        unexpected_tables = (
            actual_tables
            - expected_tables
            - extension_tables
            - POSTGRES_EXTENSION_RUNTIME_TABLES
            - {"alembic_version"}
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
    missing_extensions = POSTGRES_REQUIRED_EXTENSIONS - extensions
    if missing_extensions:
        details.append(
            "missing extensions: " + ", ".join(sorted(missing_extensions))
        )
    missing_indexes = POSTGRES_RUNTIME_INDEXES - indexes
    if missing_indexes:
        details.append("missing indexes: " + ", ".join(sorted(missing_indexes)))
    missing_triggers = POSTGRES_RUNTIME_TRIGGERS - triggers
    if missing_triggers:
        details.append("missing triggers: " + ", ".join(sorted(missing_triggers)))
    forbidden_triggers = POSTGRES_FORBIDDEN_TRIGGERS & triggers
    if forbidden_triggers:
        details.append(
            "forbidden triggers: " + ", ".join(sorted(forbidden_triggers))
        )
    if details:
        raise RuntimeError(
            "PostgreSQL runtime schema mismatch; "
            + "; ".join(details)
            + "; run `PYTHONPATH=. alembic upgrade head` before starting the API"
        )
