from __future__ import annotations

from hashlib import sha256
import json

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
    MemoirNarrativeEntityType,
)
from app.domain.memoir.types import (
    MemoirCreatedBy,
    MemoirChatMessageRole,
    MemoirEditingJobType,
    MemoirTranscriptVersionType,
)
from app.models.memoir import MemoirEditingJob, MemoirTranscriptVersion, MemoirVoiceUtterance
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirNarrativeDecision,
    MemoirNarrativeEntity,
)
from app.models.projects import Project
from app.repositories.memoir_narrative import MemoirClaimRepository, MemoirNarrativeEntityRepository
from app.schemas.memoir_narrative import (
    MemoirClaimCreate,
    MemoirClaimEvidenceCreate,
    MemoirNarrativeEntityCreate,
)
from app.services.memoir.narrative.backfill import run_narrative_ingestion_backfill
from app.services.memoir.narrative.ingestion import ingest_transcript_version
from app.services.memoir.narrative.ingestion_jobs import (
    run_narrative_ingestion_job,
    schedule_narrative_ingestion_job,
)
from app.services.memoir.voice_transcripts import prune_transcript_versions_for_utterance
from tests.memoir_helpers import create_test_voice_transcript


class FakeNarrativeAI:
    provider_name = "fake"
    model_name = "fake-narrative"

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls = 0

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 60.0,
        return_raw: bool = False,
    ) -> str:
        self.calls += 1
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def _extraction_payload(source_text: str) -> dict:
    return {
        "schema_version": "1",
        "entities": [
            {
                "key": "narrator",
                "entity_type": "person",
                "canonical_name": "余爷爷",
                "aliases": ["我"],
                "description": "讲述者",
                "is_narrator": True,
            },
            {
                "key": "grandmother",
                "entity_type": "person",
                "canonical_name": "外婆",
                "aliases": [],
                "description": "原文提到的外婆",
                "is_narrator": False,
            },
            {
                "key": "chengdu",
                "entity_type": "place",
                "canonical_name": "成都",
                "aliases": [],
                "description": "原文提到的地点",
                "is_narrator": False,
            },
        ],
        "claims": [
            {
                "key": "move",
                "claim_type": "event",
                "statement": "余爷爷在七二年前后搬到成都。",
                "subject_entity_key": "narrator",
                "predicate_code": "occurred_at",
                "object_entity_key": None,
                "object_text": None,
                "place_entity_key": "chengdu",
                "time_text": "七二年前后",
                "time_start": "1972-01-01T00:00:00",
                "time_end": "1972-12-31T23:59:59",
                "time_precision": "year",
                "certainty": "approximate",
                "evidence_quotes": ["七二年前后，我搬到了成都"],
            },
            {
                "key": "relationship",
                "claim_type": "relationship",
                "statement": "外婆是余爷爷的外婆。",
                "subject_entity_key": "narrator",
                "predicate_code": "family_relationship",
                "object_entity_key": "grandmother",
                "object_text": None,
                "place_entity_key": None,
                "time_text": None,
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "certainty": "exact",
                "evidence_quotes": ["我和外婆一起生活"],
            },
            {
                "key": "fabricated",
                "claim_type": "event",
                "statement": "院子里有一棵梨树。",
                "subject_entity_key": "narrator",
                "predicate_code": "narrative_fact",
                "object_entity_key": None,
                "object_text": None,
                "place_entity_key": None,
                "time_text": None,
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "certainty": "uncertain",
                "evidence_quotes": ["院子里有一棵梨树"],
            },
        ],
    }


async def _load_source(test_db, created: dict):
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == created["utterance_id"])
        .options(
            selectinload(MemoirVoiceUtterance.chat_message),
            selectinload(MemoirVoiceUtterance.audio_file),
        )
    )
    version = await test_db.get(MemoirTranscriptVersion, created["transcript_version_id"])
    project = await test_db.get(Project, created["session"]["project_id"])
    assert utterance and version and project
    return project, utterance, version


