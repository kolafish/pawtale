from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.domain.memoir.narrative_types import (
    MemoirClaimCertainty,
    MemoirClaimEvidenceRole,
    MemoirClaimEvidenceSourceType,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirNarrativeActorType,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.models.memoir import MemoirBookPlanChapter
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirStory,
    MemoirStoryClaim,
)
from app.services.memoir.narrative.chapter_structure_backfill import (
    run_chapter_structure_backfill,
)
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    memoir_source_ref,
)


def _draft_payload(
    key: str,
    *,
    objective: str,
    include_story_ids: list[str] | None = None,
    exclude_story_ids: list[str] | None = None,
    copy_from_contract_id: str | None = None,
) -> dict:
    return {
        "idempotency_key": key,
        "objective": objective,
        "reader_takeaway": "读者能理解这段经历的变化和影响。",
        "time_scope_text": "",
        "core_questions": [
            {"key": "meaning", "question": "这段经历为什么重要？"}
        ],
        "constraints": {
            "include_story_ids": include_story_ids or [],
            "exclude_story_ids": exclude_story_ids or [],
            "include_topics": [],
            "exclude_topics": [],
        },
        "copy_from_contract_id": copy_from_contract_id,
        "copy_nodes": True,
    }


async def _create_ready_story(
    test_db,
    *,
    created: dict,
    title: str = "离开家乡",
    is_core: bool = True,
    verification_status: MemoirStoryVerificationStatus = MemoirStoryVerificationStatus.CONFIRMED,
) -> tuple[MemoirStory, MemoirClaim]:
    project_id = created["session"]["project_id"]
    user_id = created["user_id"]
    quote = "十八岁那年，我第一次离开家乡去成都读书。"
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
        completion_status=MemoirStoryCompletionStatus.SUFFICIENT.value,
        verification_status=verification_status.value,
        verification_decided_by_user_id=(
            user_id
            if verification_status
            in {
                MemoirStoryVerificationStatus.CONFIRMED,
                MemoirStoryVerificationStatus.REJECTED,
            }
            else None
        ),
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        slot_schema_version="1",
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
                narrative_role=MemoirStoryNarrativeRole.ACTION.value,
                sort_order=0,
                is_core=is_core,
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            ),
        ]
    )
    await test_db.commit()
    return story, claim


async def test_legacy_suggestion_uses_evidence_backed_story_and_enforces_generation_gate(
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
    story, _ = await _create_ready_story(test_db, created=created)
    story_id = story.id

    suggested = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/contract/suggest",
        json={"idempotency_key": "suggest-v1"},
    )
    assert suggested.status_code == 200, suggested.text
    contract = suggested.json()
    story_nodes = [node for node in contract["nodes"] if node["node_type"] == "story"]
    assert len(story_nodes) == 1
    assert story_nodes[0]["stories"][0]["story_id"] == story.id
    assert "source_refs" not in story_nodes[0]

    optional_story = MemoirStory(
        project_id=project_id,
        title="尚待补充的旁支",
        title_normalized="尚待补充的旁支",
        story_type=MemoirStoryType.EVENT.value,
        summary="只有零散信息。",
        time_precision=MemoirClaimTimePrecision.UNKNOWN.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        completion_status=MemoirStoryCompletionStatus.INCOMPLETE.value,
        verification_status=MemoirStoryVerificationStatus.UNREVIEWED.value,
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        slot_schema_version="1",
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=created["user_id"],
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=created["user_id"],
    )
    test_db.add(optional_story)
    await test_db.commit()
    optional_story_id = optional_story.id
    optional_link = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/nodes/{story_nodes[0]['id']}/stories",
        json={
            "story_id": optional_story.id,
            "story_role": "supporting",
            "sort_order": 1,
            "required": False,
        },
    )
    assert optional_link.status_code == 200, optional_link.text

    before_confirmation = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/generation-validation"
    )
    assert before_confirmation.status_code == 200
    assert before_confirmation.json()["can_generate"] is False
    assert {issue["code"] for issue in before_confirmation.json()["issues"]} >= {
        "contract_not_confirmed",
        "required_node_not_ready",
    }

    for node in contract["nodes"]:
        if node["required"] and node["node_type"] != "story":
            response = await client.patch(
                f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/nodes/{node['id']}",
                json={"status": "ready"},
            )
            assert response.status_code == 200, response.text
    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    validated = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/generation-validation"
    )
    assert validated.status_code == 200
    assert validated.json()["can_generate"] is True
    assert validated.json()["issues"] == []

    await test_db.execute(
        update(MemoirStory)
        .where(MemoirStory.id == optional_story_id)
        .values(
            publication_status=MemoirStoryPublicationStatus.PRIVATE.value,
            publication_decided_by_user_id=created["user_id"],
        )
    )
    await test_db.commit()
    private_story_validation = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/generation-validation"
    )
    assert private_story_validation.status_code == 200
    assert private_story_validation.json()["can_generate"] is False
    assert "linked_story_ineligible" in {
        issue["code"] for issue in private_story_validation.json()["issues"]
    }
    await test_db.execute(
        update(MemoirStory)
        .where(MemoirStory.id == optional_story_id)
        .values(publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value)
    )
    await test_db.commit()

    excluded_revision = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/contract/revisions",
        json=_draft_payload(
            "exclude-linked-story",
            objective="调整本章收录边界。",
            exclude_story_ids=[story_id],
            copy_from_contract_id=contract["id"],
        ),
    )
    assert excluded_revision.status_code == 200, excluded_revision.text
    excluded_validation = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{excluded_revision.json()['id']}/generation-validation"
    )
    assert excluded_validation.status_code == 200
    assert "excluded_story_linked" in {
        issue["code"] for issue in excluded_validation.json()["issues"]
    }


