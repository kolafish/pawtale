from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json

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
    MemoirFollowupAnswerInterpretationStatus,
    MemoirFollowupAnswerKind,
    MemoirFollowupTaskStatus,
    MemoirFollowupTaskAnswerRole,
    MemoirNarrativeActorType,
    MemoirStoryCompletionStatus,
    MemoirStoryLifecycleStatus,
    MemoirStoryNarrativeRole,
    MemoirStoryPublicationStatus,
    MemoirStoryRevisionChangeType,
    MemoirStoryType,
    MemoirStoryVerificationStatus,
)
from app.domain.memoir.types import MemoirBookChapterDraftStatus
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlanChapter,
)
from app.models.memoir_narrative import (
    MemoirChapterContract,
    MemoirChapterNode,
    MemoirChapterNodeStory,
    MemoirClaim,
    MemoirClaimEvidence,
    MemoirDraftBlock,
    MemoirDraftBlockVersion,
    MemoirFollowupTask,
    MemoirFollowupTaskAnswer,
    MemoirUtteranceInterviewAssignment,
    MemoirNarrativeGap,
    MemoirStory,
    MemoirStoryClaim,
    MemoirStoryRevision,
    MemoirStorySlot,
    MemoirStorySlotClaim,
)
from app.services.memoir.narrative import (
    draft_assembly,
    draft_blocks,
    draft_generation_jobs,
    followup_completion,
)
from app.services.memoir.narrative.draft_block_ai import (
    DraftBlockEditorialContextInput,
    DraftBlockGenerationInput,
    DraftBlockLLMOutput,
    validate_generation_output,
)
from app.services.memoir.narrative.draft_generation_context import (
    build_node_generation_context,
    load_current_contract_nodes,
)
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
)


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
    confirm_contract: bool = True,
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
    if confirm_contract:
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


