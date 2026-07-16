from hashlib import sha256

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, register_models
from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeEntityType,
)
from app.domain.memoir.types import (
    MemoirCreatedBy,
    MemoirImageAssetSourceType,
    MemoirTranscriptVersionType,
)
from app.migrations.memoir_narrative import (
    MEMOIR_NARRATIVE_TABLES,
    MEMOIR_NARRATIVE_TRIGGERS,
    assert_memoir_narrative_schema,
    migrate_memoir_narrative_schema,
)
from app.models.memoir import MemoirImageAsset, MemoirTranscriptVersion, MemoirVoiceSession
from app.models.memoir_narrative import MemoirClaimEvidence, MemoirStory, MemoirStorySlot
from app.models.projects import Project
from app.models.users import User
from app.repositories.memoir_narrative import MemoirClaimRepository, MemoirNarrativeEntityRepository
from app.schemas.memoir_narrative import (
    MemoirClaimCreate,
    MemoirClaimEvidenceCreate,
    MemoirNarrativeEntityCreate,
)


def _sqlite_engine():
    return create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)


async def test_narrative_migration_is_idempotent_and_runtime_asserts_schema():
    register_models()
    engine = _sqlite_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for table_name in reversed(MEMOIR_NARRATIVE_TABLES):
                await conn.execute(text(f"DROP TABLE {table_name}"))

        first = await migrate_memoir_narrative_schema(engine)
        second = await migrate_memoir_narrative_schema(engine)
        await assert_memoir_narrative_schema(engine)

        assert first == {
            "changed_tables": list(MEMOIR_NARRATIVE_TABLES),
            "changed_columns": [],
            "changed_indexes": [],
            "changed_triggers": list(MEMOIR_NARRATIVE_TRIGGERS),
        }
        assert second == {
            "changed_tables": [],
            "changed_columns": [],
            "changed_indexes": [],
            "changed_triggers": [],
        }
    finally:
        await engine.dispose()


async def test_narrative_migration_removes_only_derived_empty_story_slots():
    register_models()
    engine = _sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as db:
            project = Project(type="memoir", title="稀疏槽位测试")
            db.add(project)
            await db.flush()
            story = MemoirStory(
                project_id=project.id,
                title="一次经历",
                title_normalized="一次经历",
                story_type="event",
                summary="",
                time_precision="unknown",
                lifecycle_status="active",
                completion_status="incomplete",
                verification_status="unreviewed",
                publication_status="eligible",
                slot_schema_version="1",
                created_by="system",
                updated_by="system",
            )
            db.add(story)
            await db.flush()
            db.add_all(
                [
                    MemoirStorySlot(
                        project_id=project.id,
                        story_id=story.id,
                        schema_version="1",
                        slot_key="trigger",
                        status="missing",
                    ),
                    MemoirStorySlot(
                        project_id=project.id,
                        story_id=story.id,
                        schema_version="1",
                        slot_key="background",
                        status="partial",
                        assessment_summary="已有一条未确认信息",
                    ),
                ]
            )
            await db.commit()

        await migrate_memoir_narrative_schema(engine)

        async with session_factory() as db:
            remaining = list(
                (
                    await db.scalars(
                        select(MemoirStorySlot).where(
                            MemoirStorySlot.project_id == project.id,
                            MemoirStorySlot.story_id == story.id,
                        )
                    )
                ).all()
            )
            assert [(slot.slot_key, slot.status) for slot in remaining] == [
                ("background", "partial")
            ]
    finally:
        await engine.dispose()


async def test_narrative_migration_upgrades_pr2_tables_in_place():
    register_models()
    engine = _sqlite_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("DROP TABLE memoir_narrative_decisions"))
            await conn.execute(text("DROP INDEX uq_memoir_claim_evidence_transcript_quote"))

        changed = await migrate_memoir_narrative_schema(engine)
        await assert_memoir_narrative_schema(engine)

        assert changed == {
            "changed_tables": ["memoir_narrative_decisions"],
            "changed_columns": [],
            "changed_indexes": [
                "uq_memoir_claim_evidence_transcript_quote",
            ],
            "changed_triggers": list(MEMOIR_NARRATIVE_TRIGGERS),
        }
    finally:
        await engine.dispose()


