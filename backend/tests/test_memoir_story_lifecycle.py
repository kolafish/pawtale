from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
    MemoirStoryNarrativeRole,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.models.memoir import MemoirTranscriptVersion, MemoirVoiceUtterance
from app.models.memoir_narrative import MemoirClaim, MemoirStory, MemoirStoryClaim
from app.models.projects import Project
from app.repositories.memoir_narrative import MemoirClaimRepository
from app.schemas.memoir_narrative import (
    MemoirClaimCreate,
    MemoirClaimEvidenceCreate,
    MemoirStoryClaimInput,
    MemoirStoryCreate,
)
from app.services.memoir.narrative import stories, story_merging
from app.services.memoir.narrative.ingestion import (
    ingest_transcript_version,
    transcript_text_hash,
)
from tests.memoir_helpers import create_test_voice_transcript


class FakeNarrativeAI:
    provider_name = "fake"
    model_name = "fake-story"

    def __init__(self, payload: dict):
        self.payload = payload

    async def _call_llm(self, *args, **kwargs) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


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
    assert utterance is not None and version is not None and project is not None
    return project, utterance, version


async def _create_confirmed_claim(
    client,
    test_db,
    *,
    project_id: str,
    text: str,
) -> MemoirClaim:
    created = await create_test_voice_transcript(
        client,
        test_db,
        text=text,
        project_id=project_id,
    )
    project, utterance, version = await _load_source(test_db, created)
    repository = MemoirClaimRepository(test_db)
    claim = await repository.create(
        MemoirClaimCreate(
            project_id=project.id,
            claim_type=MemoirClaimType.EVENT,
            statement=text,
            time_precision=MemoirClaimTimePrecision.UNKNOWN,
            verification_status=MemoirClaimVerificationStatus.CONFIRMED,
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=created["user_id"],
        )
    )
    await repository.add_evidence(
        MemoirClaimEvidenceCreate(
            project_id=project.id,
            claim_id=claim.id,
            evidence_role=MemoirClaimEvidenceRole.SUPPORTS,
            evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT,
            utterance_id=utterance.id,
            transcript_version_id=version.id,
            speaker_role="narrator",
            quote=text,
            source_text_hash=transcript_text_hash(version.content),
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=created["user_id"],
        )
    )
    return claim


def test_story_schema_keeps_decisions_and_claim_links_explicit():
    with pytest.raises(ValueError, match="verification requires the decision workflow"):
        MemoirStoryCreate(
            project_id="project-id",
            title="不能绕过确认链",
            story_type=MemoirStoryType.EVENT,
            verification_status=MemoirStoryVerificationStatus.CONFIRMED,
            created_by=MemoirNarrativeActorType.AI,
        )
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        MemoirStoryClaimInput.model_validate(
            {
                "claim_id": "claim-id",
                "narrative_role": "action",
                "slot_keys": ["actions"],
            }
        )


async def test_story_api_is_idempotent_and_requires_evidence_before_confirmation(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db, text="第一次到新学校。")
    project_id = created["session"]["project_id"]
    request = {
        "title": "第一次到新学校",
        "story_type": "event",
        "summary": "讲述第一次到新学校的经历。",
        "idempotency_key": "manual-story-new-school",
        "reason": "用户手动整理",
    }
    first = await client.post(f"/api/memoir/projects/{project_id}/stories", json=request)
    repeated = await client.post(f"/api/memoir/projects/{project_id}/stories", json=request)
    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["id"] == first.json()["id"]
    assert "slots" not in first.json()

    story_id = first.json()["id"]
    no_evidence = await client.post(
        f"/api/memoir/projects/{project_id}/stories/{story_id}/confirm",
        json={"idempotency_key": "confirm-empty-story", "reason": "确认"},
    )
    assert no_evidence.status_code == 409

    claim = await _create_confirmed_claim(
        client,
        test_db,
        project_id=project_id,
        text="我走进教室时很紧张。",
    )
    attached = await client.post(
        f"/api/memoir/projects/{project_id}/stories/{story_id}/claims",
        json={
            "claims": [
                {
                    "claim_id": claim.id,
                    "narrative_role": "background",
                    "is_core": True,
                }
            ],
            "reason": "关联原始事实",
        },
    )
    assert attached.status_code == 200
    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/stories/{story_id}/confirm",
        json={"idempotency_key": "confirm-story-with-evidence", "reason": "本人确认"},
    )
    assert confirmed.status_code == 200
    detail = await client.get(f"/api/memoir/projects/{project_id}/stories/{story_id}")
    assert detail.status_code == 200
    assert detail.json()["verification_status"] == MemoirStoryVerificationStatus.CONFIRMED.value
    assert detail.json()["claims"][0]["claim_id"] == claim.id


