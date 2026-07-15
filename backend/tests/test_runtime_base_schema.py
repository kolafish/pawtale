import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, register_models
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.migrations.runtime_base import (
    OBSOLETE_RUNTIME_TABLES,
    assert_base_runtime_schema,
    migrate_base_runtime_schema,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
)
from app.models.projects import Project
from app.models.users import User


async def test_exact_base_migration_drops_obsolete_schema_without_compatibility():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await migrate_memoir_voice_schema(engine)
        async with session_factory() as db:
            user = User(
                id="schema-user",
                email="schema@example.com",
                password_hash="hash",
                display_name="Schema User",
            )
            project = Project(
                id="schema-project",
                user_id=user.id,
                type="memoir",
                title="Schema Project",
            )
            voice_session = MemoirVoiceSession(
                id="schema-voice-session",
                project_id=project.id,
            )
            transcript = MemoirTranscriptVersion(
                id="schema-transcript",
                voice_session_id=voice_session.id,
                version_type="final",
                content="current transcript",
                created_by="system",
            )
            message = MemoirChatMessage(
                id="schema-message",
                project_id=project.id,
                voice_session_id=voice_session.id,
                transcript_version_id=transcript.id,
                role="user",
                content_text="current transcript",
                content_status="final",
                turn_index=0,
            )
            db.add_all([user, project, voice_session, transcript, message])
            await db.commit()

        async with engine.begin() as conn:
            for table_name in OBSOLETE_RUNTIME_TABLES:
                await conn.execute(
                    text(f'CREATE TABLE "{table_name}" (id TEXT PRIMARY KEY)')
                )
                await conn.execute(
                    text(f'INSERT INTO "{table_name}" (id) VALUES (\'obsolete\')')
                )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_voice_sessions "
                    "ADD COLUMN memoir_session_id VARCHAR(36)"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_transcript_versions "
                    "ADD COLUMN answer_id VARCHAR(36)"
                )
            )
            await conn.execute(
                text(
                    "UPDATE memoir_voice_sessions "
                    "SET memoir_session_id = 'obsolete-session'"
                )
            )
            await conn.execute(
                text(
                    "UPDATE memoir_transcript_versions "
                    "SET answer_id = 'obsolete-answer'"
                )
            )

        with pytest.raises(RuntimeError, match="unexpected tables"):
            await assert_base_runtime_schema(engine)

        result = await migrate_base_runtime_schema(engine)

        assert result == {
            "dropped_tables": list(OBSOLETE_RUNTIME_TABLES),
            "rebuilt_tables": [
                "memoir_voice_sessions",
                "memoir_transcript_versions",
            ],
        }
        await assert_base_runtime_schema(engine)
        async with engine.begin() as conn:
            tables = {
                str(name)
                for name in (
                    await conn.execute(
                        text(
                            "SELECT name FROM sqlite_master "
                            "WHERE type = 'table'"
                        )
                    )
                ).scalars()
            }
            voice_columns = {
                row[1]
                for row in (
                    await conn.execute(
                        text("PRAGMA table_info(memoir_voice_sessions)")
                    )
                ).fetchall()
            }
            transcript_columns = {
                row[1]
                for row in (
                    await conn.execute(
                        text("PRAGMA table_info(memoir_transcript_versions)")
                    )
                ).fetchall()
            }
            violations = (
                await conn.execute(text("PRAGMA foreign_key_check"))
            ).fetchall()
        assert not set(OBSOLETE_RUNTIME_TABLES) & tables
        assert "memoir_session_id" not in voice_columns
        assert "answer_id" not in transcript_columns
        assert violations == []
        async with session_factory() as db:
            assert await db.scalar(
                select(MemoirVoiceSession.id).where(
                    MemoirVoiceSession.id == "schema-voice-session"
                )
            ) == "schema-voice-session"
            assert await db.scalar(
                select(MemoirTranscriptVersion.content).where(
                    MemoirTranscriptVersion.id == "schema-transcript"
                )
            ) == "current transcript"
            assert await db.scalar(
                select(MemoirChatMessage.transcript_version_id).where(
                    MemoirChatMessage.id == "schema-message"
                )
            ) == "schema-transcript"
    finally:
        await engine.dispose()


async def test_exact_base_migration_is_idempotent_for_current_schema():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await migrate_memoir_voice_schema(engine)

        assert await migrate_base_runtime_schema(engine) == {
            "dropped_tables": [],
            "rebuilt_tables": [],
        }
        await assert_base_runtime_schema(engine)
    finally:
        await engine.dispose()
