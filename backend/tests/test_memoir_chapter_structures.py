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
    MemoirFollowupTaskStatus,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryRevisionChangeType,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookImagePlacementType,
    MemoirBookPlanChapterStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirImageAssetSourceType,
    MemoirImageAssetStatus,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookImagePlacement,
    MemoirBookPlanChapter,
    MemoirChapterSourceLink,
    MemoirEditingJob,
    MemoirImageAsset,
)
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirNarrativeDecision,
    MemoirFollowupTask,
    MemoirNarrativeGap,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStoryRevision,
)
from app.services.memoir.narrative.chapter_structure_backfill import (
    run_chapter_structure_backfill,
)
from app.services.memoir.narrative.cutover_checks import project_migration_report
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
    narrative_role: MemoirStoryNarrativeRole = MemoirStoryNarrativeRole.ACTION,
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
                narrative_role=narrative_role.value,
                sort_order=0,
                is_core=is_core,
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
    return story, claim


async def test_excluding_story_is_contract_scoped_audited_and_keeps_story(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一次离家"],
    )
    excluded_story, _ = await _create_ready_story(
        test_db,
        created=created,
        title="离家前夜",
    )
    retained_story, _ = await _create_ready_story(
        test_db,
        created=created,
        title="抵达成都",
    )
    draft = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/contract/revisions",
        json=_draft_payload(
            "exclude-story-contract",
            objective="讲清离家和抵达的经过。",
            include_story_ids=[excluded_story.id, retained_story.id],
        ),
    )
    assert draft.status_code == 200, draft.text
    contract_id = draft.json()["id"]

    node_ids: list[str] = []
    for index, story in enumerate([excluded_story, retained_story]):
        node = await client.post(
            f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/nodes",
            json={
                "idempotency_key": f"story-node-{index}",
                "node_type": "story",
                "title": story.title,
                "purpose": f"讲述{story.title}。",
                "required": True,
            },
        )
        assert node.status_code == 200, node.text
        node_id = node.json()["nodes"][-1]["id"]
        node_ids.append(node_id)
        linked = await client.post(
            f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/nodes/{node_id}/stories",
            json={
                "story_id": story.id,
                "story_role": "primary",
                "sort_order": 0,
                "required": True,
            },
        )
        assert linked.status_code == 200, linked.text

    decision_payload = {
        "idempotency_key": "exclude-story-from-current-chapter",
        "reason": "这段经历放到下一章",
    }
    excluded = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/stories/{excluded_story.id}/exclude",
        json=decision_payload,
    )
    assert excluded.status_code == 200, excluded.text
    contract = excluded.json()
    assert excluded_story.id not in contract["constraints"]["include_story_ids"]
    assert excluded_story.id in contract["constraints"]["exclude_story_ids"]
    excluded_node = next(node for node in contract["nodes"] if node["id"] == node_ids[0])
    assert excluded_node["stories"] == []
    assert excluded_node["required"] is False
    assert excluded_node["status"] == "excluded"

    persisted_story = await test_db.get(MemoirStory, excluded_story.id)
    assert persisted_story is not None
    assert persisted_story.lifecycle_status == MemoirStoryLifecycleStatus.ACTIVE.value
    assert persisted_story.verification_status == MemoirStoryVerificationStatus.CONFIRMED.value
    assert persisted_story.publication_status == MemoirStoryPublicationStatus.ELIGIBLE.value
    decisions = list(
        (
            await test_db.scalars(
                select(MemoirNarrativeDecision).where(
                    MemoirNarrativeDecision.project_id == project_id,
                    MemoirNarrativeDecision.idempotency_key
                    == decision_payload["idempotency_key"],
                )
            )
        ).all()
    )
    assert len(decisions) == 1
    assert decisions[0].target_type == "chapter_contract"
    assert decisions[0].target_id == contract_id
    assert decisions[0].action == "exclude"
    assert decisions[0].after_state_json["excluded_story_id"] == excluded_story.id

    replay = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/stories/{excluded_story.id}/exclude",
        json=decision_payload,
    )
    assert replay.status_code == 200, replay.text
    assert await test_db.scalar(
        select(func.count()).select_from(MemoirNarrativeDecision).where(
            MemoirNarrativeDecision.project_id == project_id,
            MemoirNarrativeDecision.idempotency_key
            == decision_payload["idempotency_key"],
        )
    ) == 1

    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    immutable = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/stories/{retained_story.id}/exclude",
        json={
            "idempotency_key": "exclude-after-confirmation",
            "reason": "不应原地修改",
        },
    )
    assert immutable.status_code == 409


