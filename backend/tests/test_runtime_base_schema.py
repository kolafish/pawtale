import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, register_models
from app.migrations.memoir_narrative import migrate_memoir_narrative_schema
from app.migrations.memoir_voice import migrate_memoir_voice_schema
from app.migrations.runtime_base import (
    LEGACY_DRAFT_BLOCK_TABLES,
    OBSOLETE_RUNTIME_TABLES,
    assert_base_runtime_schema,
    migrate_base_runtime_schema,
)
from app.models.memoir import (
    MemoirBookImagePlacement,
    MemoirChatMessage,
    MemoirImageAsset,
    MemoirMaterialShareVideo,
    MemoirShareVideoOutput,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_common import AudioFile
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
                    "ALTER TABLE memoir_draft_blocks "
                    "ADD COLUMN legacy_draft_id VARCHAR(36)"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_draft_block_versions "
                    "ADD COLUMN legacy_draft_version_id VARCHAR(36)"
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
            "reset_tables": list(LEGACY_DRAFT_BLOCK_TABLES),
        }
        await migrate_memoir_narrative_schema(engine)
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
            "reset_tables": [],
        }
        await assert_base_runtime_schema(engine)
    finally:
        await engine.dispose()


async def test_base_migration_splits_media_output_and_derives_image_inclusion():
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
        async with session_factory() as db:
            user = User(
                id="media-user",
                email="media@example.com",
                password_hash="hash",
                display_name="Media User",
            )
            project = Project(
                id="media-project",
                user_id=user.id,
                type="memoir",
                title="Media Project",
            )
            voice_session = MemoirVoiceSession(
                id="media-session",
                project_id=project.id,
            )
            audio = AudioFile(
                id="media-audio",
                user_id=user.id,
                storage_path="memoir/audio/source.wav",
                mime_type="audio/wav",
                size_bytes=128,
            )
            utterance = MemoirVoiceUtterance(
                id="media-utterance",
                voice_session_id=voice_session.id,
                audio_file_id=audio.id,
                transcript_raw="旧院子里的葡萄树",
            )
            job = MemoirMaterialShareVideo(
                id="media-job",
                project_id=project.id,
                utterance_id=utterance.id,
                audio_file_id=audio.id,
                user_id=user.id,
                status="succeeded",
                text_source="raw",
                text_hash="hash",
                selected_text="旧院子里的葡萄树",
                start_char=0,
                end_char=9,
                start_ms=0,
                end_ms=4000,
            )
            asset = MemoirImageAsset(
                id="media-image",
                project_id=project.id,
                user_id=user.id,
                status="ready",
                source_type="manual_upload",
                storage_path="memoir/images/photo.jpg",
                mime_type="image/jpeg",
            )
            placement = MemoirBookImagePlacement(
                id="media-placement",
                project_id=project.id,
                image_asset_id=asset.id,
                placement_type="appendix",
                enabled=True,
            )
            db.add_all(
                [user, project, voice_session, audio, utterance, job, asset, placement]
            )
            await db.commit()

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE memoir_material_share_videos "
                    "ADD COLUMN video_storage_path VARCHAR(500)"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_material_share_videos "
                    "ADD COLUMN video_mime_type VARCHAR(100) NOT NULL DEFAULT 'video/mp4'"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_material_share_videos "
                    "ADD COLUMN video_size_bytes INTEGER"
                )
            )
            await conn.execute(
                text(
                    "UPDATE memoir_material_share_videos SET "
                    "video_storage_path = 'memoir/share-videos/media-user/media-job.mp4', "
                    "video_size_bytes = 4096 WHERE id = 'media-job'"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_image_assets "
                    "ADD COLUMN include_in_book BOOLEAN NOT NULL DEFAULT 0"
                )
            )
            await conn.execute(
                text(
                    "UPDATE memoir_image_assets SET include_in_book = 1 "
                    "WHERE id = 'media-image'"
                )
            )

        result = await migrate_base_runtime_schema(engine)

        assert result["rebuilt_tables"] == [
            "memoir_material_share_videos",
            "memoir_image_assets",
        ]
        async with session_factory() as db:
            output = await db.scalar(
                select(MemoirShareVideoOutput).where(
                    MemoirShareVideoOutput.job_id == "media-job"
                )
            )
            assert output is not None
            assert output.project_id == "media-project"
            assert output.storage_path == "memoir/share-videos/media-user/media-job.mp4"
            assert output.mime_type == "video/mp4"
            assert output.size_bytes == 4096
            assert await db.scalar(
                select(MemoirBookImagePlacement.enabled).where(
                    MemoirBookImagePlacement.id == "media-placement"
                )
            ) is True
        async with engine.begin() as conn:
            job_columns = {
                row[1]
                for row in (
                    await conn.execute(
                        text("PRAGMA table_info(memoir_material_share_videos)")
                    )
                ).fetchall()
            }
            image_columns = {
                row[1]
                for row in (
                    await conn.execute(text("PRAGMA table_info(memoir_image_assets)"))
                ).fetchall()
            }
            violations = (
                await conn.execute(text("PRAGMA foreign_key_check"))
            ).fetchall()
        assert not {
            "video_storage_path",
            "video_mime_type",
            "video_size_bytes",
        } & job_columns
        assert "include_in_book" not in image_columns
        assert violations == []
        await assert_base_runtime_schema(engine)
    finally:
        await engine.dispose()


async def test_legacy_draft_block_reset_rejects_current_node_based_rows():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "ALTER TABLE memoir_draft_blocks "
                    "ADD COLUMN legacy_draft_id VARCHAR(36)"
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO memoir_draft_blocks (
                        id, project_id, chapter_id, node_id, block_role, sort_order,
                        editorial_status, freshness_status, locked, current_version_id,
                        legacy_draft_id, created_at, updated_at
                    ) VALUES (
                        'current-block', 'project', 'chapter', 'node', 'transition', 0,
                        'draft', 'current', 0, NULL, NULL,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        with pytest.raises(RuntimeError, match="found 1 current node-based rows"):
            await migrate_base_runtime_schema(engine)

        async with engine.begin() as conn:
            remaining = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM memoir_draft_blocks "
                        "WHERE id = 'current-block'"
                    )
                )
            ).scalar_one()
        assert remaining == 1
    finally:
        await engine.dispose()