async def test_confirmed_contract_is_immutable_and_title_change_does_not_change_objective(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["旧标题"],
    )
    chapter_id = chapters[0].id
    draft = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/contract/revisions",
        json=_draft_payload("contract-v1", objective="说明第一次离家的原因和影响。"),
    )
    assert draft.status_code == 200, draft.text
    contract = draft.json()
    with pytest.raises(IntegrityError, match="lifecycle_shape"):
        await test_db.execute(
            update(MemoirChapterContract)
            .where(MemoirChapterContract.id == contract["id"])
            .values(
                confirmed_by_user_id=created["user_id"],
                confirmed_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
    await test_db.rollback()
    node = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/nodes",
        json={
            "idempotency_key": "opening-v1",
            "node_type": "opening",
            "title": "开场",
            "purpose": "从离家当天的车站场景开始。",
            "required": True,
            "status": "ready",
        },
    )
    assert node.status_code == 200, node.text
    node_id = node.json()["nodes"][0]["id"]
    empty_update = await client.patch(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/nodes/{node_id}",
        json={},
    )
    assert empty_update.status_code == 422
    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text

    immutable = await client.patch(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/nodes/{node_id}",
        json={"title": "不应修改"},
    )
    assert immutable.status_code == 409
    with pytest.raises(IntegrityError, match="confirmed chapter nodes are immutable"):
        await test_db.execute(
            update(MemoirChapterNode)
            .where(MemoirChapterNode.id == node_id)
            .values(title="绕过 API 修改")
        )
    await test_db.rollback()

    renamed = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter_id}/title",
        json={"title": "院子里的童年"},
    )
    assert renamed.status_code == 200, renamed.text
    unchanged = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}"
    )
    assert unchanged.json()["objective"] == "说明第一次离家的原因和影响。"
    assert unchanged.json()["version"] == 1

    revision = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/contract/revisions",
        json=_draft_payload(
            "contract-v2",
            objective="说明离家如何改变了讲述者的选择。",
            copy_from_contract_id=contract["id"],
        ),
    )
    assert revision.status_code == 200, revision.text
    assert revision.json()["version"] == 2
    assert revision.json()["nodes"][0]["id"] != node_id
    assert unchanged.json()["status"] == "confirmed"

    confirmed_v2 = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{revision.json()['id']}/confirm"
    )
    assert confirmed_v2.status_code == 200, confirmed_v2.text
    superseded_v1 = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}"
    )
    assert superseded_v1.json()["status"] == "superseded"