async def test_story_can_move_between_draft_nodes_without_recreating_its_link(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一次离家"],
    )
    story, _ = await _create_ready_story(test_db, created=created)
    draft = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/contract/revisions",
        json=_draft_payload(
            "move-story-contract",
            objective="按时间顺序整理离家经历。",
            include_story_ids=[story.id],
        ),
    )
    contract_id = draft.json()["id"]
    node_ids: list[str] = []
    for index, title in enumerate(["离家前", "离家后"]):
        response = await client.post(
            f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/nodes",
            json={
                "idempotency_key": f"move-story-node-{index}",
                "node_type": "story",
                "title": title,
                "purpose": f"讲述{title}的经历。",
                "required": True,
            },
        )
        assert response.status_code == 200, response.text
        node_ids.append(response.json()["nodes"][-1]["id"])
    linked = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/nodes/{node_ids[0]}/stories",
        json={
            "story_id": story.id,
            "story_role": "primary",
            "sort_order": 0,
            "required": True,
        },
    )
    link_id = linked.json()["nodes"][0]["stories"][0]["id"]

    moved = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/stories/{story.id}/move",
        json={
            "source_node_id": node_ids[0],
            "target_node_id": node_ids[1],
        },
    )

    assert moved.status_code == 200, moved.text
    nodes = {node["id"]: node for node in moved.json()["nodes"]}
    assert nodes[node_ids[0]]["stories"] == []
    assert nodes[node_ids[0]]["required"] is False
    assert nodes[node_ids[0]]["status"] == "excluded"
    assert nodes[node_ids[1]]["stories"] == [
        {
            "id": link_id,
            "story_id": story.id,
            "story_role": "primary",
            "sort_order": 0,
            "required": True,
        }
    ]