async def test_narrative_migration_adds_structured_block_snapshot_column_once():
    register_models()
    engine = _sqlite_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_chapter_draft_versions "
                    "DROP COLUMN block_snapshot_json"
                )
            )

        first = await migrate_memoir_narrative_schema(engine)
        second = await migrate_memoir_narrative_schema(engine)
        await assert_memoir_narrative_schema(engine)

        assert first["changed_columns"] == [
            "memoir_book_chapter_draft_versions.block_snapshot_json"
        ]
        assert second["changed_columns"] == []
        async with engine.begin() as conn:
            columns = {
                row[1]
                for row in (
                    await conn.execute(
                        text("PRAGMA table_info(memoir_book_chapter_draft_versions)")
                    )
                ).fetchall()
            }
        assert "block_snapshot_json" in columns
    finally:
        await engine.dispose()


async def test_narrative_migration_adds_book_chapter_inclusion_column_once():
    register_models()
    engine = _sqlite_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_plan_chapters "
                    "DROP COLUMN include_in_book"
                )
            )

        first = await migrate_memoir_narrative_schema(engine)
        second = await migrate_memoir_narrative_schema(engine)
        await assert_memoir_narrative_schema(engine)

        assert first["changed_columns"] == [
            "memoir_book_plan_chapters.include_in_book"
        ]
        assert second["changed_columns"] == []
        async with engine.begin() as conn:
            column = next(
                row
                for row in (
                    await conn.execute(
                        text("PRAGMA table_info(memoir_book_plan_chapters)")
                    )
                ).fetchall()
                if row[1] == "include_in_book"
            )
        assert column[3] == 1
        assert column[4] == "1"
    finally:
        await engine.dispose()


async def test_narrative_migration_adds_followup_assignment_snapshot_column_once():
    register_models()
    engine = _sqlite_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "ALTER TABLE memoir_followup_task_utterances "
                    "DROP COLUMN is_assignment_snapshot"
                )
            )

        first = await migrate_memoir_narrative_schema(engine)
        second = await migrate_memoir_narrative_schema(engine)
        await assert_memoir_narrative_schema(engine)

        assert first["changed_columns"] == [
            "memoir_followup_task_utterances.is_assignment_snapshot"
        ]
        assert second["changed_columns"] == []
    finally:
        await engine.dispose()


async def test_narrative_migration_adds_followup_answer_interpretation_columns_once():
    register_models()
    engine = _sqlite_engine()
    columns = [
        "interpretation_status",
        "answer_kind",
        "direct_narrative_text",
        "clarification_text",
        "fact_resolution_text",
        "confirmation_question",
        "interpretation_reason",
    ]
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("DROP TABLE memoir_followup_task_utterances"))
            await conn.execute(
                text(
                    "CREATE TABLE memoir_followup_task_utterances ("
                    "id VARCHAR(36) PRIMARY KEY NOT NULL, "
                    "project_id VARCHAR(36) NOT NULL, "
                    "task_id VARCHAR(36) NOT NULL, "
                    "utterance_id VARCHAR(36) NOT NULL, "
                    "result_role VARCHAR(24) NOT NULL, "
                    "is_assignment_snapshot BOOLEAN NOT NULL DEFAULT 0, "
                    "created_at DATETIME NOT NULL)"
                )
            )

        first = await migrate_memoir_narrative_schema(engine)
        second = await migrate_memoir_narrative_schema(engine)

        assert first["changed_columns"] == [
            f"memoir_followup_task_utterances.{column}" for column in columns
        ]
        assert second["changed_columns"] == []
        async with engine.begin() as conn:
            actual_columns = {
                row[1]
                for row in (
                    await conn.execute(
                        text("PRAGMA table_info(memoir_followup_task_utterances)")
                    )
                ).fetchall()
            }
        assert set(columns) <= actual_columns
    finally:
        await engine.dispose()


