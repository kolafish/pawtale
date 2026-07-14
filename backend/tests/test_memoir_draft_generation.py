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
    MemoirStorySlot,
    MemoirStorySlotClaim,
)
from app.schemas.memoir_narrative import (
    MemoirDraftBlockGenerationRequest,
    MemoirDraftBlockRevisionApplyRequest,
    MemoirDraftBlockRevisionJobRequest,
)
from app.services.memoir.narrative import draft_assembly, draft_blocks, draft_generation_jobs
from app.services.memoir.narrative.draft_block_ai import (
    DraftBlockGenerationInput,
    DraftBlockLLMOutput,
    validate_generation_output,
)
from app.services.memoir.narrative.quality_measurement import (
    measure_narrative_candidate,
)
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


async def _create_generation_fixture(
    client,
    test_db,
    *,
    include_opening: bool = False,
    include_transition: bool = False,
):
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
            sort_order=(
                index * 2 + (1 if include_opening else 0)
                if include_transition
                else index + (1 if include_opening else 0)
            ),
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
        slot = MemoirStorySlot(
            project_id=project_id,
            story_id=story.id,
            schema_version="1",
            slot_key="actions",
            status="sufficient",
            assessment_summary="行动已有事实支持。",
            user_resolution_note="",
        )
        contradicted_slot = MemoirStorySlot(
            project_id=project_id,
            story_id=story.id,
            schema_version="1",
            slot_key="outcome",
            status="partial",
            assessment_summary="结果只有反证，不能作为正文覆盖来源。",
            user_resolution_note="",
        )
        test_db.add_all([slot, contradicted_slot])
        await test_db.flush()
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
                MemoirStorySlotClaim(
                    project_id=project_id,
                    story_slot_id=slot.id,
                    claim_id=claim.id,
                    evidence_role=MemoirClaimEvidenceRole.SUPPORTS.value,
                    created_by=MemoirNarrativeActorType.USER.value,
                    created_by_user_id=user_id,
                ),
                MemoirStorySlotClaim(
                    project_id=project_id,
                    story_slot_id=contradicted_slot.id,
                    claim_id=claim.id,
                    evidence_role=MemoirClaimEvidenceRole.CONTRADICTS.value,
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
    opening = None
    if include_opening:
        opening = MemoirChapterNode(
            project_id=project_id,
            chapter_id=chapter.id,
            contract_id=contract.id,
            node_type=MemoirChapterNodeType.OPENING.value,
            title="开场",
            purpose="从一个有证据的具体画面进入本章。",
            sort_order=0,
            required=True,
            status=MemoirChapterNodeStatus.READY.value,
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=user_id,
            updated_by=MemoirNarrativeActorType.USER.value,
            updated_by_user_id=user_id,
        )
        test_db.add(opening)
        await test_db.flush()
        test_db.add(
            MemoirChapterNodeStory(
                project_id=project_id,
                node_id=opening.id,
                story_id=stories[0].id,
                story_role=MemoirChapterNodeStoryRole.SUPPORTING.value,
                sort_order=0,
                required=False,
                created_by=MemoirNarrativeActorType.USER.value,
                created_by_user_id=user_id,
            )
        )
    transition = None
    if include_transition:
        transition = MemoirChapterNode(
            project_id=project_id,
            chapter_id=chapter.id,
            contract_id=contract.id,
            node_type=MemoirChapterNodeType.TRANSITION.value,
            title="转场",
            purpose="衔接离开家乡和住进宿舍。",
            sort_order=2 if include_opening else 1,
            required=True,
            transition_from_node_id=nodes[0].id,
            transition_to_node_id=nodes[1].id,
            status=MemoirChapterNodeStatus.READY.value,
            created_by=MemoirNarrativeActorType.USER.value,
            created_by_user_id=user_id,
            updated_by=MemoirNarrativeActorType.USER.value,
            updated_by_user_id=user_id,
        )
        test_db.add(transition)
    await test_db.flush()
    contract.status = MemoirChapterContractStatus.CONFIRMED.value
    contract.confirmed_by_user_id = user_id
    contract.confirmed_at = datetime.now(UTC).replace(tzinfo=None)
    await test_db.commit()
    return {
        "project_id": project_id,
        "user_id": user_id,
        "plan": plan,
        "chapter": chapter,
        "contract": contract,
        "opening": opening,
        "transition": transition,
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


def test_generation_validation_enforces_sources_unsupported_facts_and_adjacency():
    claim_id = "f07e2888-183a-4a58-9442-d4374cf0e6d7"
    context = DraftBlockGenerationInput.model_validate(
        {
            "contract_id": "contract",
            "contract_objective": "梳理工作经历。",
            "reader_takeaway": "理解关键经历。",
            "node_id": "opening",
            "node_type": "opening",
            "node_title": "开场",
            "node_purpose": "从已有经历进入本章。",
            "block_role": "opening",
            "stories": [
                {
                    "story_id": "story",
                    "story_revision_id": "revision",
                    "title": "储备粮食成为先进乡并调回县任职",
                    "summary": "储备粮食后成为先进乡，随后调回县任职。",
                    "story_role": "supporting",
                    "claims": [
                        {
                            "claim_id": claim_id,
                            "statement": "储备粮食后成为先进乡，随后调回县任职。",
                            "certainty": "exact",
                            "verification_status": "confirmed",
                            "narrative_role": "outcome",
                            "is_core": True,
                        }
                    ],
                }
            ],
        }
    )
    validate_generation_output(
        context,
        DraftBlockLLMOutput(
            content="储备粮食以后，乡里成了先进乡，后来我调回县里任职。",
            used_claim_ids=[claim_id],
            unsupported_fact_candidates=["我曾任武装队长"],
        ),
    )

    with pytest.raises(ValueError, match="unsupported fact candidates"):
        validate_generation_output(
            context,
            DraftBlockLLMOutput(
                content="我曾任武装队长，储备粮食以后乡里成了先进乡。",
                used_claim_ids=[claim_id],
                unsupported_fact_candidates=["我曾任武装队长"],
            ),
        )
    with pytest.raises(ValueError, match="with fact sources"):
        validate_generation_output(
            context,
            DraftBlockLLMOutput(
                content="我这一辈子，岗位换了不少。",
                used_claim_ids=[],
                unsupported_fact_candidates=[],
            ),
        )
    duplicate_context = context.model_copy(
        update={"next_block_context": "储备粮食以后，乡里成了先进乡。"}
    )
    with pytest.raises(ValueError, match="duplicates an adjacent sentence"):
        validate_generation_output(
            duplicate_context,
            DraftBlockLLMOutput(
                content="储备粮食以后，乡里成了先进乡！",
                used_claim_ids=[claim_id],
                unsupported_fact_candidates=[],
            ),
        )


def test_factual_generation_requires_supported_slot_coverage_and_narrator_voice():
    action_claim_id = "action-claim"
    outcome_claim_id = "outcome-claim"
    context = DraftBlockGenerationInput.model_validate(
        {
            "contract_id": "contract",
            "contract_objective": "写清一段完整经历。",
            "reader_takeaway": "理解行动和结果。",
            "node_id": "story-node",
            "node_type": "story",
            "node_title": "一次转折",
            "node_purpose": "完整讲述事情的经过。",
            "block_role": "factual",
            "stories": [
                {
                    "story_id": "story",
                    "story_revision_id": "revision",
                    "title": "一次转折",
                    "summary": "采取行动后事情有了结果。",
                    "story_role": "primary",
                    "coverage_slot_keys": ["actions", "outcome"],
                    "claims": [
                        {
                            "claim_id": action_claim_id,
                            "statement": "叙述者采取了行动。",
                            "certainty": "exact",
                            "verification_status": "confirmed",
                            "narrative_role": "action",
                            "is_core": True,
                            "slot_keys": ["actions"],
                        },
                        {
                            "claim_id": outcome_claim_id,
                            "statement": "事情有了结果。",
                            "certainty": "exact",
                            "verification_status": "confirmed",
                            "narrative_role": "outcome",
                            "is_core": True,
                            "slot_keys": ["outcome"],
                        },
                    ],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="missed supported Story slots.*outcome"):
        validate_generation_output(
            context,
            DraftBlockLLMOutput(
                content="我采取了行动。",
                used_claim_ids=[action_claim_id],
                unsupported_fact_candidates=[],
            ),
        )
    with pytest.raises(ValueError, match="first-person voice"):
        validate_generation_output(
            context,
            DraftBlockLLMOutput(
                content="叙述者采取了行动，事情随后有了结果。",
                used_claim_ids=[action_claim_id, outcome_claim_id],
                unsupported_fact_candidates=[],
            ),
        )

    validate_generation_output(
        context,
        DraftBlockLLMOutput(
            content="我采取了行动，事情随后有了结果。",
            used_claim_ids=[action_claim_id, outcome_claim_id],
            unsupported_fact_candidates=[],
        ),
    )


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


async def test_initial_generation_builds_source_backed_opening_before_facts(
    client,
    test_db,
):
    created = await _create_generation_fixture(
        client,
        test_db,
        include_opening=True,
    )
    job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
        request=MemoirDraftBlockGenerationRequest(),
    )
    ai = FakeDraftAI(
        [
            _ai_output("十八岁那年，我离开家乡。", created["claims"][0].id),
            _ai_output("十八岁那年，我离开家乡。", created["claims"][0].id),
            _ai_output("那年我动身去成都读书。", created["claims"][0].id),
            _ai_output("到成都后，我住进了学校宿舍。", created["claims"][1].id),
        ]
    )
    result = await draft_generation_jobs.run_draft_block_generation_job(
        test_db,
        job,
        ai_client=ai,
    )
    blocks = list(
        (
            await test_db.scalars(
                select(MemoirDraftBlock).order_by(MemoirDraftBlock.sort_order)
            )
        ).all()
    )

    assert [block.id for block in blocks] == result.result_json["created_block_ids"]
    assert '"node_type":"opening"' in ai.prompts[0]
    assert '"node_type":"story"' in ai.prompts[1]
    assert '"coverage_slot_keys":["actions"]' in ai.prompts[1]
    assert '"slot_keys":["actions"]' in ai.prompts[1]
    assert '"previous_block_context":"十八岁那年，我离开家乡。"' in ai.prompts[1]
    assert '"node_type":"story"' in ai.prompts[2]
    assert "上一次输出未通过服务端事实与重复校验" in ai.prompts[2]
    assert "十八岁那年，我离开家乡" in ai.prompts[2]
    assert '"node_type":"story"' in ai.prompts[3]


async def test_initial_generation_builds_transition_after_both_endpoint_blocks(
    client,
    test_db,
):
    created = await _create_generation_fixture(
        client,
        test_db,
        include_transition=True,
    )
    job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
        request=MemoirDraftBlockGenerationRequest(),
    )
    ai = FakeDraftAI(
        [
            _ai_output("十八岁那年，我离开家乡。", created["claims"][0].id),
            _ai_output("到成都后，我住进了学校宿舍。", created["claims"][1].id),
            {
                "content": "离开家乡之后，新的生活就此开始。",
                "used_claim_ids": [],
                "unsupported_fact_candidates": [],
            },
        ]
    )
    await draft_generation_jobs.run_draft_block_generation_job(
        test_db,
        job,
        ai_client=ai,
    )

    assert '"node_type":"story"' in ai.prompts[0]
    assert '"node_type":"story"' in ai.prompts[1]
    assert '"node_type":"transition"' in ai.prompts[2]
    assert '"previous_block_context":"十八岁那年，我离开家乡。"' in ai.prompts[2]
    assert '"next_block_context":"到成都后，我住进了学校宿舍。"' in ai.prompts[2]


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
            ),
            _ai_output(
                "这段文字仍然使用了不属于当前节点的事实。",
                created["claims"][1].id,
            ),
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

    with pytest.raises(ValueError, match="confirm every included draft block"):
        await draft_assembly.assemble_chapter_draft(
            test_db,
            project_id=created["project_id"],
            chapter_id=created["chapter"].id,
        )
    for index, block in enumerate(blocks):
        await draft_blocks.confirm_block(
            test_db,
            project_id=created["project_id"],
            block_id=block.id,
            expected_current_version_id=str(block.current_version_id),
            actor_user_id=created["user_id"],
            idempotency_key=f"confirm-before-first-assembly-{index}",
            reason="reviewed for chapter assembly",
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
    with pytest.raises(ValueError, match="confirm every included draft block"):
        await draft_assembly.assemble_chapter_draft(
            test_db,
            project_id=created["project_id"],
            chapter_id=created["chapter"].id,
        )
    await draft_blocks.confirm_block(
        test_db,
        project_id=created["project_id"],
        block_id=first.id,
        expected_current_version_id=str(first.current_version_id),
        actor_user_id=created["user_id"],
        idempotency_key="confirm-edited-block-before-second-assembly",
        reason="reviewed edited block",
    )
    stale_confirm_response = await client.post(
        f"/api/mobile/memoir/projects/{created['project_id']}"
        f"/book-plan/chapters/{created['chapter'].id}/draft/confirm"
    )
    assert stale_confirm_response.status_code == 409
    assert stale_confirm_response.json()["detail"] == "正文块在组装后发生变化，请重新组装本章"
    second_assembly = await draft_assembly.assemble_chapter_draft(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
    )
    assert second_assembly.blocks[0].block_version_id != first_snapshot_ids[0]
    assert second_assembly.version == first_assembly.version + 1
    assert second_assembly.content.startswith(first_version_content)
    measured_content, quality_artifact = await measure_narrative_candidate(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
        baseline_draft_version_id=first_assembly.draft_version_id,
        candidate_draft_version_id=second_assembly.draft_version_id,
    )
    assert measured_content == second_assembly.content
    assert quality_artifact["metrics"] == {
        "fact_sentence_count": 3,
        "mapped_fact_sentence_count": 3,
        "unmapped_fact_ids": [],
        "unresolved_high_gaps": {"time": 0, "people": 0, "evidence": 0},
        "locked_block_changes": 0,
        "unrelated_block_changes": 0,
    }
    assert quality_artifact["comparison"]["changed_block_ids"] == [first.id]
    assert quality_artifact["comparison"]["intended_block_ids"] == [first.id]
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
    confirm_response = await client.post(
        f"/api/mobile/memoir/projects/{created['project_id']}"
        f"/book-plan/chapters/{created['chapter'].id}/draft/confirm"
    )
    assert confirm_response.status_code == 200, confirm_response.text
    assert confirm_response.json()["status"] == MemoirBookChapterDraftStatus.CONFIRMED.value
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

    current_first_version = await test_db.get(
        MemoirDraftBlockVersion,
        first.current_version_id,
    )
    assert current_first_version is not None
    await draft_blocks.manually_edit_block(
        test_db,
        project_id=created["project_id"],
        block_id=first.id,
        expected_current_version_id=str(first.current_version_id),
        content=current_first_version.content + " 后来回想起来，这一步很重要。",
        feedback="补充后来的理解。",
    )
    await draft_blocks.confirm_block(
        test_db,
        project_id=created["project_id"],
        block_id=first.id,
        expected_current_version_id=str(first.current_version_id),
        actor_user_id=created["user_id"],
        idempotency_key="confirm-edit-after-book-assembly",
        reason="reviewed later edit",
    )

    stale_output_response = await client.get(
        f"/api/mobile/memoir/projects/{created['project_id']}/book-output"
    )
    assert stale_output_response.status_code == 200
    assert stale_output_response.json()["is_stale"] is True
    with pytest.raises(ValueError, match="confirmed structured chapter snapshots"):
        await draft_assembly.assemble_book_output(
            test_db,
            project_id=created["project_id"],
        )