async def test_story_merge_rewires_draft_contract_and_collapses_duplicate_node(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["搬家往事", "旧稿快照"],
    )
    source, _ = await _create_ready_story(
        test_db,
        created=created,
        title="搬家片段",
    )
    retained, _ = await _create_ready_story(
        test_db,
        created=created,
        title="搬到成都",
    )
    historical = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[1].id}/contract/revisions",
        json=_draft_payload(
            "merge-story-confirmed-history",
            objective="保留合并前已经确认的章节结构。",
            include_story_ids=[source.id],
        ),
    )
    historical_contract_id = historical.json()["id"]
    historical_node = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{historical_contract_id}/nodes",
        json={
            "idempotency_key": "merge-story-confirmed-node",
            "node_type": "story",
            "title": source.title,
            "purpose": "记录当时确认使用的故事版本。",
            "required": True,
        },
    )
    historical_node_id = historical_node.json()["nodes"][-1]["id"]
    historical_link = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{historical_contract_id}/nodes/{historical_node_id}/stories",
        json={
            "story_id": source.id,
            "story_role": "primary",
            "sort_order": 0,
            "required": True,
        },
    )
    assert historical_link.status_code == 200, historical_link.text
    historical_confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{historical_contract_id}/confirm"
    )
    assert historical_confirmed.status_code == 200, historical_confirmed.text

    draft = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/contract/revisions",
        json=_draft_payload(
            "merge-story-contract",
            objective="合并重复片段后讲清搬家经过。",
            include_story_ids=[source.id, retained.id],
        ),
    )
    contract_id = draft.json()["id"]
    node_ids: list[str] = []
    for index, story in enumerate([source, retained]):
        node = await client.post(
            f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/nodes",
            json={
                "idempotency_key": f"merge-story-node-{index}",
                "node_type": "story",
                "title": story.title,
                "purpose": f"讲述{story.title}。",
                "required": True,
            },
        )
        node_id = node.json()["nodes"][-1]["id"]
        node_ids.append(node_id)
        linked = await client.post(
            f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}/nodes/{node_id}/stories",
            json={
                "story_id": story.id,
                "story_role": "primary",
                "sort_order": 0,
                "required": True,
            },
        )
        assert linked.status_code == 200, linked.text

    merged = await client.post(
        f"/api/memoir/projects/{project_id}/stories/{source.id}/merge",
        json={
            "retained_story_id": retained.id,
            "idempotency_key": "merge-duplicate-draft-story",
            "reason": "项目所有者确认是同一段经历",
        },
    )

    assert merged.status_code == 200, merged.text
    contract = (
        await client.get(
            f"/api/memoir/projects/{project_id}/chapter-contracts/{contract_id}"
        )
    ).json()
    nodes = {node["id"]: node for node in contract["nodes"]}
    assert nodes[node_ids[0]]["stories"] == []
    assert nodes[node_ids[0]]["required"] is False
    assert nodes[node_ids[0]]["status"] == "excluded"
    assert nodes[node_ids[1]]["stories"][0]["story_id"] == retained.id
    assert contract["constraints"]["include_story_ids"] == [retained.id]
    historical_after_merge = (
        await client.get(
            f"/api/memoir/projects/{project_id}/chapter-contracts/{historical_contract_id}"
        )
    ).json()
    assert historical_after_merge["status"] == "confirmed"
    assert historical_after_merge["nodes"][0]["id"] == historical_node_id
    assert historical_after_merge["nodes"][0]["stories"][0]["story_id"] == source.id
    await test_db.refresh(source)
    assert source.lifecycle_status == MemoirStoryLifecycleStatus.MERGED.value
    assert source.merged_into_story_id == retained.id


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
    story, claim = await _create_ready_story(test_db, created=created)
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
        "framing_node_has_no_supported_claim",
        "required_node_not_ready",
    }

    reflection_node = next(
        node for node in contract["nodes"] if node["node_type"] == "reflection"
    )
    excluded_reflection = await client.patch(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}"
        f"/nodes/{reflection_node['id']}",
        json={"required": False, "status": "excluded"},
    )
    assert excluded_reflection.status_code == 200, excluded_reflection.text

    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert all(
        node["status"] == "ready"
        for node in confirmed.json()["nodes"]
        if node["required"]
    )
    closing_node = next(
        node for node in confirmed.json()["nodes"] if node["node_type"] == "closing"
    )
    assert closing_node["status"] == "planned"
    validated = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/generation-validation"
    )
    assert validated.status_code == 200
    assert validated.json()["can_generate"] is True
    assert validated.json()["issues"] == []

    rejected_story = await client.post(
        f"/api/memoir/projects/{project_id}/stories/{story_id}/reject",
        json={"idempotency_key": "reject-required-story", "reason": "核对后暂不采用"},
    )
    assert rejected_story.status_code == 200, rejected_story.text
    rejected_contract = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}"
    )
    rejected_story_node = next(
        node for node in rejected_contract.json()["nodes"]
        if node["node_type"] == "story"
    )
    assert rejected_story_node["status"] == "needs_work"

    reconfirmed_story = await client.post(
        f"/api/memoir/projects/{project_id}/stories/{story_id}/confirm",
        json={"idempotency_key": "reconfirm-required-story", "reason": "重新核对证据后确认"},
    )
    assert reconfirmed_story.status_code == 200, reconfirmed_story.text
    reconfirmed_contract = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}"
    )
    reconfirmed_story_node = next(
        node for node in reconfirmed_contract.json()["nodes"]
        if node["node_type"] == "story"
    )
    assert reconfirmed_story_node["status"] == "ready"

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
        .values(
            publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
            verification_status=MemoirStoryVerificationStatus.DISPUTED.value,
        )
    )
    await test_db.commit()
    disputed_story_validation = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}/generation-validation"
    )
    assert disputed_story_validation.status_code == 200
    assert disputed_story_validation.json()["can_generate"] is False
    assert "linked_story_ineligible" in {
        issue["code"] for issue in disputed_story_validation.json()["issues"]
    }

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

    rejected_claim = await client.post(
        f"/api/memoir/projects/{project_id}/claims/{claim.id}/reject",
        json={
            "idempotency_key": "reject-required-story-claim",
            "reason": "原始事实有误",
        },
    )
    assert rejected_claim.status_code == 200, rejected_claim.text
    claim_rejected_contract = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contract['id']}"
    )
    claim_rejected_story_node = next(
        node
        for node in claim_rejected_contract.json()["nodes"]
        if node["node_type"] == "story"
    )
    assert claim_rejected_story_node["status"] == "needs_work"


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
        source_refs_by_chapter=[[memoir_source_ref(created)], []],
    )
    first_chapter_id = chapters[0].id
    story, _ = await _create_ready_story(test_db, created=created)
    story_id = story.id
    contracts = []
    for index, chapter in enumerate(chapters):
        contract_payload = _draft_payload(
            f"structure-{index}",
            objective=f"组织第 {index + 1} 章。",
            include_story_ids=[story_id] if index == 0 else [],
        )
        contract_payload["core_questions"] = [
            {
                "key": f"chapter_{index + 1}",
                "question": f"第 {index + 1} 章必须回答什么？",
            }
        ]
        response = await client.post(
            f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/contract/revisions",
            json=contract_payload,
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

    stale_gap = MemoirNarrativeGap(
        project_id=project_id,
        scope_type=MemoirNarrativeGapScopeType.CHAPTER.value,
        primary_chapter_id=chapters[0].id,
        gap_type=MemoirNarrativeGapType.MISSING_SLOT.value,
        severity=MemoirNarrativeGapSeverity.HIGH.value,
        target_slot="chapter_contract",
        title="拆分前的章节问题",
        description="章节结构变化后需要重新审计。",
        fingerprint="s" * 64,
        detected_by=MemoirNarrativeGapDetectedBy.RULES.value,
    )
    test_db.add(stale_gap)
    await test_db.flush()
    stale_task = MemoirFollowupTask(
        project_id=project_id,
        gap_id=stale_gap.id,
        chapter_id=chapters[0].id,
        question="这段经历还缺少什么？",
        purpose="补齐拆分前识别出的章节问题。",
        priority=80,
        status=MemoirFollowupTaskStatus.PENDING.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(stale_task)
    await test_db.commit()

    story_count = await test_db.scalar(select(func.count()).select_from(MemoirStory))
    claim_count = await test_db.scalar(select(func.count()).select_from(MemoirClaim))
    link_id = await test_db.scalar(
        select(MemoirChapterNodeStory.id).where(
            MemoirChapterNodeStory.node_id == story_node_id
        )
    )
    split_payload = {
        "idempotency_key": "split-departure-chapter",
        "source_contract_id": contracts[0]["id"],
        "node_ids": [story_node_id],
        "target_title": "第一次离家",
        "target_objective": "完整讲述第一次离家的经过。",
        "target_reader_takeaway": "读者理解离家如何改变了讲述者。",
        "target_time_scope_text": "十八岁第一次离家期间",
        "target_core_questions": [
            {"key": "departure", "question": "为什么离家，又经历了什么？"}
        ],
    }
    split = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/split",
        json=split_payload,
    )
    assert split.status_code == 200, split.text
    split_body = split.json()
    split_contract = split_body["target"]
    split_chapter_id = split_contract["chapter_id"]
    assert split_chapter_id not in {chapter.id for chapter in chapters}
    assert story_node_id in {node["id"] for node in split_contract["nodes"]}
    assert story_id not in split_body["source"]["constraints"]["include_story_ids"]
    assert story_id in split_contract["constraints"]["include_story_ids"]
    assert await test_db.scalar(select(func.count()).select_from(MemoirStory)) == story_count
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaim)) == claim_count
    assert await test_db.scalar(
        select(MemoirChapterNodeStory.id).where(
            MemoirChapterNodeStory.node_id == story_node_id
        )
    ) == link_id
    moved_node = await test_db.get(MemoirChapterNode, story_node_id)
    assert moved_node is not None
    assert moved_node.chapter_id == split_chapter_id
    assert moved_node.contract_id == split_contract["id"]

    plan_after_split = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-plan"
    )
    assert plan_after_split.status_code == 200
    assert [chapter["title"] for chapter in plan_after_split.json()["chapters"]] == [
        "童年",
        "第一次离家",
        "离家",
    ]
    copied_source = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.chapter_id == split_chapter_id,
            MemoirChapterSourceLink.utterance_id == created["utterance_id"],
        )
    )
    assert copied_source is not None
    assert copied_source.status == "active"
    assert copied_source.assigned_by == "user"
    await test_db.refresh(stale_gap)
    await test_db.refresh(stale_task)
    assert stale_gap.status == "resolved"
    assert "拆分" in stale_gap.resolution_note
    assert stale_task.status == MemoirFollowupTaskStatus.CANCELLED.value
    assert stale_task.completed_at is not None

    repeated_split = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/split",
        json=split_payload,
    )
    assert repeated_split.status_code == 200, repeated_split.text
    assert repeated_split.json()["target"]["id"] == split_contract["id"]
    assert await test_db.scalar(
        select(func.count()).select_from(MemoirBookPlanChapter)
    ) == 3

    confirmed_split_source = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[0]['id']}/confirm"
    )
    assert confirmed_split_source.status_code == 200, confirmed_split_source.text
    repeated_after_source_confirmation = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/split",
        json=split_payload,
    )
    assert repeated_after_source_confirmation.status_code == 200
    assert repeated_after_source_confirmation.json()["target"]["id"] == split_contract["id"]
    assert await test_db.scalar(
        select(func.count()).select_from(MemoirBookPlanChapter)
    ) == 3

    image = MemoirImageAsset(
        project_id=project_id,
        user_id=created["user_id"],
        status=MemoirImageAssetStatus.READY.value,
        source_type=MemoirImageAssetSourceType.MANUAL_UPLOAD.value,
        source_signature="split-chapter-image",
    )
    test_db.add(image)
    await test_db.flush()
    placement = MemoirBookImagePlacement(
        project_id=project_id,
        image_asset_id=image.id,
        placement_type=MemoirBookImagePlacementType.CHAPTER_OPENING.value,
        chapter_id=split_chapter_id,
    )
    pending_review = MemoirAutomationEvent(
        project_id=project_id,
        chapter_id=split_chapter_id,
        event_type=MemoirAutomationEventType.SOURCE_HELD.value,
        status=MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        utterance_ids_json=[created["utterance_id"]],
        summary_text="建议归入拆分出的章节。",
        metadata_json={
            "route": {
                "target_chapter_id": split_chapter_id,
                "target_chapter_title": "第一次离家",
            }
        },
    )
    pending_job = MemoirEditingJob(
        project_id=project_id,
        chapter_id=split_chapter_id,
        job_type=MemoirEditingJobType.RECOMPUTE_CHAPTER_STRUCTURE_AUDIT.value,
        status=MemoirEditingJobStatus.PENDING.value,
        run_after=datetime.now(UTC).replace(tzinfo=None),
    )
    test_db.add_all([placement, pending_review, pending_job])
    await test_db.commit()

    removed_target_link = MemoirChapterSourceLink(
        project_id=project_id,
        plan_id=chapters[1].plan_id,
        chapter_id=chapters[1].id,
        utterance_id=created["utterance_id"],
        status="removed",
        assigned_by="user",
        assignment_reason="previously_removed",
        quote="过期的来源摘录",
        merged_draft_version_id="old-draft-version",
        removed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    test_db.add(removed_target_link)
    await test_db.commit()
    removed_target_link_id = removed_target_link.id

    conflicting_merge_payload = {
        "idempotency_key": "merge-departure-chapter",
        "source_contract_id": split_contract["id"],
        "target_chapter_id": chapters[1].id,
    }
    duplicate_primary = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/merge",
        json=conflicting_merge_payload,
    )
    assert duplicate_primary.status_code == 409
    split_chapter = await test_db.get(MemoirBookPlanChapter, split_chapter_id)
    assert split_chapter is not None
    assert split_chapter.status == "active"
    assert await test_db.scalar(
        select(MemoirChapterNode.contract_id).where(
            MemoirChapterNode.id == story_node_id
        )
    ) == split_contract["id"]

    detached = await client.delete(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[1]['id']}/nodes/{target_story_node_id}/stories/{story_id}"
    )
    assert detached.status_code == 200

    merged = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/merge",
        json=conflicting_merge_payload,
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["source"]["status"] == "superseded"
    assert merged.json()["source"]["nodes"] == []
    assert len(merged.json()["target"]["nodes"]) == 3
    assert {
        question["key"] for question in merged.json()["target"]["core_questions"]
    } == {"chapter_2", "departure"}
    assert merged.json()["target"]["constraints"]["include_story_ids"] == [story_id]
    assert merged.json()["target"]["time_scope_text"] == "十八岁第一次离家期间"
    await test_db.refresh(split_chapter)
    assert split_chapter.status == "archived"
    assert split_chapter.archived_sort_order == 1
    assert split_chapter.archived_at is not None
    assert split_chapter.archived_by_user_id == created["user_id"]
    assert split_chapter.merged_into_chapter_id == chapters[1].id
    await test_db.refresh(placement)
    await test_db.refresh(pending_review)
    await test_db.refresh(pending_job)
    assert placement.chapter_id == chapters[1].id
    assert pending_review.chapter_id == chapters[1].id
    assert pending_review.metadata_json["route"]["target_chapter_id"] == chapters[1].id
    assert pending_job.status == MemoirEditingJobStatus.SKIPPED.value
    assert pending_job.result_json["merged_into_chapter_id"] == chapters[1].id
    assert await test_db.scalar(select(func.count()).select_from(MemoirStory)) == story_count
    assert await test_db.scalar(select(func.count()).select_from(MemoirClaim)) == claim_count
    assert await test_db.scalar(
        select(MemoirChapterNodeStory.id).where(
            MemoirChapterNodeStory.node_id == story_node_id
        )
    ) == link_id

    plan_after_merge = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-plan"
    )
    assert plan_after_merge.status_code == 200
    assert [chapter["id"] for chapter in plan_after_merge.json()["chapters"]] == [
        chapters[0].id,
        chapters[1].id,
    ]
    assert [chapter["sort_order"] for chapter in plan_after_merge.json()["chapters"]] == [
        0,
        1,
    ]
    merged_source = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.chapter_id == chapters[1].id,
            MemoirChapterSourceLink.utterance_id == created["utterance_id"],
        )
    )
    assert merged_source is not None
    assert merged_source.id == removed_target_link_id
    assert merged_source.status == "active"
    assert merged_source.transcript_version_id == created["transcript_version_id"]
    assert merged_source.quote != "过期的来源摘录"
    assert merged_source.removed_at is None
    assert merged_source.merged_draft_version_id is None

    repeated_merge = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/merge",
        json=conflicting_merge_payload,
    )
    assert repeated_merge.status_code == 200, repeated_merge.text
    assert repeated_merge.json()["target"]["id"] == merged.json()["target"]["id"]
    migration_report = await project_migration_report(test_db, project_id=project_id)
    assert migration_report["chapter_count"] == 2

    second_merge = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/merge",
        json={
            "idempotency_key": "merge-retained-chapter-again",
            "source_contract_id": merged.json()["target"]["id"],
            "target_chapter_id": first_chapter_id,
        },
    )
    assert second_merge.status_code == 200, second_merge.text
    assert {
        question["key"] for question in second_merge.json()["target"]["core_questions"]
    } == {"chapter_1", "chapter_2", "departure"}
    await test_db.refresh(split_chapter)
    await test_db.refresh(chapters[1])
    await test_db.refresh(placement)
    assert split_chapter.status == MemoirBookPlanChapterStatus.ARCHIVED.value
    assert chapters[1].status == MemoirBookPlanChapterStatus.ARCHIVED.value
    assert split_chapter.merged_into_chapter_id == first_chapter_id
    assert chapters[1].merged_into_chapter_id == first_chapter_id
    assert placement.chapter_id == first_chapter_id

    final_plan = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-plan")
    assert final_plan.status_code == 200
    assert [chapter["id"] for chapter in final_plan.json()["chapters"]] == [
        first_chapter_id
    ]
    final_migration_report = await project_migration_report(test_db, project_id=project_id)
    assert final_migration_report["chapter_count"] == 1

    repeated_first_merge = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/merge",
        json=conflicting_merge_payload,
    )
    assert repeated_first_merge.status_code == 200, repeated_first_merge.text
    assert repeated_first_merge.json()["target"]["chapter_id"] == first_chapter_id

    repeated_split_after_merge = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/split",
        json=split_payload,
    )
    assert repeated_split_after_merge.status_code == 200, repeated_split_after_merge.text
    assert repeated_split_after_merge.json()["target"]["chapter_id"] == first_chapter_id


