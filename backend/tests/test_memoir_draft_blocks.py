from __future__ import annotations

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeType,
    MemoirClaimCertainty,
    MemoirClaimPredicate,
    MemoirClaimPublicationStatus,
    MemoirClaimTimePrecision,
    MemoirClaimType,
    MemoirClaimVerificationStatus,
    MemoirDraftBlockClaimUsageRole,
    MemoirDraftBlockEditorialStatus,
    MemoirDraftBlockFreshnessStatus,
    MemoirDraftBlockRole,
    MemoirDraftBlockStoryUsageRole,
    MemoirNarrativeActorType,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryRevisionChangeType,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirClaim,
    MemoirDraftBlock,
    MemoirDraftBlockVersion,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStoryRevision,
)
from app.schemas.memoir_narrative import (
    MemoirDraftBlockClaimSource,
    MemoirDraftBlockStorySource,
)
from app.services.memoir.narrative import draft_blocks
from app.services.memoir.narrative.gap_rules import load_chapter_audit_context
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    ensure_mobile_memoir_project,
)


async def _create_narrative_sources(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    current_user = (await client.get("/api/auth/me")).json()
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一次离家"],
    )
    chapter = chapters[0]
    contract = MemoirChapterContract(
        project_id=project_id,
        chapter_id=chapter.id,
        version=1,
        objective="写清第一次离家的经过与影响。",
        reader_takeaway="理解这次离家如何改变了讲述者。",
        core_questions_json=[],
        constraints_json={
            "include_story_ids": [],
            "exclude_story_ids": [],
            "include_topics": [],
            "exclude_topics": [],
        },
        status=MemoirChapterContractStatus.DRAFT.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(contract)
    await test_db.flush()
    node = MemoirChapterNode(
        project_id=project_id,
        chapter_id=chapter.id,
        contract_id=contract.id,
        node_type=MemoirChapterNodeType.STORY.value,
        title="第一次离家",
        purpose="交代事件经过。",
        sort_order=0,
        required=True,
        status=MemoirChapterNodeStatus.READY.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
        updated_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    story = MemoirStory(
        project_id=project_id,
        title="第一次离家",
        title_normalized="第一次离家",
        story_type=MemoirStoryType.EVENT.value,
        summary="十八岁那年离开家乡去成都读书。",
        time_precision=MemoirClaimTimePrecision.YEAR.value,
        lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
        completion_status=MemoirStoryCompletionStatus.SUFFICIENT.value,
        verification_status=MemoirStoryVerificationStatus.CONFIRMED.value,
        verification_decided_by_user_id=current_user["id"],
        publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
        slot_schema_version="1",
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=current_user["id"],
        updated_by=MemoirNarrativeActorType.USER.value,
        updated_by_user_id=current_user["id"],
    )
    claims = [
        MemoirClaim(
            project_id=project_id,
            claim_type=MemoirClaimType.EVENT.value,
            statement=statement,
            predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
            time_precision=MemoirClaimTimePrecision.YEAR.value,
            certainty=MemoirClaimCertainty.EXACT.value,
            verification_status=MemoirClaimVerificationStatus.CONFIRMED.value,
            publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=current_user["id"],
            updated_by=MemoirNarrativeActorType.USER.value,
            updated_by_user_id=current_user["id"],
        )
        for statement in (
            "十八岁那年离开家乡。",
            "到成都后住进了学校宿舍。",
        )
    ]
    test_db.add_all([node, story, *claims])
    await test_db.flush()
    test_db.add_all(
        [
            MemoirStoryClaim(
                project_id=project_id,
                story_id=story.id,
                claim_id=claim.id,
                narrative_role=MemoirStoryNarrativeRole.ACTION.value,
                sort_order=index,
                is_core=index == 0,
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=current_user["id"],
            )
            for index, claim in enumerate(claims)
        ]
    )
    revision = MemoirStoryRevision(
        project_id=project_id,
        story_id=story.id,
        story_version=1,
        change_type=MemoirStoryRevisionChangeType.CREATED.value,
        before_state_json={},
        after_state_json={"summary": story.summary},
        reason="test fixture",
        created_by=MemoirNarrativeActorType.USER.value,
        created_by_user_id=current_user["id"],
    )
    test_db.add(revision)
    await test_db.commit()
    return {
        "project_id": project_id,
        "user_id": current_user["id"],
        "chapter": chapter,
        "node": node,
        "story": story,
        "revision": revision,
        "claims": claims,
    }


def _claim_source(claim: MemoirClaim) -> MemoirDraftBlockClaimSource:
    return MemoirDraftBlockClaimSource(
        claim_id=claim.id,
        usage_role=MemoirDraftBlockClaimUsageRole.CORE,
    )


def _story_source(created: dict) -> MemoirDraftBlockStorySource:
    return MemoirDraftBlockStorySource(
        story_id=created["story"].id,
        story_revision_id=created["revision"].id,
        usage_role=MemoirDraftBlockStoryUsageRole.PRIMARY,
    )


async def test_factual_block_keeps_sources_across_manual_versions_and_legacy_unlock(
    client,
    test_db,
):
    created = await _create_narrative_sources(client, test_db)
    claim_source = _claim_source(created["claims"][0])
    story_source = _story_source(created)
    claim_id = claim_source.claim_id
    revision_id = story_source.story_revision_id
    node_id = created["node"].id
    with pytest.raises(ValueError, match="requires direct story and claim sources"):
        await draft_blocks.create_block(
            test_db,
            project_id=created["project_id"],
            chapter_id=created["chapter"].id,
            node_id=created["node"].id,
            block_role=MemoirDraftBlockRole.FACTUAL,
            sort_order=0,
            content="没有 Claim 来源的正文不能进入结构化稿。",
            created_by=MemoirNarrativeActorType.AI,
            claims=[],
            stories=[story_source],
        )
    block = await draft_blocks.create_block(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
        node_id=created["node"].id,
        block_role=MemoirDraftBlockRole.FACTUAL,
        sort_order=0,
        content="十八岁那年，我第一次离开家乡。",
        created_by=MemoirNarrativeActorType.AI,
        claims=[claim_source],
        stories=[story_source],
        model="test-model",
    )
    await test_db.commit()
    block_id = block.id
    initial_version_id = block.current_version_id
    audit_context = await load_chapter_audit_context(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
    )
    assert [item.id for item in audit_context.draft_blocks] == [block.id]
    assert audit_context.draft_blocks[0].content == "十八岁那年，我第一次离开家乡。"
    block.locked = True
    test_db.add(block)
    await test_db.commit()

    try:
        await draft_blocks.append_version(
            test_db,
            block=block,
            expected_current_version_id=str(initial_version_id),
            content="后台不应覆盖这段文字。",
            created_by=MemoirNarrativeActorType.AI,
            claims=[claim_source],
            stories=[story_source],
            sources_refreshed=True,
        )
        raise AssertionError("locked block accepted an automatic revision")
    except ValueError as exc:
        assert "locked draft block" in str(exc)

    stale_unlock = await client.post(
        f"/api/memoir/projects/{created['project_id']}/draft-blocks/{block_id}/unlock",
        json={
            "idempotency_key": "unlock-stale-version",
            "reason": "simulated race",
            "expected_current_version_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert stale_unlock.status_code == 409

    unlocked = await client.post(
        f"/api/memoir/projects/{created['project_id']}/draft-blocks/{block_id}/unlock",
        json={
            "idempotency_key": "unlock-block-1",
            "reason": "迁移到整章确认",
            "expected_current_version_id": initial_version_id,
        },
    )
    assert unlocked.status_code == 200, unlocked.text
    current_version_id = unlocked.json()["current_version_id"]
    edited = await client.post(
        f"/api/memoir/projects/{created['project_id']}/draft-blocks/{block_id}/manual-revisions",
        json={
            "expected_current_version_id": current_version_id,
            "content": "十八岁那年，我第一次离开家乡，到成都读书。",
            "feedback": "补上去成都的目的",
        },
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["node_id"] == node_id
    assert "legacy_draft_id" not in body
    assert "legacy_draft_version_id" not in body["current_version"]
    assert body["locked"] is False
    assert body["editorial_status"] == MemoirDraftBlockEditorialStatus.DRAFT.value
    assert body["version_count"] == 2
    assert body["current_version"]["claims"] == [
        {
            "claim_id": claim_id,
            "usage_role": MemoirDraftBlockClaimUsageRole.CORE.value,
        }
    ]
    assert body["current_version"]["stories"][0]["story_revision_id"] == revision_id

    history = list(
        (
            await test_db.scalars(
                select(MemoirDraftBlockVersion)
                .where(MemoirDraftBlockVersion.block_id == block_id)
                .order_by(MemoirDraftBlockVersion.version.desc())
            )
        ).all()
    )
    assert [item.version for item in history] == [2, 1]

    with pytest.raises(IntegrityError, match="draft block versions are immutable"):
        await test_db.execute(
            update(MemoirDraftBlockVersion)
            .where(MemoirDraftBlockVersion.id == history[-1].id)
            .values(content="试图篡改旧版本")
        )
    await test_db.rollback()


async def test_claim_staleness_only_marks_current_versions_with_that_claim(
    client,
    test_db,
):
    created = await _create_narrative_sources(client, test_db)
    blocks = []
    for index, claim in enumerate(created["claims"]):
        blocks.append(
            await draft_blocks.create_block(
                test_db,
                project_id=created["project_id"],
                chapter_id=created["chapter"].id,
                node_id=created["node"].id,
                block_role=MemoirDraftBlockRole.FACTUAL,
                sort_order=index,
                content=claim.statement,
                created_by=MemoirNarrativeActorType.AI,
                claims=[_claim_source(claim)],
                stories=[_story_source(created)],
            )
        )
    blocks[0].editorial_status = MemoirDraftBlockEditorialStatus.CONFIRMED.value
    blocks[0].locked = True
    test_db.add(blocks[0])
    await test_db.commit()

    changed = await draft_blocks.mark_blocks_stale_for_claim(
        test_db,
        project_id=created["project_id"],
        claim_id=created["claims"][0].id,
    )
    await test_db.commit()
    assert changed == 1

    await test_db.refresh(blocks[0])
    await test_db.refresh(blocks[1])
    assert blocks[0].freshness_status == MemoirDraftBlockFreshnessStatus.STALE.value
    assert blocks[0].editorial_status == MemoirDraftBlockEditorialStatus.CONFIRMED.value
    assert blocks[0].locked is True
    assert blocks[1].freshness_status == MemoirDraftBlockFreshnessStatus.CURRENT.value