async def test_split_and_merge_move_structure_without_copying_story_or_claim(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["童年", "离家"],
    )
    story, _ = await _create_ready_story(test_db, created=created)
    story_id = story.id
    contracts = []
    for index, chapter in enumerate(chapters):
        response = await client.post(
            f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/contract/revisions",
            json=_draft_payload(
                f"structure-{index}",
                objective=f"组织第 {index + 1} 章。",
                include_story_ids=[story_id] if index == 0 else [],
            ),
        )
        assert response.status_code == 200, response.text
        contracts.append(response.json())

    source_opening = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[0]['id']}/nodes",
        json={
            "idempotency_key": "source-opening",
            "node_type": "opening",
            "title": "开场",
            "purpose": "交代背景。",
            "status": "ready",
        },
    )
    assert source_opening.status_code == 200
    source_story = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[0]['id']}/nodes",
        json={
            "idempotency_key": "source-story",
            "node_type": "story",
            "title": "离家",
            "purpose": "讲述离家经过。",
            "required": True,
        },
    )
    story_node_id = source_story.json()["nodes"][-1]["id"]
    linked = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[0]['id']}/nodes/{story_node_id}/stories",
        json={
            "story_id": story_id,
            "story_role": "primary",
            "sort_order": 0,
            "required": True,
        },
    )
    assert linked.status_code == 200, linked.text
    target_opening = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[1]['id']}/nodes",
        json={
            "idempotency_key": "target-opening",
            "node_type": "opening",
            "title": "开场",
            "purpose": "交代第二章背景。",
            "status": "ready",
        },
    )
    assert target_opening.status_code == 200
    target_story = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[1]['id']}/nodes",
        json={
            "idempotency_key": "target-story",
            "node_type": "story",
            "title": "重复的离家故事",
            "purpose": "验证同章 primary 不重复。",
        },
    )
    assert target_story.status_code == 200
    target_story_node_id = target_story.json()["nodes"][-1]["id"]
    target_link = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[1]['id']}/nodes/{target_story_node_id}/stories",
        json={
            "story_id": story_id,
            "story_role": "primary",
            "sort_order": 0,
            "required": False,
        },
    )
    assert target_link.status_code == 200

    duplicate_primary = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/split",
        json={
            "source_contract_id": contracts[0]["id"],
            "target_contract_id": contracts[1]["id"],
            "node_ids": [story_node_id],
        },
    )
    assert duplicate_primary.status_code == 409
    detached = await client.delete(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[1]['id']}/nodes/{target_story_node_id}/stories/{story_id}"
    )
    assert detached.status_code == 200

    story_count = await test_db.scalar(select(func.count()).select_from(MemoirStory))
    claim_count = await test_db.scalar(select(func.count()).select_from(MemoirClaim))
    link_id = await test_db.scalar(
        select(MemoirChapterNodeStory.id).where(
            MemoirChapterNodeStory.node_id == story_node_id
        )
    )
    split = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/split",
        json={
            "source_contract_id": contracts[0]["id"],
            "target_contract_id": contracts[1]["id"],
            "node_ids": [story_node_id],
        },
    )
    assert split.status_code == 200, split.text
    assert story_node_id in {node["id"] for node in split.json()["target"]["nodes"]}
    assert story_id not in split.json()["source"]["constraints"]["include_story_ids"]
    assert story_id in split.json()["target"]["constraints"]["include_story_ids"]
    assert await test_db.scalar(select(func.count()).select_from(MemoirStory)) == story_count
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaim)) == claim_count
    assert await test_db.scalar(
        select(MemoirChapterNodeStory.id).where(
            MemoirChapterNodeStory.node_id == story_node_id
        )
    ) == link_id

    merged = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/merge",
        json={
            "source_contract_id": contracts[0]["id"],
            "target_contract_id": contracts[1]["id"],
        },
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["source"]["status"] == "superseded"
    assert merged.json()["source"]["nodes"] == []
    assert len(merged.json()["target"]["nodes"]) == 4


async def test_chapter_structure_backfill_is_dry_run_by_default_and_never_confirms(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一章", "第二章"],
    )

    dry_run = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
    )
    assert dry_run["candidate_count"] == 2
    assert dry_run["created_count"] == 0
    assert await test_db.scalar(
        select(func.count()).select_from(MemoirChapterContract)
    ) == 0

    executed = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    assert executed["created_count"] == 2
    contracts = list(
        (
            await test_db.scalars(
                select(MemoirChapterContract).where(
                    MemoirChapterContract.chapter_id.in_([chapter.id for chapter in chapters])
                )
            )
        ).all()
    )
    assert {contract.status for contract in contracts} == {"draft"}

    repeated = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    assert repeated["candidate_count"] == 0
    assert repeated["created_count"] == 0
    assert await test_db.scalar(select(func.count()).select_from(MemoirChapterNode)) == 8

    stored_titles = set(
        (
            await test_db.scalars(
                select(MemoirBookPlanChapter.title).where(
                    MemoirBookPlanChapter.project_id == project_id
                )
            )
        ).all()
    )
    assert stored_titles == {"第一章", "第二章"}