async def test_merge_into_confirmed_chapter_creates_draft_without_mutating_confirmed_structure(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["乡村童年", "求学生涯"],
    )
    contracts: list[dict] = []
    node_ids: list[str] = []
    for index, chapter in enumerate(chapters):
        contract = await client.post(
            f"/api/memoir/projects/{project_id}/chapters/{chapter.id}/contract/revisions",
            json=_draft_payload(
                f"confirmed-target-contract-{index}",
                objective=f"组织第 {index + 1} 章。",
            ),
        )
        assert contract.status_code == 200, contract.text
        contracts.append(contract.json())
        node = await client.post(
            f"/api/memoir/projects/{project_id}/chapter-contracts/{contract.json()['id']}/nodes",
            json={
                "idempotency_key": f"confirmed-target-node-{index}",
                "node_type": "opening",
                "title": f"第 {index + 1} 章开场",
                "purpose": "用具体场景进入本章。",
                "required": True,
                "status": "ready",
            },
        )
        assert node.status_code == 200, node.text
        node_ids.append(node.json()["nodes"][0]["id"])

    confirmed = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[1]['id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text

    merged = await client.post(
        f"/api/memoir/projects/{project_id}/chapter-structures/merge",
        json={
            "idempotency_key": "merge-into-confirmed-target",
            "source_contract_id": contracts[0]["id"],
            "target_chapter_id": chapters[1].id,
        },
    )
    assert merged.status_code == 200, merged.text
    target = merged.json()["target"]
    assert target["status"] == "draft"
    assert target["version"] == 2
    assert target["id"] != contracts[1]["id"]
    assert node_ids[0] in {node["id"] for node in target["nodes"]}
    assert node_ids[1] not in {node["id"] for node in target["nodes"]}

    original = await client.get(
        f"/api/memoir/projects/{project_id}/chapter-contracts/{contracts[1]['id']}"
    )
    assert original.status_code == 200, original.text
    assert original.json()["status"] == "confirmed"
    assert [node["id"] for node in original.json()["nodes"]] == [node_ids[1]]
    moved_source_node = await test_db.get(MemoirChapterNode, node_ids[0])
    original_target_node = await test_db.get(MemoirChapterNode, node_ids[1])
    assert moved_source_node is not None
    assert moved_source_node.contract_id == target["id"]
    assert moved_source_node.chapter_id == chapters[1].id
    assert original_target_node is not None
    assert original_target_node.contract_id == contracts[1]["id"]
    assert original_target_node.chapter_id == chapters[1].id


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

    migration = await project_migration_report(test_db, project_id=project_id)
    assert "chapter contracts still require user confirmation" not in migration[
        "cutover_data_blockers"
    ]
    assert migration["chapter_workflow_ready"] is False
    assert migration["chapter_workflow_blockers"] == [
        "chapter contracts still require user confirmation",
        "chapters do not all have structured draft blocks",
    ]


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
    reflection_story, _ = await _create_ready_story(
        test_db,
        created=created,
        title="回望基层工作经历",
        is_core=False,
        narrative_role=MemoirStoryNarrativeRole.REFLECTION,
        verification_status=MemoirStoryVerificationStatus.NEEDS_VERIFICATION,
    )
    confirmable_story.updated_at = datetime(2026, 1, 1)
    non_core_story.updated_at = confirmable_story.updated_at + timedelta(seconds=1)
    reflection_story.updated_at = non_core_story.updated_at + timedelta(seconds=1)
    test_db.add_all([confirmable_story, non_core_story, reflection_story])
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
    primary_links_by_story = {
        link.story_id: link for link in links if link.story_role == "primary"
    }
    assert primary_links_by_story[confirmable_story.id].required is True
    assert (
        nodes_by_id[primary_links_by_story[confirmable_story.id].node_id].required
        is True
    )
    assert primary_links_by_story[non_core_story.id].required is False
    assert (
        nodes_by_id[primary_links_by_story[non_core_story.id].node_id].required
        is False
    )
    framing_links = [link for link in links if link.story_role == "supporting"]
    assert len(framing_links) == 3
    assert all(not link.required for link in framing_links)
    framing_story_by_type = {
        nodes_by_id[link.node_id].node_type: link.story_id for link in framing_links
    }
    assert framing_story_by_type == {
        "opening": confirmable_story.id,
        "reflection": reflection_story.id,
        "closing": reflection_story.id,
    }

    contract.constraints_json = {
        **contract.constraints_json,
        "include_story_ids": [non_core_story.id],
    }
    primary_links_by_story[confirmable_story.id].required = False
    nodes_by_id[primary_links_by_story[confirmable_story.id].node_id].required = False
    primary_links_by_story[non_core_story.id].required = True
    nodes_by_id[primary_links_by_story[non_core_story.id].node_id].required = True
    await test_db.commit()

    repaired = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    assert repaired["repaired_contract_ids"] == [contract.id]
    await test_db.refresh(contract)
    await test_db.refresh(primary_links_by_story[confirmable_story.id])
    await test_db.refresh(primary_links_by_story[non_core_story.id])
    assert contract.constraints_json["include_story_ids"] == [confirmable_story.id]
    assert primary_links_by_story[confirmable_story.id].required is True
    assert primary_links_by_story[non_core_story.id].required is False

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

    story_link = await test_db.scalar(
        select(MemoirChapterNodeStory).where(
            MemoirChapterNodeStory.story_id == non_core_story.id
        )
    )
    assert story_link is not None
    await test_db.delete(nodes[1])
    contract.constraints_json = {
        **contract.constraints_json,
        "include_story_ids": [non_core_story.id],
    }
    nodes[2].required = True
    story_link.required = True
    test_db.add_all([contract, nodes[2], story_link])
    await test_db.commit()

    repaired = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    assert repaired["repaired_contract_ids"] == [contract.id]
    repaired_nodes = list(
        (
            await test_db.scalars(
                select(MemoirChapterNode)
                .where(MemoirChapterNode.contract_id == contract.id)
                .order_by(MemoirChapterNode.sort_order)
            )
        ).all()
    )
    assert [node.title for node in repaired_nodes[:3]] == [
        "开场",
        "待补充核心故事",
        non_core_story.title,
    ]
    assert repaired_nodes[1].required is True
    assert repaired_nodes[2].required is False
    await test_db.refresh(story_link)
    assert story_link.required is False

    repaired_nodes[2].updated_by = MemoirNarrativeActorType.USER.value
    repaired_nodes[2].updated_by_user_id = created["user_id"]
    test_db.add(repaired_nodes[2])
    await test_db.commit()
    skipped = await run_chapter_structure_backfill(
        test_db,
        project_id=project_id,
        dry_run=False,
    )
    assert skipped["skipped_modified_contract_ids"] == [contract.id]