async def test_ingestion_requires_exact_evidence_and_is_idempotent(client, test_db):
    source_text = "七二年前后，我搬到了成都，那时我和外婆一起生活。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    project.metadata_json = {
        "memoir_profile": {"narrator_name": "余爷爷", "narrator_origin": "四川成都"}
    }
    fake_ai = FakeNarrativeAI([
        _extraction_payload(source_text),
        _extraction_payload(source_text),
    ])

    first = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=fake_ai,
    )
    second = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=fake_ai,
    )
    await test_db.commit()

    assert len(first.created_claim_ids) == 2
    assert first.skipped_claim_keys == ("fabricated",)
    assert second.created_claim_ids == ()
    assert len(second.reused_claim_ids) == 2
    assert second.created_evidence_ids == ()
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaim)) == 2
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaimEvidence)) == 2
    relationship = await test_db.scalar(
        select(MemoirClaim).where(MemoirClaim.claim_type == MemoirClaimType.RELATIONSHIP.value)
    )
    assert relationship is not None
    assert relationship.subject_entity_id
    assert relationship.object_entity_id
    move = await test_db.scalar(select(MemoirClaim).where(MemoirClaim.time_text == "七二年前后"))
    assert move is not None
    assert move.time_precision == "year"
    assert move.certainty == "approximate"
    evidence = await test_db.scalar(
        select(MemoirClaimEvidence).where(MemoirClaimEvidence.claim_id == move.id)
    )
    assert evidence is not None
    assert evidence.quote == "七二年前后，我搬到了成都"
    assert evidence.source_text_hash == sha256(source_text.encode("utf-8")).hexdigest()


async def test_invalid_ai_schema_creates_no_narrative_state(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我小时候住在成都。")
    project, utterance, version = await _load_source(test_db, created)
    fake_ai = FakeNarrativeAI([{"schema_version": "1", "claims": "not-a-list"}])

    with pytest.raises(ValueError, match="did not match schema"):
        await ingest_transcript_version(
            test_db,
            project=project,
            utterance=utterance,
            version=version,
            ai_client=fake_ai,
        )
    await test_db.rollback()

    assert await test_db.scalar(select(func.count()).select_from(MemoirClaim)) == 0
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaimEvidence)) == 0
    assert await test_db.scalar(select(func.count()).select_from(MemoirNarrativeEntity)) == 0


async def test_interviewer_fact_preserves_speaker_and_requires_review(client, test_db):
    source_text = "我记得他是在七二年搬到成都的。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, created)
    utterance.chat_message.role = MemoirChatMessageRole.INTERVIEWER.value
    payload = {
        "schema_version": "1",
        "entities": [],
        "claims": [
            {
                "key": "interviewer_fact",
                "claim_type": "event",
                "statement": "采访者记得讲述者在七二年搬到成都。",
                "subject_entity_key": None,
                "predicate_code": "narrative_fact",
                "object_entity_key": None,
                "object_text": None,
                "place_entity_key": None,
                "time_text": "七二年",
                "time_start": None,
                "time_end": None,
                "time_precision": "year",
                "certainty": "uncertain",
                "evidence_quotes": [source_text],
            }
        ],
    }

    result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=FakeNarrativeAI([payload]),
    )
    await test_db.commit()

    claim = await test_db.get(MemoirClaim, result.created_claim_ids[0])
    evidence = await test_db.scalar(
        select(MemoirClaimEvidence).where(MemoirClaimEvidence.claim_id == claim.id)
    )
    assert claim.verification_status == MemoirClaimVerificationStatus.NEEDS_VERIFICATION.value
    assert evidence is not None
    assert evidence.speaker_role == "interviewer"
    assert evidence.speaker_entity_id is None


