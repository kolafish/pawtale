from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirChapterNodeStatus,
    MemoirChapterNodeStoryRole,
    MemoirChapterNodeType,
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
    MemoirStoryRevisionChangeType,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import MemoirBookChapterDraftStatus, MemoirEditingJobStatus
from app.models.memoir import MemoirBookChapterDraft, MemoirBookChapterDraftVersion
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirDraftBlock,
    MemoirDraftBlockVersion,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStoryRevision,
)
from app.schemas.memoir_narrative import (
    MemoirDraftBlockGenerationRequest,
    MemoirDraftBlockRevisionApplyRequest,
    MemoirDraftBlockRevisionJobRequest,
)
from app.services.memoir.narrative import draft_assembly, draft_blocks, draft_generation_jobs
from tests.memoir_helpers import create_test_book_plan_with_chapters, ensure_mobile_memoir_project


class FakeDraftAI:
    provider_name = "test"
    model_name = "test-draft-model"

    def __init__(self, responses: list[dict[str, object]]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def _call_llm(self, system_prompt: str, user_prompt: str, **_: object) -> str:
        self.prompts.append(user_prompt)
        if not self.responses:
            raise AssertionError("unexpected draft block AI call")
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


async def _create_generation_fixture(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    user_id = (await client.get("/api/auth/me")).json()["id"]
    plan, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        title="离家往事",
        chapter_titles=["第一次离家"],
    )
    chapter = chapters[0]
    contract = MemoirChapterContract(
        project_id=project_id,
        chapter_id=chapter.id,
        version=1,
        objective="写清两次关键行动及其影响。",
        reader_takeaway="理解讲述者如何适应新的生活。",
        time_scope_text="青年时期",
        core_questions_json=[],
        constraints_json={"exclude_topics": ["未经证实的细节"]},
        status=MemoirChapterContractStatus.DRAFT.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(contract)
    await test_db.flush()

    node_titles = ["离开家乡", "住进宿舍"]
    statements = ["十八岁那年，我离开家乡去成都。", "到成都后，我住进了学校宿舍。"]
    evidence_notes = ["十八岁那年我去成都读书。", "到成都以后住的是学校宿舍。"]
    nodes: list[MemoirChapterNode] = []
    stories: list[MemoirStory] = []
    claims: list[MemoirClaim] = []
    revisions: list[MemoirStoryRevision] = []
    for index, (title, statement, evidence_note) in enumerate(
        zip(node_titles, statements, evidence_notes, strict=True)
    ):
        node = MemoirChapterNode(
            project_id=project_id,
            chapter_id=chapter.id,
            contract_id=contract.id,
            node_type=MemoirChapterNodeType.STORY.value,
            title=title,
            purpose=f"写清{title}的经过。",
            sort_order=index,
            required=True,
            status=MemoirChapterNodeStatus.READY.value,
            created_by=MemoirNarrativeActorType.SYSTEM.value,
            updated_by=MemoirNarrativeActorType.SYSTEM.value,
        )
        story = MemoirStory(
            project_id=project_id,
            title=title,
            title_normalized=title,
            story_type=MemoirStoryType.EVENT.value,
            summary=statement,
            time_precision=MemoirClaimTimePrecision.YEAR.value,
            lifecycle_status=MemoirStoryLifecycleStatus.ACTIVE.value,
            completion_status=MemoirStoryCompletionStatus.SUFFICIENT.value,
            verification_status=MemoirStoryVerificationStatus.CONFIRMED.value,
            verification_decided_by_user_id=user_id,
            publication_status=MemoirStoryPublicationStatus.ELIGIBLE.value,
            slot_schema_version="1",
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=user_id,
            updated_by=MemoirNarrativeActorType.USER.value,
            updated_by_user_id=user_id,
        )
        claim = MemoirClaim(
            project_id=project_id,
            claim_type=MemoirClaimType.EVENT.value,
            statement=statement,
            predicate_code=MemoirClaimPredicate.NARRATIVE_FACT.value,
            time_precision=MemoirClaimTimePrecision.YEAR.value,
            certainty=MemoirClaimCertainty.EXACT.value,
            verification_status=MemoirClaimVerificationStatus.CONFIRMED.value,
            publication_status=MemoirClaimPublicationStatus.ELIGIBLE.value,
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=user_id,
            updated_by=MemoirNarrativeActorType.USER.value,
            updated_by_user_id=user_id,
        )
        test_db.add_all([node, story, claim])
        await test_db.flush()
        revision = MemoirStoryRevision(
            project_id=project_id,
            story_id=story.id,
            story_version=1,
            change_type=MemoirStoryRevisionChangeType.CREATED.value,
            before_state_json={},
            after_state_json={"summary": story.summary},
            reason="test fixture",
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=user_id,
        )
        test_db.add_all(
            [
                revision,
                MemoirChapterNodeStory(
                    project_id=project_id,
                    node_id=node.id,
                    story_id=story.id,
                    story_role=MemoirChapterNodeStoryRole.PRIMARY.value,
                    sort_order=0,
                    required=True,
                    created_by=MemoirNarrativeActorType.USER.value,
                    created_by_user_id=user_id,
                ),
                MemoirStoryClaim(
                    project_id=project_id,
                    story_id=story.id,
                    claim_id=claim.id,
                    narrative_role=MemoirStoryNarrativeRole.ACTION.value,
                    sort_order=0,
                    is_core=True,
                    created_by=MemoirNarrativeActorType.USER.value,
                    created_by_user_id=user_id,
                ),
                MemoirClaimEvidence(
                    project_id=project_id,
                    claim_id=claim.id,
                    evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
                    evidence_source_type=MemoirClaimEvidenceSourceType.MANUAL.value,
                    evidence_note=evidence_note,
                    created_by=MemoirNarrativeActorType.USER.value,
                    created_by_user_id=user_id,
                ),
            ]
        )
        nodes.append(node)
        stories.append(story)
        claims.append(claim)
        revisions.append(revision)
    await test_db.flush()
    contract.status = MemoirChapterContractStatus.CONFIRMED.value
    contract.confirmed_by_user_id = user_id
    contract.confirmed_at = datetime.now(UTC).replace(tzinfo=None)
    await test_db.commit()
    return {
        "project_id": project_id,
        "plan": plan,
        "chapter": chapter,
        "contract": contract,
        "nodes": nodes,
        "stories": stories,
        "claims": claims,
        "revisions": revisions,
    }


def _ai_output(content: str, claim_id: str) -> dict[str, object]:
    return {
        "content": content,
        "used_claim_ids": [claim_id],
        "unsupported_fact_candidates": [],
    }


async def test_draft_generation_job_routes_enqueue_and_read_scoped_job(client, test_db):
    created = await _create_generation_fixture(client, test_db)
    queued = await client.post(
        (
            f"/api/memoir/projects/{created['project_id']}"
            f"/chapters/{created['chapter'].id}/draft-blocks/generation-jobs"
        ),
        json={"node_ids": [created["nodes"][0].id]},
    )
    assert queued.status_code == 200, queued.text
    body = queued.json()
    assert body["job_type"] == "generate_draft_blocks"
    assert body["status"] == "pending"

    loaded = await client.get(
        f"/api/memoir/projects/{created['project_id']}"
        f"/draft-block-generation-jobs/{body['id']}"
    )
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["id"] == body["id"]


async def test_node_generation_uses_claim_allowlists_and_never_rewrites_existing_blocks(
    client,
    test_db,
    monkeypatch,
):
    created = await _create_generation_fixture(client, test_db)
    request = MemoirDraftBlockGenerationRequest()
    job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
        request=request,
    )
    ai = FakeDraftAI(
        [
            _ai_output("十八岁那年，我离开家乡，前往成都读书。", created["claims"][0].id),
            _ai_output("抵达成都后，我住进了学校宿舍。", created["claims"][1].id),
        ]
    )
    result = await draft_generation_jobs.run_draft_block_generation_job(
        test_db,
        job,
        ai_client=ai,
    )
    await test_db.flush()

    blocks = list(
        (
            await test_db.scalars(
                select(MemoirDraftBlock)
                .where(MemoirDraftBlock.project_id == created["project_id"])
                .order_by(MemoirDraftBlock.sort_order)
            )
        ).all()
    )
    assert len(blocks) == 2
    assert result.result_json["created_block_ids"] == [block.id for block in blocks]
    assert created["claims"][0].statement in ai.prompts[0]
    assert created["claims"][1].statement not in ai.prompts[0]
    assert "transcript" not in ai.prompts[0].lower()

    original_version_ids = [block.current_version_id for block in blocks]
    blocks[0].locked = True
    await test_db.flush()

    def fail_if_provider_is_loaded(*_args, **_kwargs):
        raise AssertionError("provider must not be loaded when every node already has a block")

    monkeypatch.setattr(draft_generation_jobs, "get_ai_service", fail_if_provider_is_loaded)
    repeated = await draft_generation_jobs.run_draft_block_generation_job(
        test_db,
        job,
    )
    assert repeated.result_json["created_block_ids"] == []
    assert repeated.result_json["skipped_node_ids"] == [node.id for node in created["nodes"]]
    assert [block.current_version_id for block in blocks] == original_version_ids


async def test_generation_rejects_provider_claim_outside_node_allowlist(client, test_db):
    created = await _create_generation_fixture(client, test_db)
    job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
        request=MemoirDraftBlockGenerationRequest(node_ids=[created["nodes"][0].id]),
    )
    ai = FakeDraftAI(
        [
            _ai_output(
                "这段文字使用了不属于当前节点的事实。",
                created["claims"][1].id,
            )
        ]
    )
    with pytest.raises(ValueError, match="outside allowlist"):
        await draft_generation_jobs.run_draft_block_generation_job(
            test_db,
            job,
            ai_client=ai,
        )
    count = await test_db.scalar(select(func.count(MemoirDraftBlock.id)))
    assert count == 0


