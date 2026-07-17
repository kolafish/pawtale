from __future__ import annotations

import json
from hashlib import sha256

import pytest
from sqlalchemy import func, select

from app.domain.memoir.narrative_types import (
    MemoirCompletionItemStatus,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapRecommendedAction,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirChapterSourceLink,
    MemoirEditingJob,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import MemoirCompletionItem
from app.services.memoir import book_generation_jobs
from app.services.memoir.narrative.chapter_draft_ai import (
    ChapterDraftClaimInput,
    ChapterDraftGenerationInput,
    ChapterDraftLLMOutput,
    ChapterDraftStoryInput,
    build_chapter_draft_prompt,
    generate_chapter_draft_text,
    validate_chapter_draft_output,
)
from app.services.memoir.narrative.draft_generation_jobs import (
    _generation_input,
    enqueue_chapter_draft_generation,
    run_chapter_draft_generation_job,
)
from app.services.memoir.narrative.gap_rules import (
    chapter_completion_content_signature,
    load_chapter_audit_context,
)
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_source_backed_story,
    create_test_voice_transcript,
    memoir_source_ref,
)


class FakeChapterDraftAI:
    provider_name = "test"
    model_name = "test-whole-chapter"

    def __init__(self, *responses: dict[str, object]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def _call_llm(self, _system_prompt: str, user_prompt: str, **_kwargs):
        self.prompts.append(user_prompt)
        if not self.responses:
            raise AssertionError("unexpected chapter draft AI call")
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def _generation_context() -> ChapterDraftGenerationInput:
    return ChapterDraftGenerationInput(
        chapter_id="chapter-1",
        chapter_title="第一次离家",
        stories=[
            ChapterDraftStoryInput(
                story_id="story-1",
                title="第一次离家",
                summary="十八岁那年离家去成都读书。",
                story_type="event",
                claims=[
                    ChapterDraftClaimInput(
                        claim_id="claim-1",
                        statement="十八岁那年，我离开家乡去成都读书。",
                        certainty="exact",
                        narrative_role="action",
                        is_core=True,
                        evidence_quotes=["十八岁那年，我离开家乡去成都读书。"],
                    )
                ],
            )
        ],
    )


def test_whole_chapter_validation_rejects_unknown_claims_and_third_person_voice():
    context = _generation_context()

    with pytest.raises(ValueError, match="outside allowlist"):
        validate_chapter_draft_output(
            context,
            ChapterDraftLLMOutput(
                content="十八岁那年，我离开家乡。",
                used_claim_ids=["claim-outside"],
            ),
        )

    with pytest.raises(ValueError, match="first-person"):
        validate_chapter_draft_output(
            context,
            ChapterDraftLLMOutput(
                content="叙述者十八岁那年离开家乡。",
                used_claim_ids=["claim-1"],
            ),
        )


async def test_whole_chapter_generation_retries_fact_violation_once():
    context = _generation_context()
    ai = FakeChapterDraftAI(
        {
            "content": "十八岁那年，我离开家乡。",
            "used_claim_ids": ["claim-outside"],
            "unsupported_fact_candidates": [],
        },
        {
            "content": "十八岁那年，我离开家乡去成都读书。",
            "used_claim_ids": ["claim-1"],
            "unsupported_fact_candidates": [],
        },
    )

    result = await generate_chapter_draft_text(ai, context=context)

    assert result.used_claim_ids == ("claim-1",)
    assert len(ai.prompts) == 2
    assert "上一次输出未通过事实约束" in ai.prompts[1]


async def test_generation_job_writes_one_whole_draft_and_acknowledges_sources(
    client,
    test_db,
):
    source_text = "十八岁那年，我离开家乡去成都读书。"
    created = await create_test_voice_transcript(client, test_db, source_text)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一次离家"],
        source_refs_by_chapter=[[memoir_source_ref(created)]],
    )
    chapter = chapters[0]
    _, claim = await create_test_source_backed_story(
        test_db,
        project_id=project_id,
        user_id=created["user_id"],
        source=created,
        title="第一次离家",
        statement=source_text,
    )
    ai = FakeChapterDraftAI(
        {
            "content": "十八岁那年，我离开家乡，去成都读书。",
            "used_claim_ids": [claim.id],
            "unsupported_fact_candidates": [],
        },
        {
            "content": "十八岁那年，我离开家乡，去成都读书。",
            "used_claim_ids": [claim.id],
            "unsupported_fact_candidates": [],
        },
    )

    first_job = await enqueue_chapter_draft_generation(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content_signature=chapter_completion_content_signature(
            await load_chapter_audit_context(
                test_db,
                project_id=project_id,
                chapter_id=chapter.id,
            )
        ),
    )
    await test_db.commit()
    first = await run_chapter_draft_generation_job(
        test_db,
        first_job,
        ai_client=ai,
    )
    await test_db.commit()

    draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.chapter_id == chapter.id
        )
    )
    assert draft is not None
    assert draft.status == "draft"
    assert draft.metadata_json["used_claim_ids"] == [claim.id]
    assert first.result_json["draft_id"] == draft.id
    link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.chapter_id == chapter.id
        )
    )
    assert link is not None
    assert link.merged_draft_version_id == first.result_json["draft_version_id"]

    second_job = await enqueue_chapter_draft_generation(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content_signature=chapter_completion_content_signature(
            await load_chapter_audit_context(
                test_db,
                project_id=project_id,
                chapter_id=chapter.id,
            )
        ),
    )
    await test_db.commit()
    second = await run_chapter_draft_generation_job(
        test_db,
        second_job,
        ai_client=ai,
    )
    await test_db.commit()

    assert second.result_json["draft_version_id"] == first.result_json["draft_version_id"]
    assert await test_db.scalar(
        select(func.count(MemoirBookChapterDraftVersion.id)).where(
            MemoirBookChapterDraftVersion.draft_id == draft.id
        )
    ) == 1


