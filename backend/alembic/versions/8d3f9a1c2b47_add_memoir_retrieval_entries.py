"""add memoir retrieval entries

Revision ID: 8d3f9a1c2b47
Revises: 3b5e639b4b76
Create Date: 2026-08-30 15:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa


revision: str = "8d3f9a1c2b47"
down_revision: Union[str, Sequence[str], None] = "3b5e639b4b76"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_EDITING_JOB_TYPES = (
    "correct_transcript",
    "reprocess_material_offline_asr",
    "generate_material_image",
    "regenerate_chapter_sections",
    "evaluate_interview_policy_shadow",
    "evaluate_interview_policy_outcome",
    "organize_material",
    "update_topic_map",
    "generate_book_editorial_copy",
    "generate_book_review",
    "apply_book_review_answers",
    "generate_book_revision_proposal",
    "generate_chapter_section_revision",
    "prepare_chapter",
)
NEW_EDITING_JOB_TYPES = OLD_EDITING_JOB_TYPES + ("index_retrieval_source",)


def _enum_check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_search"))

    op.drop_constraint(
        "ck_memoir_editing_jobs_type",
        "memoir_editing_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_memoir_editing_jobs_type",
        "memoir_editing_jobs",
        _enum_check("job_type", NEW_EDITING_JOB_TYPES),
    )

    op.create_table(
        "memoir_retrieval_entries",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("voice_session_id", sa.String(length=36), nullable=False),
        sa.Column("utterance_id", sa.String(length=36), nullable=False),
        sa.Column("transcript_version_id", sa.String(length=36), nullable=True),
        sa.Column("audio_file_id", sa.String(length=36), nullable=True),
        sa.Column("entry_kind", sa.String(length=30), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("text_source", sa.String(length=20), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.Column("embedding_model", sa.String(length=120), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "entry_kind IN ('source_text')",
            name="ck_memoir_retrieval_entries_kind",
        ),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="ck_memoir_retrieval_entries_nonnegative_ordinal",
        ),
        sa.CheckConstraint(
            "index_version > 0",
            name="ck_memoir_retrieval_entries_positive_index_version",
        ),
        sa.CheckConstraint(
            "text_source IN ('raw', 'edited')",
            name="ck_memoir_retrieval_entries_text_source",
        ),
        sa.ForeignKeyConstraint(
            ["audio_file_id"],
            ["audio_files.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transcript_version_id"],
            ["memoir_transcript_versions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["utterance_id"],
            ["memoir_voice_utterances.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["voice_session_id"],
            ["memoir_voice_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "utterance_id",
            "entry_kind",
            "ordinal",
            name="uq_memoir_retrieval_entries_source_slot",
        ),
    )
    op.create_index(
        "ix_memoir_retrieval_entries_scope",
        "memoir_retrieval_entries",
        ["project_id", "voice_session_id", "entry_kind"],
        unique=False,
    )
    op.create_index(
        "ix_memoir_retrieval_entries_embedding_hnsw",
        "memoir_retrieval_entries",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX ix_memoir_retrieval_entries_bm25
            ON memoir_retrieval_entries
            USING bm25 (
                id,
                project_id,
                voice_session_id,
                entry_kind,
                (search_text::pdb.jieba)
            )
            WITH (key_field='id')
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION pawtale_validate_memoir_retrieval_entry()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM memoir_voice_utterances AS utterance
                    JOIN memoir_voice_sessions AS session
                      ON session.id = utterance.voice_session_id
                    WHERE utterance.id = NEW.utterance_id
                      AND session.id = NEW.voice_session_id
                      AND session.project_id = NEW.project_id
                      AND (
                          NEW.audio_file_id IS NULL
                          OR utterance.audio_file_id = NEW.audio_file_id
                      )
                ) THEN
                    RAISE EXCEPTION 'invalid memoir retrieval source'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.transcript_version_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1
                    FROM memoir_transcript_versions AS version
                    WHERE version.id = NEW.transcript_version_id
                      AND version.voice_session_id = NEW.voice_session_id
                      AND version.source_artifact_id = NEW.utterance_id
                ) THEN
                    RAISE EXCEPTION 'invalid memoir retrieval transcript version'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER memoir_retrieval_entries_source_insert
            BEFORE INSERT ON memoir_retrieval_entries
            FOR EACH ROW EXECUTE FUNCTION pawtale_validate_memoir_retrieval_entry()
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER memoir_retrieval_entries_source_update
            BEFORE UPDATE OF project_id, voice_session_id, utterance_id,
                             transcript_version_id, audio_file_id
            ON memoir_retrieval_entries
            FOR EACH ROW EXECUTE FUNCTION pawtale_validate_memoir_retrieval_entry()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS memoir_retrieval_entries_source_update "
            "ON memoir_retrieval_entries"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS memoir_retrieval_entries_source_insert "
            "ON memoir_retrieval_entries"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS pawtale_validate_memoir_retrieval_entry"))
    op.drop_table("memoir_retrieval_entries")

    op.drop_constraint(
        "ck_memoir_editing_jobs_type",
        "memoir_editing_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_memoir_editing_jobs_type",
        "memoir_editing_jobs",
        _enum_check("job_type", OLD_EDITING_JOB_TYPES),
    )
