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
    MemoirNarrativeEntityType,
    MemoirStoryCompletionStatus,
    MemoirStoryNarrativeRole,
    MemoirStorySlotStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.models.memoir import MemoirTranscriptVersion, MemoirVoiceUtterance
from app.models.memoir_narrative import (
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStorySlot,
)
from app.models.projects import Project
from app.repositories.memoir_narrative import (
    MemoirClaimRepository,
    MemoirNarrativeEntityRepository,
)
from app.schemas.memoir_narrative import (
    MemoirClaimCreate,
    MemoirClaimEvidenceCreate,
    MemoirNarrativeEntityCreate,
    MemoirStoryClaimInput,
    MemoirStoryCreate,
)
from app.schemas.memoir_narrative.story_slots import STORY_SLOT_SCHEMAS, story_slot_schema
from app.schemas.memoir_narrative.extraction import (
    MemoirNarrativeExtractedStory,
    MemoirNarrativeExtractedStoryClaim,
)
from app.services.memoir.narrative import (
    decisions,
    entities,
    stories,
    story_decisions,
    story_merging,
    story_matching,
)
from app.services.memoir.narrative.ingestion import ingest_transcript_version, transcript_text_hash
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


def _event_payload(*, existing_story_id: str | None = None) -> dict:
    quotes = {
        "background": "我小时候住在成都",
        "trigger": "父亲调动工作后，我们决定搬家",
        "people": "我和父母一起收拾行李",
        "actions": "我们坐了一夜火车到新家",
        "outcome": "后来我在成都读完了中学",
    }
    claims = []
    story_claims = []
    for index, (slot_key, quote) in enumerate(quotes.items()):
        claim_key = f"claim_{index}"
        claims.append(
            {
                "key": claim_key,
                "claim_type": "event",
                "statement": quote + "。",
                "subject_entity_key": "narrator",
                "predicate_code": "narrative_fact",
                "object_entity_key": None,
                "object_text": None,
                "place_entity_key": "chengdu",
                "time_text": None,
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "certainty": "exact",
                "evidence_quotes": [quote],
            }
        )
        story_claims.append(
            {
                "claim_key": claim_key,
                "narrative_role": "context" if slot_key in {"background", "people"} else "action",
                "slot_keys": [slot_key],
                "evidence_role": "supports",
                "is_core": True,
            }
        )
    return {
        "schema_version": "1",
        "entities": [
            {
                "key": "narrator",
                "entity_type": "person",
                "canonical_name": "讲述者",
                "aliases": ["我"],
                "description": "讲述者本人",
                "is_narrator": True,
            },
            {
                "key": "chengdu",
                "entity_type": "place",
                "canonical_name": "成都",
                "aliases": [],
                "description": "故事地点",
                "is_narrator": False,
            },
        ],
        "claims": claims,
        "stories": [
            {
                "key": "move_to_chengdu",
                "title": "搬到成都",
                "story_type": "event",
                "summary": "一家人因父亲工作调动搬到成都。",
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "primary_place_entity_key": "chengdu",
                "existing_story_id": existing_story_id,
                "match_confidence": 0.97 if existing_story_id else 0.0,
                "claims": story_claims,
            }
        ],
    }


def test_story_slot_schemas_are_versioned_and_cover_all_story_types():
    assert {story_type for story_type, version in STORY_SLOT_SCHEMAS if version == "1"} == set(
        MemoirStoryType
    )
    assert story_slot_schema(MemoirStoryType.EVENT).slot_keys == (
        "background",
        "trigger",
        "people",
        "actions",
        "outcome",
        "difficulty",
        "turning_point",
        "reflection",
    )
    with pytest.raises(ValueError, match="verification requires the decision workflow"):
        MemoirStoryCreate(
            project_id="project-id",
            title="不能绕过确认链",
            story_type=MemoirStoryType.EVENT,
            verification_status=MemoirStoryVerificationStatus.CONFIRMED,
            created_by=MemoirNarrativeActorType.AI,
        )


