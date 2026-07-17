from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from sqlalchemy import select

from app.domain.memoir.narrative_types import (
    MemoirClaimCertainty,
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirChapterNodeStoryRole,
    MemoirNarrativeActorType,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryRevisionChangeType,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.models.memoir_narrative import (
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStoryRevision,
)
from app.services.memoir.narrative import chapter_contracts
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    memoir_source_ref,
)


async def _create_ready_story(
    test_db,
    *,
    created: dict,
    title: str = "离开家乡",
    narrative_role: MemoirStoryNarrativeRole = MemoirStoryNarrativeRole.ACTION,
) -> MemoirStory:
    project_id = created["session"]["project_id"]
    user_id = created["user_id"]
    quote = f"十八岁那年，我第一次经历了“{title}”。"
    claim = MemoirClaim(
        project_id=project_id,
        claim_type=MemoirClaimType.EVENT.value,
        statement=quote,
        predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        certainty=MemoirClaimCertainty.EXACT.value,
        verification_status=MemoirClaimVerificationStatus.CONFIRMED.value,
        publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=user_id,
    )
    story = MemoirStory(
        project_id=project_id,
        title=title,
        title_normalized=title,
        story_type=MemoirStoryType.EVENT.value,
        summary=quote,
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        verification_status=MemoirStoryVerificationStatus.CONFIRMED.value,
        verification_decided_by_user_id=user_id,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=user_id,
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=user_id,
    )
    test_db.add_all([claim, story])
    await test_db.flush()
    test_db.add_all(
        [
            MemoirClaimEvidence(
                project_id=project_id,
                claim_id=claim.id,
                evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
                evidence_source_type=MemoirClaimEvidenceSourceType.TRANSCRIPT.value,
                utterance_id=created["utterance_id"],
                transcript_version_id=created["transcript_version_id"],
                audio_file_id=created["audio_file_id"],
                start_ms=0,
                end_ms=8000,
                quote=quote,
                source_text_hash=sha256(quote.encode("utf-8")).hexdigest(),
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            ),
            MemoirStoryClaim(
                project_id=project_id,
                story_id=story.id,
                claim_id=claim.id,
                narrative_role=narrative_role.value,
                sort_order=0,
                is_core=True,
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            ),
            MemoirStoryRevision(
                project_id=project_id,
                story_id=story.id,
                story_version=story.version,
                change_type=MemoirStoryRevisionChangeType.CREATED.value,
                before_state_json={},
                after_state_json={"summary": story.summary},
                reason="test fixture",
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            ),
        ]
    )
    await test_db.commit()
    return story


async def test_system_suggestion_builds_evidence_backed_structure_and_confirms(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一次离家"],
        source_refs_by_chapter=[[memoir_source_ref(created)]],
    )
    story = await _create_ready_story(test_db, created=created)

    suggested = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/contract/suggest",
        json={"idempotency_key": "suggest-v1"},
    )
    assert suggested.status_code == 200, suggested.text
    contract = suggested.json()
    assert contract["status"] == "draft"
    assert contract["created_by"] == "system"
    assert contract["constraints"]["include_story_ids"] == [story.id]
    assert [node["node_type"] for node in contract["nodes"]] == [
        "opening",
        "story",
        "reflection",
        "closing",
    ]
    story_node = contract["nodes"][1]
    assert story_node["required"] is True
    assert story_node["status"] == "ready"
    assert story_node["stories"] == [
        {
            "id": story_node["stories"][0]["id"],
            "story_id": story.id,
            "story_role": "primary",
            "sort_order": 0,
            "required": True,
        }
    ]

    repeated = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/contract/suggest",
        json={"idempotency_key": "suggest-v2"},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == contract["id"]

    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_contract = confirmed.json()
    assert confirmed_contract["status"] == "confirmed"
    assert all(
        node["status"] == "ready"
        for node in confirmed_contract["nodes"]
        if node["required"]
    )


async def test_system_draft_absorbs_new_story_without_changing_required_anchor(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["工作经历"],
        source_refs_by_chapter=[[memoir_source_ref(created)]],
    )
    first_story = await _create_ready_story(
        test_db,
        created=created,
        title="第一次承担重要工作",
    )
    first_story.updated_at = datetime(2026, 1, 1)
    test_db.add(first_story)
    await test_db.commit()

    contract = await chapter_contracts.create_system_contract_suggestion(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
        idempotency_key="system-draft",
    )
    await test_db.commit()
    assert contract.constraints_json["include_story_ids"] == [first_story.id]

    new_story = await _create_ready_story(
        test_db,
        created=created,
        title="后来补充的关键转折",
    )
    new_story.updated_at = datetime(2026, 1, 2)
    test_db.add(new_story)
    await test_db.commit()

    updated_contract_ids = (
        await chapter_contracts.sync_system_drafts_for_utterance(
            test_db,
            project_id=project_id,
            utterance_id=created["utterance_id"],
        )
    )
    assert updated_contract_ids == (contract.id,)
    await test_db.refresh(contract)
    assert contract.constraints_json["include_story_ids"] == [first_story.id]
    primary_story_ids = set(
        (
            await test_db.scalars(
                select(MemoirChapterNodeStory.story_id)
                .join(
                    MemoirChapterNode,
                    MemoirChapterNode.id == MemoirChapterNodeStory.node_id,
                )
                .where(
                    MemoirChapterNode.contract_id == contract.id,
                    MemoirChapterNodeStory.story_role
                    == MemoirChapterNodeStoryRole.PRIMARY.value,
                )
            )
        ).all()
    )
    assert primary_story_ids == {first_story.id, new_story.id}