async def test_local_revision_is_a_suggestion_until_applied_and_assembly_keeps_snapshots(
    client,
    test_db,
):
    created = await _create_generation_fixture(client, test_db)
    generation_job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
        request=MemoirDraftBlockGenerationRequest(),
    )
    await draft_generation_jobs.run_draft_block_generation_job(
        test_db,
        generation_job,
        ai_client=FakeDraftAI(
            [
                _ai_output("十八岁那年，我离开家乡。", created["claims"][0].id),
                _ai_output("到成都后，我住进了学校宿舍。", created["claims"][1].id),
            ]
        ),
    )
    await test_db.flush()
    blocks = list(
        (
            await test_db.scalars(
                select(MemoirDraftBlock).order_by(MemoirDraftBlock.sort_order)
            )
        ).all()
    )
    first = blocks[0]
    original_version_id = first.current_version_id
    revision_job = await draft_generation_jobs.enqueue_block_revision(
        test_db,
        project_id=created["project_id"],
        block_id=first.id,
        request=MemoirDraftBlockRevisionJobRequest(
            expected_current_version_id=str(original_version_id),
            feedback="保留第一人称，并把句子写得更自然。",
        ),
    )
    suggestion = await draft_generation_jobs.run_draft_block_generation_job(
        test_db,
        revision_job,
        ai_client=FakeDraftAI(
            [_ai_output("十八岁那年，我第一次独自离开家乡。", created["claims"][0].id)]
        ),
    )
    assert first.current_version_id == original_version_id
    revision_job.status = MemoirEditingJobStatus.SUCCEEDED.value
    revision_job.result_json = suggestion.result_json
    await test_db.flush()

    await draft_generation_jobs.apply_generated_revision(
        test_db,
        project_id=created["project_id"],
        block_id=first.id,
        request=MemoirDraftBlockRevisionApplyRequest(
            expected_current_version_id=str(original_version_id),
            generation_job_id=revision_job.id,
        ),
    )
    assert first.current_version_id != original_version_id
    with pytest.raises(ValueError, match="changed concurrently"):
        await draft_generation_jobs.apply_generated_revision(
            test_db,
            project_id=created["project_id"],
            block_id=first.id,
            request=MemoirDraftBlockRevisionApplyRequest(
                expected_current_version_id=str(original_version_id),
                generation_job_id=revision_job.id,
            ),
        )

    first_assembly = await draft_assembly.assemble_chapter_draft(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
    )
    await test_db.flush()
    assert [item.block_id for item in first_assembly.blocks] == [block.id for block in blocks]
    assert first_assembly.content.startswith("十八岁那年")
    first_snapshot_ids = [item.block_version_id for item in first_assembly.blocks]

    first_version = await test_db.get(MemoirDraftBlockVersion, first.current_version_id)
    assert first_version is not None
    first_version_content = first_version.content
    await draft_blocks.manually_edit_block(
        test_db,
        project_id=created["project_id"],
        block_id=first.id,
        expected_current_version_id=str(first.current_version_id),
        content=first_version_content + " 我也第一次学会独自面对陌生环境。",
        feedback="补充这次离家带来的变化。",
    )
    second_assembly = await draft_assembly.assemble_chapter_draft(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
    )
    assert second_assembly.blocks[0].block_version_id != first_snapshot_ids[0]
    assert second_assembly.version == first_assembly.version + 1
    assert second_assembly.content.startswith(first_version_content)
    repeated_assembly = await draft_assembly.assemble_chapter_draft(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
    )
    assert repeated_assembly.draft_version_id == second_assembly.draft_version_id
    assert repeated_assembly.version == second_assembly.version

    draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.chapter_id == created["chapter"].id
        )
    )
    assert draft is not None
    draft.status = MemoirBookChapterDraftStatus.CONFIRMED.value
    book = await draft_assembly.assemble_book_output(
        test_db,
        project_id=created["project_id"],
    )
    assert book.chapter_snapshot_json[0]["draft_version_id"] == second_assembly.draft_version_id
    assert (
        book.chapter_snapshot_json[0]["block_snapshot"][0]["block_version_id"]
        == second_assembly.blocks[0].block_version_id
    )
    repeated_book = await draft_assembly.assemble_book_output(
        test_db,
        project_id=created["project_id"],
    )
    assert repeated_book.output_version_id == book.output_version_id
    assert repeated_book.version == book.version
    history = list(
        (
            await test_db.scalars(
                select(MemoirBookChapterDraftVersion)
                .where(MemoirBookChapterDraftVersion.draft_id == draft.id)
                .order_by(MemoirBookChapterDraftVersion.version)
            )
        ).all()
    )
    assert len(history) == 2
    assert history[0].block_snapshot_json != history[1].block_snapshot_json