async def test_different_sessions_add_evidence_to_one_story(client, test_db):
    source_text = "。".join(
        [
            "我小时候住在成都",
            "父亲调动工作后，我们决定搬家",
            "我和父母一起收拾行李",
            "我们坐了一夜火车到新家",
            "后来我在成都读完了中学",
        ]
    ) + "。"
    first = await create_test_voice_transcript(client, test_db, text=source_text)
    project, utterance, version = await _load_source(test_db, first)
    first_result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=utterance,
        version=version,
        ai_client=FakeNarrativeAI(_event_payload()),
    )
    assert len(first_result.created_story_ids) == 1
    story = await test_db.get(MemoirStory, first_result.created_story_ids[0])
    assert story is not None
    response = await stories.story_response(test_db, story=story)
    required_slots = [slot for slot in response.slots if slot.required]
    assert {slot.status for slot in required_slots} == {MemoirStorySlotStatus.PARTIAL.value}
    assert story.completion_status == MemoirStoryCompletionStatus.INCOMPLETE.value

    user_id = first["user_id"]
    claim_ids = list(
        (
            await test_db.scalars(
                select(MemoirStoryClaim.claim_id).where(MemoirStoryClaim.story_id == story.id)
            )
        ).all()
    )
    for index, claim_id in enumerate(claim_ids):
        await decisions.decide_claim_verification(
            test_db,
            project_id=project.id,
            claim_id=claim_id,
            confirmed=True,
            actor_user_id=user_id,
            idempotency_key=f"confirm-story-claim-{index}",
        )
    await test_db.refresh(story)
    response = await stories.story_response(test_db, story=story)
    assert {slot.status for slot in response.slots if slot.required} == {
        MemoirStorySlotStatus.SUFFICIENT.value
    }
    assert story.completion_status == MemoirStoryCompletionStatus.SUFFICIENT.value

    second = await create_test_voice_transcript(
        client,
        test_db,
        text="多年以后回想起来，我最感谢父母当时的勇气。",
        project_id=project.id,
    )
    _, second_utterance, second_version = await _load_source(test_db, second)
    payload = {
        "schema_version": "1",
        "entities": [
            {
                "key": "narrator",
                "entity_type": "person",
                "canonical_name": "讲述者",
                "aliases": ["我"],
                "description": "讲述者本人",
                "is_narrator": True,
            }
        ],
        "claims": [
            {
                "key": "reflection",
                "claim_type": "reflection",
                "statement": "讲述者感谢父母当时的勇气。",
                "subject_entity_key": "narrator",
                "predicate_code": "narrative_fact",
                "object_entity_key": None,
                "object_text": None,
                "place_entity_key": None,
                "time_text": "多年以后",
                "time_start": None,
                "time_end": None,
                "time_precision": "relative",
                "certainty": "exact",
                "evidence_quotes": ["我最感谢父母当时的勇气"],
            }
        ],
        "stories": [
            {
                "key": "move_reflection",
                "title": "搬到成都",
                "story_type": "event",
                "summary": "讲述者回望搬家经历。",
                "time_start": None,
                "time_end": None,
                "time_precision": "unknown",
                "primary_place_entity_key": None,
                "existing_story_id": story.id,
                "match_confidence": 0.97,
                "claims": [
                    {
                        "claim_key": "reflection",
                        "narrative_role": "reflection",
                        "slot_keys": ["reflection"],
                        "evidence_role": "supports",
                        "is_core": False,
                    }
                ],
            }
        ],
    }
    second_result = await ingest_transcript_version(
        test_db,
        project=project,
        utterance=second_utterance,
        version=second_version,
        ai_client=FakeNarrativeAI(payload),
    )

    assert second_result.created_story_ids == ()
    assert second_result.reused_story_ids == (story.id,)
    assert await test_db.scalar(
        select(MemoirStoryClaim).where(
            MemoirStoryClaim.story_id == story.id,
            MemoirStoryClaim.claim_id == second_result.created_claim_ids[0],
        )
    ) is not None