async def test_later_transcript_reuses_story_and_attaches_new_claim(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我搬到成都。")
    project, utterance, version = await _load_source(test_db, created)
    payload = {
        "schema_version": "1",
        "entities": [],
        "claims": [
            {
                "key": "move",
                "claim_type": "event",
                "statement": "讲述者搬到成都。",
                "subject_entity_key": None,
                "predicate_code": "narrative_fact",
                "object_entity_key": None,
                "object_text": None,
                "place_entity_key": None,
                "time_text": None,
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "certainty": "exact",
                "evidence_quotes": ["我搬到成都"],
            }
        ],
        "stories": [
            {
                "key": "move_to_chengdu",
                "title": "搬到成都",
                "story_type": "event",
                "summary": "讲述者搬到成都。",
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "primary_place_entity_key": None,
                "existing_story_id": None,
                "match_confidence": 0.0,
                "claims": [
                    {
                        "claim_key": "move",
                        "narrative_role": "action",
                        "is_core": True,
                    }
                ],
            }
        ],
    }
    first = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=FakeNarrativeAI(payload),
    )
    story_id = first.created_story_ids[0]

    second = await create_test_voice_transcript(
        client,
        test_db,
        text="后来我一直在那里生活。",
        project_id=project.id,
    )
    _, second_utterance, second_version = await _load_source(test_db, second)
    payload["claims"][0].update(
        {
            "key": "later_life",
            "statement": "讲述者后来一直在成都生活。",
            "evidence_quotes": ["后来我一直在那里生活"],
        }
    )
    payload["stories"][0].update(
        {
            "key": "move_reflection",
            "summary": "讲述者回望搬家后的生活。",
            "existing_story_id": story_id,
            "match_confidence": 0.98,
            "claims": [
                {
                    "claim_key": "later_life",
                    "narrative_role": "outcome",
                    "is_core": False,
                }
            ],
        }
    )
    later = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=second_utterance,
        version=second_version,
        ai_client=FakeNarrativeAI(payload),
    )

    assert later.created_story_ids == ()
    assert later.reused_story_ids == (story_id,)
    assert await test_db.scalar(
        select(MemoirStoryClaim).where(
            MemoirStoryClaim.story_id == story_id,
            MemoirStoryClaim.claim_id == later.created_claim_ids[0],
        )
    ) is not None


async def test_manual_story_merge_moves_claims_and_is_idempotent(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="两段故事。")
    project_id = created["session"]["project_id"]
    user_id = created["user_id"]
    source = await stories.create_story(
        test_db,
        data=MemoirStoryCreate(
            project_id=project_id,
            title="搬家片段",
            story_type=MemoirStoryType.EVENT,
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user_id,
        ),
    )
    retained = await stories.create_story(
        test_db,
        data=MemoirStoryCreate(
            project_id=project_id,
            title="搬到成都",
            story_type=MemoirStoryType.EVENT,
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=user_id,
        ),
    )
    source_claim = await _create_confirmed_claim(
        client,
        test_db,
        project_id=project_id,
        text="我们坐火车搬家。",
    )
    retained_claim = await _create_confirmed_claim(
        client,
        test_db,
        project_id=project_id,
        text="后来我在成都上学。",
    )
    for story, claim in ((source, source_claim), (retained, retained_claim)):
        await stories.attach_claims_to_story(
            test_db,
            story=story,
            claims=[
                MemoirStoryClaimInput(
                    claim_id=claim.id,
                    narrative_role=MemoirStoryNarrativeRole.ACTION,
                    is_core=True,
                )
            ],
            actor_type=MemoirNarrativeActorType.USER,
            actor_user_id=user_id,
            reason="人工整理",
        )

    first = await story_merging.merge_stories(
        test_db,
        project_id=project_id,
        source_story_id=source.id,
        retained_story_id=retained.id,
        actor_user_id=user_id,
        idempotency_key="merge-moving-story",
        reason="确认是同一次搬家",
    )
    repeated = await story_merging.merge_stories(
        test_db,
        project_id=project_id,
        source_story_id=source.id,
        retained_story_id=retained.id,
        actor_user_id=user_id,
        idempotency_key="merge-moving-story",
        reason="确认是同一次搬家",
    )

    await test_db.refresh(source)
    assert repeated.id == first.id
    assert source.lifecycle_status == "merged"
    assert source.merged_into_story_id == retained.id
    retained_claim_ids = set(
        (
            await test_db.scalars(
                select(MemoirStoryClaim.claim_id).where(
                    MemoirStoryClaim.story_id == retained.id
                )
            )
        ).all()
    )
    assert retained_claim_ids == {source_claim.id, retained_claim.id}
    assert await test_db.get(MemoirStory, retained.id) is not None