async def test_transcript_change_marks_unsupported_claim_for_review(client, test_db):
    source_text = "七二年前后，我搬到了成都，那时我和外婆一起生活。"
    created = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, first_version = await _load_source(test_db, created)
    first = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=first_version,
        ai_client=FakeNarrativeAI([_extraction_payload(source_text)]),
    )
    old_claim_id = first.created_claim_ids[0]

    revised_text = "我后来搬到了重庆。"
    revised_version = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.USER_EDITED.value,
        content=revised_text,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(revised_version)
    await test_db.flush()
    utterance.chat_message.transcript_version_id = revised_version.id
    utterance.chat_message.content_text = revised_text
    utterance.transcript_edited = revised_text
    await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=revised_version,
        ai_client=FakeNarrativeAI([{"schema_version": "1", "entities": [], "claims": []}]),
    )
    await test_db.commit()

    old_claim = await test_db.get(MemoirClaim, old_claim_id)
    assert old_claim is not None
    assert old_claim.verification_status == MemoirClaimVerificationStatus.NEEDS_VERIFICATION.value
    decision = await test_db.scalar(
        select(MemoirNarrativeDecision).where(MemoirNarrativeDecision.target_id == old_claim_id)
    )
    assert decision is not None
    assert decision.action == "needs_review"
    assert decision.before_state_json["verification_status"] == "unreviewed"


async def test_ingestion_job_skips_stale_transcript_without_calling_ai(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="最初的转写。")
    project, utterance, _version = await _load_source(test_db, created)
    job = await schedule_narrative_ingestion_job(
        test_db,
        project_id=project.id,
        utterance=utterance,
        source="test",
    )
    assert job is not None
    replacement = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.USER_EDITED.value,
        content="用户已经改过的转写。",
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(replacement)
    await test_db.flush()
    utterance.chat_message.transcript_version_id = replacement.id
    utterance.chat_message.content_text = replacement.content

    result = await run_narrative_ingestion_job(test_db, job)

    assert result.status.value == "skipped"
    assert result.error_message == "narrative ingestion source changed"
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaim)) == 0