async def _create_confirmed_claim(client, test_db, *, project_id: str, text: str) -> MemoirClaim:
    created = await create_test_voice_transcript(client, test_db, text=text, project_id=project_id)
    project, utterance, version = await _load_source(test_db, created)
    claim = await MemoirClaimRepository(test_db).create(
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
    await MemoirClaimRepository(test_db).add_evidence(
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


async def test_manual_incomplete_resolution_survives_new_claims(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="故事背景。")
    project_id = created["session"]["project_id"]
    story = await stories.create_story(
        test_db,
        data=MemoirStoryCreate(
            project_id=project_id,
            title="一次尚未讲完的搬家",
            story_type=MemoirStoryType.EVENT,
            created_by=MemoirNarrativeActorType.USER,
            created_by_user_id=created["user_id"],
        ),
    )
    claim = await _create_confirmed_claim(
        client,
        test_db,
        project_id=project_id,
        text="我们最终到了新家。",
    )
    claim_id = claim.id
    await stories.attach_claims_to_story(
        test_db,
        story=story,
        claims=[
            MemoirStoryClaimInput(
                claim_id=claim_id,
                narrative_role=MemoirStoryNarrativeRole.OUTCOME,
                slot_keys=["outcome"],
                is_core=True,
            )
        ],
        actor_type=MemoirNarrativeActorType.USER,
        actor_user_id=created["user_id"],
        reason="manual story organization",
    )
    with pytest.raises(
        ValueError,
        match="sufficient story slot cannot be resolved as incomplete",
    ):
        await story_decisions.resolve_story_slot(
            test_db,
            project_id=project_id,
            story_id=story.id,
            slot_key="outcome",
            status=MemoirStorySlotStatus.ACCEPTED_INCOMPLETE,
            actor_user_id=created["user_id"],
            idempotency_key="invalid-outcome-incomplete",
        )
    await story_decisions.resolve_story_slot(
        test_db,
        project_id=project_id,
        story_id=story.id,
        slot_key="background",
        status=MemoirStorySlotStatus.ACCEPTED_INCOMPLETE,
        actor_user_id=created["user_id"],
        idempotency_key="accept-background-incomplete",
        note="老人记不清更早的背景，可以接受。",
    )
    new_background = await _create_confirmed_claim(
        client,
        test_db,
        project_id=project_id,
        text="搬家前我们住在河边。",
    )
    await stories.attach_claims_to_story(
        test_db,
        story=story,
        claims=[
            MemoirStoryClaimInput(
                claim_id=new_background.id,
                narrative_role=MemoirStoryNarrativeRole.BACKGROUND,
                slot_keys=["background"],
                is_core=True,
            )
        ],
        actor_type=MemoirNarrativeActorType.AI,
        actor_user_id=None,
        reason="later interview added background evidence",
    )

    response = await stories.story_response(test_db, story=story)
    background = next(slot for slot in response.slots if slot.slot_key == "background")
    assert background.status == MemoirStorySlotStatus.ACCEPTED_INCOMPLETE.value
    assert background.user_resolution_note == "老人记不清更早的背景，可以接受。"
    assert background.assessment_summary == "supports=1; confirmed=1; contradicts=0"


async def test_story_api_is_idempotent_and_requires_evidence_before_confirmation(client, test_db):
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
    assert len(first.json()["slots"]) == 8

    story_id = first.json()["id"]
    assert list(
        (
            await test_db.scalars(
                select(MemoirStorySlot).where(
                    MemoirStorySlot.project_id == project_id,
                    MemoirStorySlot.story_id == story_id,
                )
            )
        ).all()
    ) == []
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
                    "slot_keys": ["background"],
                    "is_core": True,
                }
            ],
            "reason": "关联原始事实",
        },
    )
    assert attached.status_code == 200
    persisted_slots = list(
        (
            await test_db.scalars(
                select(MemoirStorySlot).where(
                    MemoirStorySlot.project_id == project_id,
                    MemoirStorySlot.story_id == story_id,
                )
            )
        ).all()
    )
    assert [slot.slot_key for slot in persisted_slots] == ["background"]
    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/stories/{story_id}/confirm",
        json={"idempotency_key": "confirm-story-with-evidence", "reason": "本人确认"},
    )
    assert confirmed.status_code == 200
    detail = await client.get(f"/api/memoir/projects/{project_id}/stories/{story_id}")
    assert detail.status_code == 200
    assert detail.json()["verification_status"] == MemoirStoryVerificationStatus.CONFIRMED.value
    assert detail.json()["claims"][0]["claim_id"] == claim.id


async def test_ai_story_candidate_cannot_merge_without_hard_anchor(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="旧故事。")
    project_id = created["session"]["project_id"]
    existing = await stories.create_story(
        test_db,
        data=MemoirStoryCreate(
            project_id=project_id,
            title="在成都读书",
            story_type=MemoirStoryType.EVENT,
            created_by=MemoirNarrativeActorType.AI,
        ),
    )
    candidate = MemoirNarrativeExtractedStory(
        key="unrelated_job",
        title="第一次参加工作",
        story_type=MemoirStoryType.EVENT,
        summary="另一件没有共同时间地点的事。",
        existing_story_id=existing.id,
        match_confidence=0.99,
        claims=[
            MemoirNarrativeExtractedStoryClaim(
                claim_key="new_claim",
                narrative_role=MemoirStoryNarrativeRole.ACTION,
                slot_keys=["actions"],
                is_core=True,
            )
        ],
    )

    resolved, review_candidates, was_created = await story_matching.resolve_extracted_story(
        test_db,
        project_id=project_id,
        transcript_version_id="new-transcript-version",
        candidate=candidate,
        primary_place_entity_id=None,
        claim_ids={"new-claim-id"},
    )

    assert was_created is True
    assert resolved.id != existing.id
    assert review_candidates == (existing.id,)
    assert resolved.verification_status == MemoirStoryVerificationStatus.NEEDS_VERIFICATION.value