async def test_generation_input_keeps_one_completion_answer_as_a_coherent_block(
    client,
    test_db,
):
    source_text = (
        "朋友告诉我他在组织爬山，我一两年前问过但时间不合适。"
        "今年我发了山桃花朋友圈，他发来照片并说是周末路线，我就请他带上我。"
    )
    created = await create_test_voice_transcript(client, test_db, source_text)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["山桃花开的心动"],
        source_refs_by_chapter=[[memoir_source_ref(created)]],
        draft_contents=["后来我们一起去爬山。"],
    )
    chapter = chapters[0]
    _, claim = await create_test_source_backed_story(
        test_db,
        project_id=project_id,
        user_id=created["user_id"],
        source=created,
        title="重新联系并相约爬山",
        statement=source_text,
    )
    completion_item = MemoirCompletionItem(
        project_id=project_id,
        scope_type=MemoirNarrativeGapScopeType.CHAPTER.value,
        chapter_id=chapter.id,
        item_type=MemoirNarrativeGapType.CAUSALITY.value,
        severity=MemoirNarrativeGapSeverity.MEDIUM.value,
        dimension="chapter_context",
        title="补充重新联系的背景",
        description="解释两人如何从弱联系重新搭上线。",
        fingerprint=sha256(b"coherent-completion-answer").hexdigest(),
        action=MemoirNarrativeGapRecommendedAction.INTERVIEW.value,
        question="这段内容开始前，读者还需要知道哪些重要背景？",
        priority=100,
        status=MemoirCompletionItemStatus.RESOLVED.value,
        detected_by=MemoirNarrativeGapDetectedBy.AI.value,
    )
    test_db.add(completion_item)
    await test_db.flush()
    utterance = await test_db.get(
        MemoirVoiceUtterance,
        created["utterance_id"],
    )
    assert utterance is not None
    utterance.completion_item_id = completion_item.id
    test_db.add(utterance)
    await test_db.commit()

    generation_input = await _generation_input(
        test_db,
        context=await load_chapter_audit_context(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
        ),
        editorial_guidance=[],
    )

    assert len(generation_input.supplement_blocks) == 1
    supplement = generation_input.supplement_blocks[0]
    assert supplement.text == source_text
    assert supplement.question == completion_item.question
    assert supplement.dimension == "chapter_context"
    assert supplement.claim_ids == [claim.id]
    prompt = build_chapter_draft_prompt(generation_input)
    assert "不要让 existing_draft 或其他素材插入 block 中间" in prompt
    assert source_text in prompt