async def _create_editorial_answer(
    client,
    test_db,
    *,
    created,
    answer_text: str,
    answer_kind: MemoirFollowupAnswerKind,
    direct_narrative_text: str = "",
    clarification_text: str = "",
    fact_resolution_text: str = "",
    answer_use: str = "clarification",
    story_id: str | None = None,
    answered: bool = True,
):
    transcript = await create_test_voice_transcript(
        client,
        test_db,
        answer_text,
        project_id=created["project_id"],
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    gap = MemoirNarrativeGap(
        project_id=created["project_id"],
        scope_type="chapter",
        primary_chapter_id=created["chapter"].id,
        gap_type="causality",
        severity="medium",
        title="解释现有内容与章节主线的关系",
        description="需要区分正文事实和编辑解释。",
        fingerprint=sha256(
            f"editorial:{transcript['utterance_id']}".encode("utf-8")
        ).hexdigest(),
        status="resolved" if answered else "open",
        detected_by="ai",
        resolved_by="system" if answered else None,
        resolved_at=now if answered else None,
        resolution_note="回答用途已确认。" if answered else "",
    )
    test_db.add(gap)
    await test_db.flush()
    task = MemoirFollowupTask(
        project_id=created["project_id"],
        gap_id=gap.id,
        chapter_id=created["chapter"].id,
        story_id=story_id,
        answer_use=answer_use,
        question="这段话是在补充事实，还是解释原文？",
        purpose="避免把编辑解释写入章节正文。",
        priority=100,
        status=(
            MemoirFollowupTaskStatus.ANSWERED.value
            if answered
            else MemoirFollowupTaskStatus.PENDING.value
        ),
        completed_at=now if answered else None,
        created_by="system",
    )
    test_db.add(task)
    await test_db.flush()
    relation = MemoirFollowupTaskAnswer(
        project_id=created["project_id"],
        task_id=task.id,
        utterance_id=transcript["utterance_id"],
        result_role=(
            MemoirFollowupTaskAnswerRole.TARGET_ANSWER.value
            if answered
            else MemoirFollowupTaskAnswerRole.RELATED_CONTEXT.value
        ),
        interpretation_status=MemoirFollowupAnswerInterpretationStatus.RESOLVED.value,
        answer_kind=answer_kind.value,
        direct_narrative_text=direct_narrative_text,
        clarification_text=clarification_text,
        fact_resolution_text=fact_resolution_text,
    )
    test_db.add_all(
        [
            relation,
            MemoirUtteranceInterviewAssignment(
                utterance_id=transcript["utterance_id"],
                followup_task_id=task.id,
            ),
        ]
    )
    await test_db.commit()
    return transcript, gap, task, relation


def test_generation_validation_enforces_sources_and_adjacent_sentence_boundary():
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
    with pytest.raises(ValueError, match="duplicates an adjacent sentence"):
        validate_generation_output(
            context.model_copy(
                update={"next_block_context": "储备粮食以后，乡里成了先进乡。"}
            ),
            DraftBlockLLMOutput(
                content="储备粮食以后，乡里成了先进乡！",
                used_claim_ids=[claim_id],
                unsupported_fact_candidates=[],
            ),
        )

    editorial_context = DraftBlockEditorialContextInput(
        relation_id="relation",
        task_id="task",
        utterance_id="utterance",
        question="这段话是什么意思？",
        purpose="解释原文，不新增正文。",
        answer_kind=MemoirFollowupAnswerKind.CLARIFICATION,
        clarification_text="这只是我原本想发给爷爷、但最后没有发出的消息。",
    )
    with pytest.raises(ValueError, match="copied non-narrative editorial context"):
        validate_generation_output(
            context.model_copy(update={"editorial_contexts": [editorial_context]}),
            DraftBlockLLMOutput(
                content="这只是我原本想发给爷爷、但最后没有发出的消息。",
                used_claim_ids=[claim_id],
                unsupported_fact_candidates=[],
            ),
        )
    validate_generation_output(
        context.model_copy(
            update={
                "editorial_contexts": [
                    editorial_context.model_copy(
                        update={
                            "clarification_text": "储备粮食后成为先进乡，随后调回县任职。"
                        }
                    )
                ]
            }
        ),
        DraftBlockLLMOutput(
            content="储备粮食后成为先进乡，随后调回县任职。",
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


async def test_draft_generation_uses_the_current_draft_chapter_direction(
    client,
    test_db,
):
    created = await _create_generation_fixture(
        client,
        test_db,
        confirm_contract=False,
    )
    assert created["contract"].status == MemoirChapterContractStatus.DRAFT.value
    job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
    )

    result = await draft_generation_jobs.run_draft_block_generation_job(
        test_db,
        job,
        ai_client=FakeDraftAI(
            [
                _ai_output(
                    "十八岁那年，我离开家乡，前往成都读书。",
                    created["claims"][0].id,
                ),
                _ai_output(
                    "抵达成都后，我住进了学校宿舍。",
                    created["claims"][1].id,
                ),
            ]
        ),
    )

    assert result.result_json["contract_id"] == created["contract"].id
    assert len(result.result_json["created_block_ids"]) == 2


async def test_node_generation_uses_claim_allowlists_and_skips_current_blocks(
    client,
    test_db,
    monkeypatch,
):
    created = await _create_generation_fixture(client, test_db)
    job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
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


async def test_structured_generation_separates_narrative_and_editorial_answer_parts(
    client,
    test_db,
):
    created = await _create_generation_fixture(client, test_db)
    direct_text = "我还想补充一次回成都后的探望。"
    clarification_text = "买西瓜是我原本想发给爷爷、但最后没有发出的消息背景。"
    transcript, _gap, _task, _relation = await _create_editorial_answer(
        client,
        test_db,
        created=created,
        answer_text=direct_text + clarification_text,
        answer_kind=MemoirFollowupAnswerKind.MIXED,
        direct_narrative_text=direct_text,
        clarification_text=clarification_text,
        story_id=created["stories"][0].id,
    )
    fact_text = "这里确认正确的年份是1982年，而不是1983年。"
    await _create_editorial_answer(
        client,
        test_db,
        created=created,
        answer_text=fact_text,
        answer_kind=MemoirFollowupAnswerKind.FACT_RESOLUTION,
        fact_resolution_text=fact_text,
        answer_use="fact_resolution",
        story_id=created["stories"][0].id,
    )

    contract, nodes = await load_current_contract_nodes(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
    )
    context = await build_node_generation_context(
        test_db,
        project_id=created["project_id"],
        contract=contract,
        nodes=nodes,
        node=nodes[0],
    )

    assert len(context.ai_input.editorial_contexts) == 2
    assert context.ai_input.editorial_contexts[0].clarification_text == clarification_text
    assert context.ai_input.editorial_contexts[0].transcript_version_id == transcript[
        "transcript_version_id"
    ]
    assert context.ai_input.editorial_contexts[1].fact_resolution_text == fact_text
    serialized = context.ai_input.model_dump_json()
    assert direct_text not in serialized
    assert clarification_text in serialized
    assert fact_text in serialized


async def test_clarification_marks_blocks_stale_and_regenerates_only_unlocked_blocks(
    client,
    test_db,
):
    created = await _create_generation_fixture(client, test_db)
    initial_job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
    )
    await draft_generation_jobs.run_draft_block_generation_job(
        test_db,
        initial_job,
        ai_client=FakeDraftAI(
            [
                _ai_output("十八岁那年，我离开家乡。", created["claims"][0].id),
                _ai_output("到成都后，我住进了学校宿舍。", created["claims"][1].id),
            ]
        ),
    )
    blocks = list(
        (
            await test_db.scalars(
                select(MemoirDraftBlock).order_by(MemoirDraftBlock.sort_order)
            )
        ).all()
    )
    original_version_ids = [block.current_version_id for block in blocks]
    blocks[1].locked = True
    await test_db.flush()
    clarification_text = "这段话只是在解释章节为什么写到离家，不是新的经历。"
    _transcript, _gap, _task, relation = await _create_editorial_answer(
        client,
        test_db,
        created=created,
        answer_text=clarification_text,
        answer_kind=MemoirFollowupAnswerKind.CLARIFICATION,
        clarification_text=clarification_text,
        answered=False,
    )

    completed = await followup_completion.complete_task_from_semantic_interpretation(
        test_db,
        project_id=created["project_id"],
        relation=relation,
    )
    assert completed is True
    await test_db.commit()
    for block in blocks:
        await test_db.refresh(block)
        assert block.freshness_status == "stale"

    regeneration_job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
    )
    ai = FakeDraftAI(
        [_ai_output("十八岁那年，我从家乡动身去成都。", created["claims"][0].id)]
    )
    result = await draft_generation_jobs.run_draft_block_generation_job(
        test_db,
        regeneration_job,
        ai_client=ai,
    )

    assert result.result_json["created_block_ids"] == []
    assert result.result_json["updated_block_ids"] == [blocks[0].id]
    assert result.result_json["skipped_locked_node_ids"] == [created["nodes"][1].id]
    assert clarification_text in ai.prompts[0]
    await test_db.refresh(blocks[0])
    await test_db.refresh(blocks[1])
    assert blocks[0].current_version_id != original_version_ids[0]
    assert blocks[0].freshness_status == "current"
    assert blocks[1].current_version_id == original_version_ids[1]
    assert blocks[1].freshness_status == "stale"