async def test_narrative_migration_backfills_followup_answer_use_from_gap_type():
    register_models()
    engine = _sqlite_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, password_hash, role, display_name, created_at, updated_at) "
                    "VALUES ('answer-use-user', 'answer-use@example.com', 'hash', 'user', "
                    "'Answer Use User', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO projects "
                    "(id, user_id, type, title, metadata_json, created_at, updated_at) "
                    "VALUES ('answer-use-project', 'answer-use-user', 'memoir', "
                    "'Answer Use Project', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text("ALTER TABLE memoir_followup_tasks DROP COLUMN answer_use")
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_narrative_gaps "
                    "(id, project_id, scope_type, primary_story_id, primary_chapter_id, "
                    "gap_type, severity, target_slot, title, description, fingerprint, "
                    "status, detected_by, detection_count, resolution_note, resolved_by, "
                    "resolved_by_user_id, resolved_at, first_detected_at, last_detected_at, "
                    "created_at, updated_at) VALUES "
                    "('answer-use-gap', 'answer-use-project', 'book', NULL, NULL, "
                    "'causality', 'medium', '', '章节关系不清楚', '', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "'open', 'ai', 1, '', NULL, NULL, NULL, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_followup_tasks "
                    "(id, project_id, gap_id, chapter_id, story_id, target_slot, question, "
                    "purpose, known_context_summary, priority, status, assigned_session_id, "
                    "defer_until, defer_reason, created_by, started_at, completed_at, "
                    "created_at, updated_at) VALUES "
                    "('answer-use-task', 'answer-use-project', 'answer-use-gap', NULL, NULL, "
                    "'', '这件事和本章有什么关系？', '确认章节关系', '', 10, 'pending', "
                    "NULL, NULL, '', 'system', NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        first = await migrate_memoir_narrative_schema(engine)
        second = await migrate_memoir_narrative_schema(engine)
        await assert_memoir_narrative_schema(engine)

        assert first["changed_columns"] == ["memoir_followup_tasks.answer_use"]
        assert second["changed_columns"] == []
        async with engine.begin() as conn:
            answer_use = (
                await conn.execute(
                    text(
                        "SELECT answer_use FROM memoir_followup_tasks "
                        "WHERE id = 'answer-use-task'"
                    )
                )
            ).scalar_one()
        assert answer_use == "clarification"
    finally:
        await engine.dispose()


async def test_narrative_migration_backfills_semantic_followup_target_slot_once():
    register_models()
    engine = _sqlite_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, password_hash, role, display_name, created_at, updated_at) "
                    "VALUES ('semantic-user', 'semantic@example.com', 'hash', 'user', "
                    "'Semantic User', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO projects "
                    "(id, user_id, type, title, metadata_json, created_at, updated_at) "
                    "VALUES ('semantic-project', 'semantic-user', 'memoir', "
                    "'Semantic Project', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_narrative_gaps "
                    "(id, project_id, scope_type, primary_story_id, primary_chapter_id, "
                    "gap_type, severity, target_slot, title, description, fingerprint, "
                    "status, detected_by, detection_count, resolution_note, resolved_by, "
                    "resolved_by_user_id, resolved_at, first_detected_at, last_detected_at, "
                    "created_at, updated_at) VALUES "
                    "('semantic-gap', 'semantic-project', 'book', NULL, NULL, "
                    "'relationship', 'medium', 'semantic:opening:semantic-gap', "
                    "'章节开头还可以更清楚', '', "
                    "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                    "'open', 'ai', 1, '', NULL, NULL, NULL, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_followup_tasks "
                    "(id, project_id, gap_id, chapter_id, story_id, target_slot, answer_use, "
                    "question, purpose, known_context_summary, priority, status, "
                    "assigned_session_id, defer_until, defer_reason, created_by, started_at, "
                    "completed_at, created_at, updated_at) VALUES "
                    "('semantic-task', 'semantic-project', 'semantic-gap', NULL, NULL, '', "
                    "'direct_narrative', '这一章从哪一幕开始？', '补充章节开头', '', 10, "
                    "'pending', NULL, NULL, '', 'system', NULL, NULL, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )

        await migrate_memoir_narrative_schema(engine)
        await migrate_memoir_narrative_schema(engine)

        async with engine.begin() as conn:
            target_slot = (
                await conn.execute(
                    text(
                        "SELECT target_slot FROM memoir_followup_tasks "
                        "WHERE id = 'semantic-task'"
                    )
                )
            ).scalar_one()
        assert target_slot == "semantic:opening:semantic-gap"
    finally:
        await engine.dispose()