async def test_active_followup_target_can_reuse_story_without_duplicate_title(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="补访回答。")
    project_id = created["session"]["project_id"]
    existing = await stories.create_story(
        test_db,
        data=MemoirStoryCreate(
            project_id=project_id,
            title="爷爷退休后的创业经历",
            story_type=MemoirStoryType.EVENT,
            created_by=MemoirNarrativeActorType.AI,
        ),
    )
    candidate = MemoirNarrativeExtractedStory(
        key="courtyard_colleagues",
        title="爷爷和院里同事一起做事",
        story_type=MemoirStoryType.EVENT,
        summary="爷爷退休后和院里同事一起创业。",
        claims=[
            MemoirNarrativeExtractedStoryClaim(
                claim_key="people_claim",
                narrative_role=MemoirStoryNarrativeRole.CONTEXT,
                slot_keys=["people"],
                is_core=True,
            )
        ],
    )

    resolved, review_candidates, was_created = await story_matching.resolve_extracted_story(
        test_db,
        project_id=project_id,
        transcript_version_id="followup-transcript",
        candidate=candidate,
        primary_place_entity_id=None,
        claim_ids={"people-claim-id"},
        preferred_story_id=existing.id,
    )

    assert resolved.id == existing.id
    assert was_created is False
    assert review_candidates == ()


async def test_same_title_without_structural_anchor_requires_review(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="同名的两段往事。")
    project_id = created["session"]["project_id"]
    existing = await stories.create_story(
        test_db,
        data=MemoirStoryCreate(
            project_id=project_id,
            title="第一次离家",
            story_type=MemoirStoryType.EVENT,
            created_by=MemoirNarrativeActorType.AI,
        ),
    )
    candidate = MemoirNarrativeExtractedStory(
        key="same_title_new_event",
        title="第一次离家",
        story_type=MemoirStoryType.EVENT,
        summary="标题相同，但没有共同事实、时间或地点。",
        claims=[
            MemoirNarrativeExtractedStoryClaim(
                claim_key="unrelated_claim",
                narrative_role=MemoirStoryNarrativeRole.ACTION,
                slot_keys=["actions"],
                is_core=True,
            )
        ],
    )

    resolved, review_candidates, was_created = await story_matching.resolve_extracted_story(
        test_db,
        project_id=project_id,
        transcript_version_id="same-title-new-transcript",
        candidate=candidate,
        primary_place_entity_id=None,
        claim_ids={"unrelated-claim-id"},
    )

    assert was_created is True
    assert resolved.id != existing.id
    assert review_candidates == (existing.id,)
    assert resolved.verification_status == MemoirStoryVerificationStatus.NEEDS_VERIFICATION.value


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
    for story, claim, slot_key in (
        (source, source_claim, "actions"),
        (retained, retained_claim, "outcome"),
    ):
        await stories.attach_claims_to_story(
            test_db,
            story=story,
            claims=[
                MemoirStoryClaimInput(
                    claim_id=claim.id,
                    narrative_role=MemoirStoryNarrativeRole.ACTION,
                    slot_keys=[slot_key],
                    is_core=True,
                )
            ],
            actor_type=MemoirNarrativeActorType.USER,
            actor_user_id=user_id,
            reason="人工整理",
        )
    await story_decisions.resolve_story_slot(
        test_db,
        project_id=project_id,
        story_id=source.id,
        slot_key="outcome",
        status=MemoirStorySlotStatus.ACCEPTED_INCOMPLETE,
        actor_user_id=user_id,
        idempotency_key="source-outcome-incomplete",
        note="这段记忆没有讲到结果。",
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
                select(MemoirStoryClaim.claim_id).where(MemoirStoryClaim.story_id == retained.id)
            )
        ).all()
    )
    assert retained_claim_ids == {source_claim.id, retained_claim.id}
    retained_response = await stories.story_response(test_db, story=retained)
    retained_outcome = next(
        slot for slot in retained_response.slots if slot.slot_key == "outcome"
    )
    assert retained_outcome.status == MemoirStorySlotStatus.SUFFICIENT.value