async def test_chapter_structure_backfill_requires_first_confirmable_story_and_repairs_legacy_draft(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["工作经历"],
        source_refs_by_chapter=[[memoir_source_ref(created)]],
    )
    confirmable_story, _ = await _create_ready_story(
        test_db,
        created=created,
        title="储备粮食成为先进乡",
    )
    non_core_story, _ = await _create_ready_story(
        test_db,
        created=created,
        title="一生担任一把手领导职务",
        is_core=False,
        verification_status=MemoirStoryVerificationStatus.NEEDS_VERIFICATION,
    )
    confirmable_story.updated_at = datetime(2026, 1, 1)
    non_core_story.updated_at = confirmable_story.updated_at + timedelta(seconds=1)
    test_db.add_all([confirmable_story, non_core_story])
    await test_db.commit()

    created_report = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    contract = await test_db.get(
        MemoirChapterContract,
        created_report["created_contract_ids"][0],
    )
    assert contract is not None
    assert contract.constraints_json["include_story_ids"] == [confirmable_story.id]
    nodes = list(
        (
            await test_db.scalars(
                select(MemoirChapterNode).where(
                    MemoirChapterNode.contract_id == contract.id
                )
            )
        ).all()
    )
    links = list(
        (
            await test_db.scalars(
                select(MemoirChapterNodeStory).where(
                    MemoirChapterNodeStory.node_id.in_([node.id for node in nodes])
                )
            )
        ).all()
    )
    nodes_by_id = {node.id: node for node in nodes}
    links_by_story = {link.story_id: link for link in links}
    assert links_by_story[confirmable_story.id].required is True
    assert nodes_by_id[links_by_story[confirmable_story.id].node_id].required is True
    assert links_by_story[non_core_story.id].required is False
    assert nodes_by_id[links_by_story[non_core_story.id].node_id].required is False

    contract.constraints_json = {
        **contract.constraints_json,
        "include_story_ids": [non_core_story.id],
    }
    links_by_story[confirmable_story.id].required = False
    nodes_by_id[links_by_story[confirmable_story.id].node_id].required = False
    links_by_story[non_core_story.id].required = True
    nodes_by_id[links_by_story[non_core_story.id].node_id].required = True
    await test_db.commit()

    repaired = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    assert repaired["repaired_contract_ids"] == [contract.id]
    await test_db.refresh(contract)
    await test_db.refresh(links_by_story[confirmable_story.id])
    await test_db.refresh(links_by_story[non_core_story.id])
    assert contract.constraints_json["include_story_ids"] == [confirmable_story.id]
    assert links_by_story[confirmable_story.id].required is True
    assert links_by_story[non_core_story.id].required is False

    repeated = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    assert repeated["repaired_count"] == 0
    assert repeated["unchanged_contract_ids"] == [contract.id]


async def test_chapter_structure_backfill_uses_placeholder_and_skips_user_modified_draft(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["工作经历"],
        source_refs_by_chapter=[[memoir_source_ref(created)]],
    )
    non_core_story, _ = await _create_ready_story(
        test_db,
        created=created,
        title="一生担任一把手领导职务",
        is_core=False,
        verification_status=MemoirStoryVerificationStatus.NEEDS_VERIFICATION,
    )

    report = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    contract = await test_db.get(
        MemoirChapterContract,
        report["created_contract_ids"][0],
    )
    assert contract is not None
    assert contract.constraints_json["include_story_ids"] == []
    nodes = list(
        (
            await test_db.scalars(
                select(MemoirChapterNode)
                .where(MemoirChapterNode.contract_id == contract.id)
                .order_by(MemoirChapterNode.sort_order)
            )
        ).all()
    )
    assert [node.title for node in nodes[:3]] == [
        "开场",
        "待补充核心故事",
        non_core_story.title,
    ]
    assert nodes[1].required is True
    assert nodes[1].status == "needs_work"
    assert nodes[2].required is False

    nodes[2].updated_by = MemoirNarrativeActorType.USER.value
    nodes[2].updated_by_user_id = created["user_id"]
    test_db.add(nodes[2])
    await test_db.commit()
    skipped = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    assert skipped["skipped_modified_contract_ids"] == [contract.id]