async def test_source_refresh_endpoint_bypasses_open_completion_question_and_updates_event(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(
        client,
        test_db,
        "我回成都后开始重新安排自己的生活。",
    )
    added = await create_test_voice_transcript(
        client,
        test_db,
        "后来我常去青城山散步，也慢慢学会和那段经历保持距离。",
    )
    project_id = first["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["回成都后的疏离与克制"],
        source_refs_by_chapter=[[
            memoir_source_ref(first),
            memoir_source_ref(added),
        ]],
        draft_contents=["我回成都后开始重新安排自己的生活。"],
    )
    chapter = chapters[0]
    chapter.metadata_json = {
        "pending_source_utterance_ids": [added["utterance_id"]],
    }
    _, claim = await create_test_source_backed_story(
        test_db,
        project_id=project_id,
        user_id=added["user_id"],
        source=added,
        title="去青城山散步",
        statement="后来我常去青城山散步，也慢慢学会和那段经历保持距离。",
    )
    completion_item = MemoirCompletionItem(
        project_id=project_id,
        scope_type=MemoirNarrativeGapScopeType.CHAPTER.value,
        chapter_id=chapter.id,
        item_type=MemoirNarrativeGapType.CAUSALITY.value,
        severity=MemoirNarrativeGapSeverity.MEDIUM.value,
        dimension="causality",
        title="去青城山的前因尚不清楚",
        description="可选的质量追问，不应阻断新增讲述补写。",
        fingerprint=sha256(b"source-refresh-open-gap").hexdigest(),
        action=MemoirNarrativeGapRecommendedAction.INTERVIEW.value,
        question="当时为什么会去青城山？",
        priority=20,
        status=MemoirCompletionItemStatus.OPEN.value,
        detected_by=MemoirNarrativeGapDetectedBy.AI.value,
    )
    test_db.add_all([chapter, completion_item])
    await test_db.commit()

    response = await client.post(
        (
            f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/"
            f"{chapter.id}/draft/source-refresh/jobs"
        ),
        json={},
    )

    assert response.status_code == 202
    event_data = response.json()
    assert event_data["event_type"] == "chapter_draft_generated"
    assert event_data["status"] == "pending"
    job = await test_db.get(MemoirEditingJob, event_data["editing_job_id"])
    assert job is not None
    assert job.payload_json["source"] == "book_pending_sources"
    assert job.payload_json["automation_event_id"] == event_data["id"]
    assert completion_item.status == MemoirCompletionItemStatus.OPEN.value
    repeated_response = await client.post(
        (
            f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/"
            f"{chapter.id}/draft/source-refresh/jobs"
        ),
        json={},
    )
    assert repeated_response.status_code == 202
    assert repeated_response.json()["id"] == event_data["id"]
    assert await test_db.scalar(
        select(func.count(MemoirAutomationEvent.id)).where(
            MemoirAutomationEvent.editing_job_id == job.id,
            MemoirAutomationEvent.event_type == "chapter_draft_generated",
        )
    ) == 1

    ai = FakeChapterDraftAI(
        {
            "content": (
                "我回成都后开始重新安排自己的生活。后来我常去青城山散步，"
                "也慢慢学会和那段经历保持距离。"
            ),
            "used_claim_ids": [claim.id],
            "unsupported_fact_candidates": [],
        }
    )
    monkeypatch.setattr(
        "app.services.memoir.narrative.draft_generation_jobs.get_ai_service",
        lambda _model=None: ai,
    )
    result = await book_generation_jobs.run_chapter_draft_generation_job(
        test_db,
        job,
    )
    await test_db.commit()

    assert result.status.value == "succeeded"
    event = await test_db.get(MemoirAutomationEvent, event_data["id"])
    assert event is not None
    assert event.status == "completed"
    assert "已补写 1 条新增讲述" in event.summary_text
    await test_db.refresh(chapter)
    assert "pending_source_utterance_ids" not in chapter.metadata_json
    await test_db.refresh(completion_item)
    assert completion_item.status == MemoirCompletionItemStatus.OPEN.value


async def test_generation_job_skips_stale_input_and_requeues_chapter_refresh(
    client,
    test_db,
):
    source_text = "十八岁那年，我离开家乡去成都读书。"
    created = await create_test_voice_transcript(client, test_db, source_text)
    project_id = created["session"]["project_id"]
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["第一次离家"],
        source_refs_by_chapter=[[memoir_source_ref(created)]],
    )
    chapter = chapters[0]
    await create_test_source_backed_story(
        test_db,
        project_id=project_id,
        user_id=created["user_id"],
        source=created,
        title="第一次离家",
        statement=source_text,
    )
    signature = chapter_completion_content_signature(
        await load_chapter_audit_context(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
    )
    job = await enqueue_chapter_draft_generation(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content_signature=signature,
    )
    await test_db.commit()
    link = await test_db.scalar(
        select(MemoirChapterSourceLink).where(
            MemoirChapterSourceLink.chapter_id == chapter.id
        )
    )
    assert link is not None
    link.quote = f"{link.quote} 后来又补充了一句话。"
    await test_db.commit()
    ai = FakeChapterDraftAI()

    result = await run_chapter_draft_generation_job(
        test_db,
        job,
        ai_client=ai,
    )
    await test_db.commit()

    assert result.status.value == "skipped"
    assert result.result_json["published"] is False
    assert result.result_json["replacement_job_id"]
    assert ai.prompts == []
    assert await test_db.scalar(
        select(func.count(MemoirBookChapterDraft.id)).where(
            MemoirBookChapterDraft.chapter_id == chapter.id
        )
    ) == 0