async def test_generation_rejects_changed_editorial_context_before_calling_ai(
    client,
    test_db,
):
    created = await _create_generation_fixture(client, test_db)
    answer_text = "第一种解释是原文背景。第二种解释是章节关系。"
    _transcript, _gap, _task, relation = await _create_editorial_answer(
        client,
        test_db,
        created=created,
        answer_text=answer_text,
        answer_kind=MemoirFollowupAnswerKind.CLARIFICATION,
        clarification_text="第一种解释是原文背景。",
        story_id=created["stories"][0].id,
    )
    job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
    )
    relation.clarification_text = "第二种解释是章节关系。"
    test_db.add(relation)
    await test_db.commit()

    with pytest.raises(ValueError, match="editorial context changed"):
        await draft_generation_jobs.run_draft_block_generation_job(
            test_db,
            job,
            ai_client=FakeDraftAI([]),
        )


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


async def test_chapter_assembly_accepts_current_blocks_and_keeps_snapshots(
    client,
    test_db,
):
    created = await _create_generation_fixture(client, test_db)
    generation_job = await draft_generation_jobs.enqueue_chapter_block_generation(
        test_db,
        project_id=created["project_id"],
        chapter_id=created["chapter"].id,
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
    hidden_chapter = MemoirBookPlanChapter(
        plan_id=created["chapter"].plan_id,
        project_id=created["project_id"],
        title="保留但不收入本书的补记",
        sort_order=1,
        include_in_book=False,
        source_refs_json=[],
        metadata_json={},
    )
    test_db.add(hidden_chapter)
    await test_db.flush()
    book = await draft_assembly.assemble_book_output(
        test_db,
        project_id=created["project_id"],
    )
    assert [item["chapter_id"] for item in book.chapter_snapshot_json] == [
        created["chapter"].id
    ]
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
