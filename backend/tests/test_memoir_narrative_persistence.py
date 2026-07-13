from hashlib import sha256

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.database import Base, create_database_engine, register_models
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
    assert_memoir_narrative_schema,
    migrate_memoir_narrative_schema,
)
from app.models.memoir import MemoirImageAsset, MemoirTranscriptVersion, MemoirVoiceSession
from app.models.memoir_narrative import MemoirClaimEvidence
from app.models.projects import Project
from app.models.users import User
from app.repositories.memoir_narrative import MemoirClaimRepository, MemoirNarrativeEntityRepository
from app.schemas.memoir_narrative import (
    MemoirClaimCreate,
    MemoirClaimEvidenceCreate,
    MemoirNarrativeEntityCreate,
)


def _sqlite_engine():
    return create_database_engine(
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            sqlite_journal_mode="memory",
        )
    )


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

        assert first == {"changed_tables": list(MEMOIR_NARRATIVE_TABLES)}
        assert second == {"changed_tables": []}
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

            with pytest.raises(IntegrityError, match="FOREIGN KEY"):
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