async def test_narrative_migration_rebuilds_legacy_decisions_and_preserves_rows():
    register_models()
    engine = _sqlite_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, password_hash, role, display_name, created_at, updated_at) "
                    "VALUES ('migration-user', 'migration@example.com', 'hash', 'user', "
                    "'Migration User', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO projects "
                    "(id, user_id, type, title, metadata_json, created_at, updated_at) "
                    "VALUES ('migration-project', 'migration-user', 'memoir', "
                    "'Migration Project', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(text("DROP TABLE memoir_narrative_decisions"))
            await conn.execute(
                text(
                    "CREATE TABLE memoir_narrative_decisions ("
                    "id VARCHAR(36) PRIMARY KEY NOT NULL, "
                    "project_id VARCHAR(36) NOT NULL, "
                    "target_type VARCHAR(32) NOT NULL, "
                    "target_id VARCHAR(36) NOT NULL, "
                    "action VARCHAR(32) NOT NULL, "
                    "actor_type VARCHAR(12) NOT NULL, "
                    "actor_user_id VARCHAR(36), "
                    "before_state_json JSON NOT NULL, "
                    "after_state_json JSON NOT NULL, "
                    "reason TEXT NOT NULL DEFAULT '', "
                    "idempotency_key VARCHAR(200) NOT NULL, "
                    "created_at DATETIME NOT NULL, "
                    "CONSTRAINT uq_memoir_narrative_decisions_project_idempotency "
                    "UNIQUE (project_id, idempotency_key))"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_narrative_decisions "
                    "(id, project_id, target_type, target_id, action, actor_type, "
                    "actor_user_id, before_state_json, after_state_json, reason, "
                    "idempotency_key, created_at) VALUES "
                    "('legacy-decision', 'migration-project', 'story', 'story-1', "
                    "'confirm', 'user', 'migration-user', '{}', '{}', "
                    "'preserve me', 'legacy-confirm', CURRENT_TIMESTAMP)"
                )
            )

        changed = await migrate_memoir_narrative_schema(engine)
        await assert_memoir_narrative_schema(engine)
        assert changed["changed_tables"] == ["memoir_narrative_decisions"]
        async with engine.begin() as conn:
            upgraded_sql = str(
                (
                    await conn.execute(
                        text(
                            "SELECT sql FROM sqlite_master "
                            "WHERE type = 'table' AND name = 'memoir_narrative_decisions'"
                        )
                    )
                ).scalar_one()
            )
            preserved = (
                await conn.execute(
                    text(
                        "SELECT action, reason, source_followup_task_utterance_id, "
                        "source_transcript_version_id FROM memoir_narrative_decisions "
                        "WHERE id = 'legacy-decision'"
                    )
                )
            ).one()
        assert "'include'" in upgraded_sql
        assert "fk_memoir_narrative_decisions_followup_source_project" in upgraded_sql
        assert "fk_memoir_narrative_decisions_transcript_source" in upgraded_sql
        assert preserved == ("confirm", "preserve me", None, None)
    finally:
        await engine.dispose()