async def test_claim_confirmation_requires_evidence_and_decisions_are_append_only(client, test_db):
    project_id = await create_test_voice_transcript(client, test_db, text="测试素材")
    user = (await client.get("/api/auth/me")).json()
    project = await test_db.get(Project, project_id["session"]["project_id"])
    assert project is not None
    claim = await MemoirClaimRepository(test_db).create(
        MemoirClaimCreate(
            project_id=project.id,
            claim_type=MemoirClaimType.EVENT,
            statement="一条尚无证据的事实。",
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    project_id = project.id
    claim_id = claim.id
    await test_db.commit()

    no_evidence = await client.post(
        f"/api/memoir/projects/{project_id}/claims/{claim_id}/confirm",
        json={"idempotency_key": "confirm-without-evidence", "reason": "测试"},
    )
    assert no_evidence.status_code == 409

    await MemoirClaimRepository(test_db).add_evidence(
        MemoirClaimEvidenceCreate(
            project_id=project_id,
            claim_id=claim_id,
            evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
            evidence_source_type=MemoirClaimEvidenceSourceType.MANUAL,
            evidence_note="用户本人录入并确认。",
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    await test_db.commit()
    payload = {"idempotency_key": "confirm-with-evidence", "reason": "本人确认"}
    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/claims/{claim_id}/confirm",
        json=payload,
    )
    repeated = await client.post(
        f"/api/memoir/projects/{project_id}/claims/{claim_id}/confirm",
        json=payload,
    )
    assert confirmed.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["id"] == confirmed.json()["id"]

    with pytest.raises(IntegrityError, match="append-only"):
        await test_db.execute(
            text("UPDATE memoir_narrative_decisions SET reason = 'tampered' WHERE id = :id"),
            {"id": confirmed.json()["id"]},
        )
        await test_db.flush()
    await test_db.rollback()


async def test_entity_merge_keeps_historical_id_and_records_decision(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我提到了李主任。")
    user = (await client.get("/api/auth/me")).json()
    project_id = created["session"]["project_id"]
    repository = MemoirNarrativeEntityRepository(test_db)
    retained = await repository.create(
        MemoirNarrativeEntityCreate(
            project_id=project_id,
            entity_type=MemoirNarrativeEntityType.PERSON,
            canonical_name="李明",
            aliases=["老李"],
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    source = await repository.create(
        MemoirNarrativeEntityCreate(
            project_id=project_id,
            entity_type=MemoirNarrativeEntityType.PERSON,
            canonical_name="李主任",
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    retained_id = retained.id
    source_id = source.id
    await test_db.commit()

    response = await client.post(
        f"/api/memoir/projects/{project_id}/entities/{source_id}/merge",
        json={
            "retained_entity_id": retained_id,
            "idempotency_key": "merge-li-director",
            "reason": "用户确认是同一个人",
        },
    )
    assert response.status_code == 200
    assert response.json()["action"] == "merge"
    await test_db.refresh(source)
    await test_db.refresh(retained)
    assert source.merged_into_entity_id == retained_id
    assert "李主任" in retained.aliases_json


async def test_pruning_preserves_evidence_referenced_transcript(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="最早的人工版本。")
    project, utterance, pinned_version = await _load_source(test_db, created)
    user = (await client.get("/api/auth/me")).json()
    claim = await MemoirClaimRepository(test_db).create(
        MemoirClaimCreate(
            project_id=project.id,
            claim_type=MemoirClaimType.EVENT,
            statement="最早版本支持的事实。",
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    await MemoirClaimRepository(test_db).add_evidence(
        MemoirClaimEvidenceCreate(
            project_id=project.id,
            claim_id=claim.id,
            evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
            evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT,
            utterance_id=utterance.id,
            transcript_version_id=pinned_version.id,
            audio_file_id=utterance.audio_file_id,
            quote=pinned_version.content,
            source_text_hash=sha256(pinned_version.content.encode("utf-8")).hexdigest(),
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user["id"],
        )
    )
    for index in range(4):
        test_db.add(
            MemoirTranscriptVersion(
                voice_session_id=utterance.voice_session_id,
                source_artifact_id=utterance.id,
                version_type=pinned_version.version_type,
                content=f"后续版本 {index}",
                created_by=MemoirCreatedBy.USER.value,
            )
        )
        await test_db.flush()

    deleted = await prune_transcript_versions_for_utterance(
        test_db,
        utterance_id=utterance.id,
        version_type=pinned_version.version_type,
        keep=1,
    )
    await test_db.commit()

    remaining_ids = set(
        (
            await test_db.scalars(
                select(MemoirTranscriptVersion.id).where(
                    MemoirTranscriptVersion.source_artifact_id == utterance.id,
                    MemoirTranscriptVersion.version_type == pinned_version.version_type,
                )
            )
        ).all()
    )
    assert deleted == 3
    assert pinned_version.id in remaining_ids
    assert len(remaining_ids) == 2


async def test_backfill_dry_run_does_not_write_and_execute_only_queues(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我小时候住在成都。")
    project_id = created["session"]["project_id"]

    dry_run = await run_narrative_ingestion_backfill(
        test_db,
        project_id=project_id,
        dry_run=True,
    )
    assert dry_run["candidate_count"] == 1
    assert dry_run["enqueued_count"] == 0
    assert await test_db.scalar(select(func.count()).select_from(MemoirEditingJob)) == 0

    executed = await run_narrative_ingestion_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    assert executed["enqueued_count"] == 1
    job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.job_type == MemoirEditingJobType.INGEST_NARRATIVE_STATE.value
        )
    )
    assert job is not None
    assert job.payload_json["transcript_version_id"] == created["transcript_version_id"]

    repeated = await run_narrative_ingestion_backfill(
        test_db,
        project_id=project_id,
        dry_run=True,
    )
    assert repeated["candidate_count"] == 0
    assert repeated["skipped_existing_job_count"] == 1