async def test_narrative_repositories_enforce_project_scoping_and_source_shapes():
    register_models()
    engine = _sqlite_engine()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            user = User(
                email="narrative@example.com",
                password_hash="test",
                display_name="Narrative Test",
            )
            first_project = Project(user_id=user.id, type="memoir", title="First memoir")
            second_project = Project(user_id=user.id, type="memoir", title="Second memoir")
            db.add_all([user, first_project, second_project])
            await db.flush()

            entity_repository = MemoirNarrativeEntityRepository(db)
            claim_repository = MemoirClaimRepository(db)
            narrator = await entity_repository.create(
                MemoirNarrativeEntityCreate(
                    project_id=first_project.id,
                    entity_type=MemoirNarrativeEntityType.PERSON,
                    canonical_name="讲述者",
                    aliases=["我", "讲述者", "我"],
                    created_by=MemoirNarrativeActorType.USER,
                    created_by_user_id=user.id,
                )
            )
            other_project_speaker = await entity_repository.create(
                MemoirNarrativeEntityCreate(
                    project_id=second_project.id,
                    entity_type=MemoirNarrativeEntityType.PERSON,
                    canonical_name="另一个项目的讲述者",
                    created_by=MemoirNarrativeActorType.SYSTEM,
                )
            )
            claim = await claim_repository.create(
                MemoirClaimCreate(
                    project_id=first_project.id,
                    claim_type=MemoirClaimType.EVENT,
                    statement="讲述者在七二年前后搬到了成都。",
                    subject_entity_id=narrator.id,
                    predicate_code=MemoirClaimPredicate.NARRATIVE_FACT,
                    time_text="七二年前后",
                    created_by=MemoirNarrativeActorType.AI,
                )
            )

            voice_session = MemoirVoiceSession(project_id=first_project.id)
            db.add(voice_session)
            await db.flush()
            transcript = MemoirTranscriptVersion(
                voice_session_id=voice_session.id,
                version_type=MemoirTranscriptVersionType.FINAL.value,
                content="七二年前后，我搬到了成都。",
                created_by=MemoirCreatedBy.USER.value,
            )
            image = MemoirImageAsset(
                project_id=first_project.id,
                user_id=user.id,
                source_type=MemoirImageAssetSourceType.MANUAL_UPLOAD.value,
                source_signature="original-photo",
                storage_path="memoir/original-photo.jpg",
                mime_type="image/jpeg",
                provider="user_upload",
            )
            generated_image = MemoirImageAsset(
                project_id=first_project.id,
                user_id=user.id,
                source_type=MemoirImageAssetSourceType.CHAPTER_ILLUSTRATION.value,
                source_signature="generated-image",
                storage_path="memoir/generated-image.jpg",
                mime_type="image/jpeg",
                provider="image-model",
            )
            other_voice_session = MemoirVoiceSession(project_id=second_project.id)
            db.add_all([transcript, image, generated_image, other_voice_session])
            await db.flush()
            other_transcript = MemoirTranscriptVersion(
                voice_session_id=other_voice_session.id,
                version_type=MemoirTranscriptVersionType.FINAL.value,
                content="这是另一个项目的转写。",
                created_by=MemoirCreatedBy.USER.value,
            )
            db.add(other_transcript)
            await db.flush()

            transcript_hash = sha256(transcript.content.encode("utf-8")).hexdigest()
            await claim_repository.add_evidence(
                MemoirClaimEvidenceCreate(
                    project_id=first_project.id,
                    claim_id=claim.id,
                    evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
                    evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT,
                    transcript_version_id=transcript.id,
                    speaker_entity_id=narrator.id,
                    quote="七二年前后，我搬到了成都。",
                    source_text_hash=transcript_hash,
                    created_by=MemoirNarrativeActorType.AI,
                )
            )
            with pytest.raises(ValueError, match="quote is not present"):
                await claim_repository.add_evidence(
                    MemoirClaimEvidenceCreate(
                        project_id=first_project.id,
                        claim_id=claim.id,
                        evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
                        evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT,
                        transcript_version_id=transcript.id,
                        quote="原文里不存在的句子",
                        source_text_hash=transcript_hash,
                        created_by=MemoirNarrativeActorType.AI,
                    )
                )
            with pytest.raises(ValueError, match="source hash does not match"):
                await claim_repository.add_evidence(
                    MemoirClaimEvidenceCreate(
                        project_id=first_project.id,
                        claim_id=claim.id,
                        evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
                        evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT,
                        transcript_version_id=transcript.id,
                        quote=transcript.content,
                        source_text_hash=sha256(b"stale transcript").hexdigest(),
                        created_by=MemoirNarrativeActorType.AI,
                    )
                )
            await claim_repository.add_evidence(
                MemoirClaimEvidenceCreate(
                    project_id=first_project.id,
                    claim_id=claim.id,
                    evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
                    evidence_source_type=MemoirClaimEvidenceSourceType.IMAGE,
                    image_asset_id=image.id,
                    evidence_note="照片背面写有一九七二年成都。",
                    created_by=MemoirNarrativeActorType.USER,
                    created_by_user_id=user.id,
                )
            )
            await claim_repository.add_evidence(
                MemoirClaimEvidenceCreate(
                    project_id=first_project.id,
                    claim_id=claim.id,
                    evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
                    evidence_source_type=MemoirClaimEvidenceSourceType.MANUAL,
                    evidence_note="讲述者本人确认搬迁时间。",
                    created_by=MemoirNarrativeActorType.USER,
                    created_by_user_id=user.id,
                )
            )
            with pytest.raises(ValueError, match="another project"):
                await claim_repository.add_evidence(
                    MemoirClaimEvidenceCreate(
                        project_id=first_project.id,
                        claim_id=claim.id,
                        evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
                        evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT,
                        transcript_version_id=other_transcript.id,
                        quote=other_transcript.content,
                        source_text_hash=sha256(other_transcript.content.encode("utf-8")).hexdigest(),
                        created_by=MemoirNarrativeActorType.AI,
                    )
                )
            with pytest.raises(ValueError, match="speaker entity"):
                await claim_repository.add_evidence(
                    MemoirClaimEvidenceCreate(
                        project_id=first_project.id,
                        claim_id=claim.id,
                        evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
                        evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT,
                        transcript_version_id=transcript.id,
                        speaker_entity_id=other_project_speaker.id,
                        quote=transcript.content,
                        source_text_hash=transcript_hash,
                        created_by=MemoirNarrativeActorType.AI,
                    )
                )
            with pytest.raises(ValueError, match="generated images"):
                await claim_repository.add_evidence(
                    MemoirClaimEvidenceCreate(
                        project_id=first_project.id,
                        claim_id=claim.id,
                        evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
                        evidence_source_type=MemoirClaimEvidenceSourceType.IMAGE,
                        image_asset_id=generated_image.id,
                        evidence_note="AI 生成图不应成为事实证据。",
                        created_by=MemoirNarrativeActorType.AI,
                    )
                )
            await db.commit()

            loaded = await claim_repository.get(
                project_id=first_project.id,
                claim_id=claim.id,
                include_evidence=True,
            )
            assert loaded is not None
            assert len(loaded.evidence) == 3
            assert await claim_repository.get(project_id=second_project.id, claim_id=claim.id) is None
            assert await entity_repository.get(project_id=second_project.id, entity_id=narrator.id) is None

        async with session_factory() as db:
            invalid = MemoirClaimEvidence(
                project_id=first_project.id,
                claim_id=claim.id,
                evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
                evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT.value,
                quote="缺少明确的 transcript version。",
                source_text_hash=sha256(b"invalid").hexdigest(),
                created_by=MemoirNarrativeActorType.AI.value,
            )
            db.add(invalid)
            with pytest.raises(IntegrityError, match="source_shape"):
                await db.flush()
    finally:
        await engine.dispose()


async def test_claim_entity_foreign_key_rejects_cross_project_relation():
    register_models()
    engine = _sqlite_engine()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            user = User(email="isolation@example.com", password_hash="test", display_name="Isolation")
            source_project = Project(user_id=user.id, type="memoir", title="Source")
            target_project = Project(user_id=user.id, type="memoir", title="Target")
            db.add_all([user, source_project, target_project])
            await db.flush()
            entity = await MemoirNarrativeEntityRepository(db).create(
                MemoirNarrativeEntityCreate(
                    project_id=source_project.id,
                    entity_type=MemoirNarrativeEntityType.PERSON,
                    canonical_name="跨项目人物",
                    created_by=MemoirNarrativeActorType.SYSTEM,
                )
            )

            with pytest.raises(ValueError, match="entity reference"):
                await MemoirClaimRepository(db).create(
                    MemoirClaimCreate(
                        project_id=target_project.id,
                        claim_type=MemoirClaimType.EVENT,
                        statement="不应允许引用另一个项目的人物。",
                        subject_entity_id=entity.id,
                        created_by=MemoirNarrativeActorType.SYSTEM,
                    )
                )
    finally:
        await engine.dispose()


def test_ai_cannot_create_user_decision_states():
    with pytest.raises(ValidationError, match="explicit user decision"):
        MemoirClaimCreate(
            project_id="project-id",
            claim_type=MemoirClaimType.EVENT,
            statement="AI 不能把抽取结果直接标记为用户已确认。",
            verification_status=MemoirClaimVerificationStatus.CONFIRMED,
            created_by=MemoirNarrativeActorType.AI,
        )

    with pytest.raises(ValidationError, match="explicit user decision"):
        MemoirClaimCreate(
            project_id="project-id",
            claim_type=MemoirClaimType.EVENT,
            statement="AI 不能替用户决定隐藏事实。",
            publication_status=MemoirClaimPublicationStatus.PRIVATE,
            created_by=MemoirNarrativeActorType.AI,
        )
