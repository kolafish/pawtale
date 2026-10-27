import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.config import DEFAULT_AI_TEXT_MODEL
from app.services.ai_base import AITextServiceBase
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirSectionMaterialAssignedBy,
    MemoirSemanticCacheStatus,
    MemoirTranscriptPolishLevel,
)
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirChapterDigest,
    MemoirCompletionIssue,
    MemoirSection,
    MemoirSectionMaterial,
)
from app.schemas.memoir_section_digests import MemoirBodyDigest
from app.services.ai_references import ModelReferenceMap
from app.services.memoir import (
    book_output_snapshots,
    chapter_digests,
    chapter_generation,
    section_digests,
    sections,
)
from app.services.memoir.editing_job_handlers import (
    register_memoir_editing_job_handlers,
)
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from app.services.memoir.section_roles import (
    PENDING_ASSIGNMENT_SECTION_ROLE,
    PENDING_ASSIGNMENT_SECTION_TITLE,
    SECTION_SYSTEM_ROLE_KEY,
)
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
    run_test_editing_job,
)


class FakeChapterAI(AITextServiceBase):
    provider_name = "test"
    model_name = "test-chapter"

    def __init__(
        self,
        *,
        section_material_id: str,
        chapter_section_id: str,
        chapter_material_id: str,
        draft_material_ids: list[str],
    ):
        self.section_material_id = section_material_id
        self.chapter_section_id = chapter_section_id
        self.chapter_material_id = chapter_material_id
        self.draft_material_ids = draft_material_ids
        self.prompts: list[str] = []

    async def _call_llm(
        self,
        system_prompt,
        user_prompt,
        temperature=0.7,
        max_tokens=4000,
        timeout=60.0,
        return_raw=False,
    ):
        self.prompts.append(user_prompt)
        prompt_section_ids = [
            line.removeprefix("section_id: ").strip()
            for line in user_prompt.splitlines()
            if line.startswith("section_id: ")
        ]
        prompt_material_ids = [
            line.removeprefix("material_id: ").strip()
            for line in user_prompt.splitlines()
            if line.startswith("material_id: ")
        ]
        source_line = next((line for line in user_prompt.splitlines() if line.startswith("全部已归属素材编号")), "")
        if source_line:
            prompt_material_ids = source_line.split("：", 1)[1].split("、")
        if "审阅现有回忆录目录" in user_prompt:
            payload = {"findings": []}
        elif "你只概括当前已保存的小节正文" in system_prompt:
            payload = {"summary": "当前正文的经历", "focus": "经历", "points": []}
        elif "请通读回忆录章节" in user_prompt:
            payload = {
                "summary": "这一章讲重新联系和共同爬山。",
                "focus": "重新建立联系。",
                "candidate_issues": [
                    {
                        "issue_type": "meaning",
                        "channel": "interview",
                        "question": "这次见面对你意味着什么？",
                        "reason": "会让章节重点更清楚。",
                        "priority": "high",
                        "section_ids": prompt_section_ids[:1],
                    }
                ],
            }
        else:
            payload = {
                "content": (
                    "朋友发来一张山桃花照片，后来我们一起去爬山。"
                    "这次见面让一段疏远的关系重新有了联系。"
                ),
                "used_material_ids": (
                    ["M99"]
                    if self.draft_material_ids == ["foreign-material"]
                    else prompt_material_ids
                ),
                "missing_source_candidates": [],
                "editor_notes": ["合并了重复描述。"],
            }
        if "content" in payload:
            payload["body_digest"] = {"summary": payload["content"], "focus": "经历", "points": [
                {"text": payload["content"], "material_ids": payload["used_material_ids"]}]}
        return json.dumps(payload, ensure_ascii=False)


class TruncatedChapterDigestAI(FakeChapterAI):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.chapter_max_tokens: list[int] = []

    async def _call_llm(
        self,
        system_prompt,
        user_prompt,
        temperature=0.7,
        max_tokens=4000,
        timeout=60.0,
        return_raw=False,
    ):
        if "请通读回忆录章节" not in user_prompt:
            return await super()._call_llm(
                system_prompt,
                user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                return_raw=return_raw,
            )
        self.prompts.append(user_prompt)
        self.chapter_max_tokens.append(max_tokens)
        content = '{"summary":"这一章讲重新联系和共同爬山。",'
        if return_raw:
            return content, {
                "choices": [{
                    "finish_reason": "length",
                    "message": {"content": content},
                }]
            }
        return content


def _two_section_context() -> chapter_generation.ChapterGenerationContext:
    first_material = chapter_generation.ChapterMaterialInput(
        material_id="material-1",
        section_id="section-1",
        section_title="出发",
        section_type="event",
        section_version=1,
        section_order=0,
        material_order=0,
        transcript_version_id="transcript-1",
        text="那天我从家里出发。",
    )
    second_material = chapter_generation.ChapterMaterialInput(
        material_id="material-2",
        section_id="section-2",
        section_title="抵达",
        section_type="event",
        section_version=2,
        section_order=1,
        material_order=0,
        transcript_version_id="transcript-2",
        text="傍晚时我终于抵达。",
    )
    return chapter_generation.ChapterGenerationContext(
        project_id="project-1",
        chapter_id="chapter-1",
        chapter_title="一次远行",
        sections=(
            chapter_generation.ChapterSectionInput(
                section_id="section-1",
                title="出发",
                section_type="event",
                section_version=1,
                sort_order=0,
                materials=(first_material,),
            ),
            chapter_generation.ChapterSectionInput(
                section_id="section-2",
                title="抵达",
                section_type="event",
                section_version=2,
                sort_order=1,
                materials=(second_material,),
            ),
        ),
        materials=(first_material, second_material),
        input_signature="signature",
    )


async def test_chapter_generation_binds_third_section_without_model_identifier():
    class SectionAI(AITextServiceBase):
        provider_name = "test"
        model_name = "test-bound-section"

        async def _call_llm(self, *args, **kwargs):
            return json.dumps({"body_digest": {"summary": "Saved body summary", "points": []},
                "content": "后来我又继续出发了。",
                "used_material_ids": ["M1"],
            }, ensure_ascii=False)

    context = _two_section_context()
    material = replace(context.materials[1], material_id="material-3", section_id="section-3",
                       section_title="再出发", section_order=2, text="后来我又继续出发了。")
    third = replace(context.sections[1], section_id="section-3", title="再出发",
                    sort_order=2, materials=(material,))
    full_sections = (*context.sections, third)
    preserved = tuple(chapter_generation.GeneratedChapterSection(
        section_id=section.section_id, title=section.title, content=section.materials[0].text,
        used_material_ids=(section.materials[0].material_id,),
    ) for section in context.sections)
    generated = await chapter_generation.generate_chapter(
        chapter_generation._single_section_context(context, third),
        chapter_sections=full_sections,
        preserved_sections=preserved, ai_client=SectionAI(),
    )

    assert generated.sections[:2] == preserved
    assert generated.sections[2] == chapter_generation.GeneratedChapterSection(
        section_id=third.section_id, title=third.title, content="后来我又继续出发了。",
        used_material_ids=(material.material_id,),
        body_digest=MemoirBodyDigest(summary="Saved body summary"),
    )
    assert generated.used_material_ids == ("material-1", "material-2", "material-3")


def test_chapter_generation_prompt_requests_natural_paragraphs():
    context = replace(
        _two_section_context(),
        polish_level=MemoirTranscriptPolishLevel.HIGH,
    )
    prompt = chapter_generation.build_chapter_generation_prompt(
        chapter_generation._single_section_context(context, context.sections[0]),
    )

    assert "正文根据内容自然分段，段落之间用换行分隔" in prompt
    assert "可较大幅度重组句式与表达层次" in prompt
    assert "按时间、场景、事件推进或感受转折" not in prompt
    assert "不把一条 Material 拆散后穿插到多处" not in prompt


async def test_chapter_generation_retries_only_the_invalid_section():
    class RetryingSectionAI(AITextServiceBase):
        provider_name = "test"
        model_name = "test-section-retry"

        def __init__(self):
            self.prompts: list[str] = []
            self.section_attempts: dict[str, int] = {}

        async def _call_llm(
            self,
            system_prompt,
            user_prompt,
            temperature=0.7,
            max_tokens=4000,
            timeout=60.0,
            return_raw=False,
        ):
            self.prompts.append(user_prompt)
            material_id = next(
                line.removeprefix("material_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("material_id: ")
            )
            target = (
                "section-2"
                if "小节标题: 抵达" in user_prompt
                else "section-1"
            )
            attempt = self.section_attempts.get(target, 0) + 1
            self.section_attempts[target] = attempt
            used_material_ids = [material_id]
            if target == "section-2" and attempt == 1:
                used_material_ids = []
            return json.dumps(
                {"body_digest": {"summary": "Saved body summary", "points": []},
                    "content": (
                        "第一小节的正文"
                        if target == "section-1"
                        else "第二小节的正文"
                    ),
                    "used_material_ids": used_material_ids,
                    "missing_source_candidates": [],
                    "editor_notes": [],
                },
                ensure_ascii=False,
            )

    context = _two_section_context()
    fake = RetryingSectionAI()

    generated = await chapter_generation.generate_chapter(
        context,
        ai_client=fake,
    )

    assert [section.section_id for section in generated.sections] == [
        "section-1",
        "section-2",
    ]
    assert fake.section_attempts == {"section-1": 1, "section-2": 2}
    assert len(fake.prompts) == 3
    assert "material-1" not in fake.prompts[0]
    assert "material-2" not in fake.prompts[1]
    assert "material_id: M1" in fake.prompts[0]
    assert "material_id: M1" in fake.prompts[1]
    assert "上次输出未通过校验" in fake.prompts[2]


async def test_chapter_generation_resumes_from_completed_section_checkpoint():
    class SectionAI(AITextServiceBase):
        provider_name = "test"
        model_name = "test-section-checkpoint"

        def __init__(self):
            self.prompts: list[str] = []

        async def _call_llm(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(user_prompt)
            material_id = next(
                line.removeprefix("material_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("material_id: ")
            )
            return json.dumps(
                {"body_digest": {"summary": "Saved body summary", "points": []},
                    "content": (
                        "那天我从家里出发。"
                        if "小节标题: 出发" in user_prompt
                        else "傍晚时我终于抵达。"
                    ),
                    "used_material_ids": [material_id],
                    "missing_source_candidates": [],
                    "editor_notes": [],
                },
                ensure_ascii=False,
            )

    checkpoint: dict[str, object] = {}

    async def fail_after_first_section(patch: dict[str, object]) -> None:
        checkpoint.update(patch)
        if patch.get("completed_section_count") == 1:
            raise RuntimeError("模拟任务在下一小节前中断")

    first = SectionAI()
    with pytest.raises(RuntimeError, match="模拟任务"):
        await chapter_generation.generate_chapter(
            _two_section_context(),
            ai_client=first,
            progress_callback=fail_after_first_section,
        )

    assert len(first.prompts) == 1
    assert checkpoint["completed_section_count"] == 1
    assert [
        section["section_id"]
        for section in checkpoint["checkpoint_sections"]
    ] == ["section-1"]

    second = SectionAI()
    completed = await chapter_generation.generate_chapter(
        _two_section_context(),
        ai_client=second,
        checkpoint=checkpoint,
    )

    assert len(second.prompts) == 1
    assert "小节标题: 抵达" in second.prompts[0]
    assert "小节标题: 出发" not in second.prompts[0]
    assert [section.section_id for section in completed.sections] == [
        "section-1",
        "section-2",
    ]


async def test_chapter_generation_reuses_unchanged_draft_sections():
    class ChangedSectionAI(AITextServiceBase):
        provider_name = "test"
        model_name = "test-incremental-section"

        def __init__(self):
            self.prompts: list[str] = []

        async def _call_llm(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(user_prompt)
            material_ids = [
                line.removeprefix("material_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("material_id: ")
            ]
            return json.dumps(
                {"body_digest": {"summary": "Saved body summary", "points": []},
                    "content": "这是补充素材整理后的第二小节正文。",
                    "used_material_ids": material_ids,
                    "missing_source_candidates": [],
                    "editor_notes": [],
                },
                ensure_ascii=False,
            )

    context = replace(_two_section_context(), polish_level=MemoirTranscriptPolishLevel.MEDIUM)
    latest = SimpleNamespace(
        model="previous-model",
        prompt_snapshot=chapter_generation._render_policy_tag(context),
        section_snapshot_json=[
            {
                "section_id": "section-1",
                "title": "出发",
                "section_type": "event",
                "section_version": 1,
                "sort_order": 0,
                "input_signature": (
                    chapter_generation._section_input_signature(context, context.sections[0])
                ),
                "content": "保留用户已经通读过的第一小节正文。",
                "used_material_ids": ["material-1"],
            },
            {
                "section_id": "section-2",
                "title": "抵达",
                "section_type": "event",
                "section_version": 1,
                "sort_order": 1,
                "content": "素材变化前的第二小节正文。",
                "used_material_ids": ["material-2"],
            },
        ],
        material_snapshot_json=chapter_generation._snapshot(
            context, used_material_ids={"material-1"}
        ),
    )
    plan = chapter_generation.chapter_draft_generation_plan(
        context,
        latest,
    )

    assert [
        section.section_id for section in plan.target_context.sections
    ] == ["section-2"]
    assert [
        section.section_id for section in plan.preserved_sections
    ] == ["section-1"]
    renamed_plan = chapter_generation.chapter_draft_generation_plan(
        replace(context, chapter_title="新的章节标题"),
        latest,
    )
    assert [
        section.section_id
        for section in renamed_plan.target_context.sections
    ] == ["section-2"]
    stronger_plan = chapter_generation.chapter_draft_generation_plan(
        replace(
            context,
            polish_level=MemoirTranscriptPolishLevel.HIGH,
        ),
        latest,
    )
    assert [
        section.section_id
        for section in stronger_plan.target_context.sections
    ] == ["section-2"]
    assert chapter_generation.chapter_draft_render_policy_current(context, latest)
    assert not chapter_generation.chapter_draft_render_policy_current(
        replace(context, polish_level=MemoirTranscriptPolishLevel.HIGH), latest
    )
    style_plan = chapter_generation.chapter_draft_generation_plan(
        replace(context, polish_level=MemoirTranscriptPolishLevel.HIGH),
        latest,
        refresh_unedited=True,
    )
    assert [section.section_id for section in style_plan.target_context.sections] == [
        "section-1", "section-2",
    ]
    reordered = replace(
        context,
        sections=(
            replace(context.sections[0], section_version=2, sort_order=2),
            context.sections[1],
        ),
    )
    reordered_plan = chapter_generation.chapter_draft_generation_plan(
        reordered, latest
    )
    assert [section.section_id for section in reordered_plan.target_context.sections] == [
        "section-2"
    ]

    fake = ChangedSectionAI()
    generated = await chapter_generation.generate_chapter(
        plan.target_context,
        ai_client=fake,
        chapter_sections=context.sections,
        preserved_sections=plan.preserved_sections,
        preserved_model=plan.previous_model,
    )

    assert len(fake.prompts) == 1
    assert "小节标题: 抵达" in fake.prompts[0]
    assert "material_id: M1" in fake.prompts[0]
    assert "material-1" not in fake.prompts[0]
    assert "material-2" not in fake.prompts[0]
    assert [section.content for section in generated.sections] == [
        "保留用户已经通读过的第一小节正文。",
        "这是补充素材整理后的第二小节正文。",
    ]


@pytest.mark.parametrize("engine", ["v2", "v3"])
def test_retired_signatures_require_generation_and_preserve_manual_review(engine):
    context = _two_section_context()
    snapshot = {
        "section_id": "section-1", "title": "出发", "section_type": "event",
        "input_signature": "retired-section-signature", "content": "用户修订的出发故事。",
        "used_material_ids": ["material-1"], "user_edited": True,
    }
    latest = SimpleNamespace(
        model="previous-model", created_by="ai",
        prompt_snapshot=f"llm_layered_sectioned_chapter_draft_{engine}",
        section_snapshot_json=[snapshot],
        material_snapshot_json=chapter_generation._snapshot(context, used_material_ids={"material-1"}),
    )
    plan = chapter_generation.chapter_draft_generation_plan(context, latest)
    assert not plan.preserved_sections
    assert [section.section_id for section in plan.target_context.sections] == ["section-1", "section-2"]
    assert plan.protected_target_section_ids == ("section-1",)
    assert plan.previous_sections[0].content == "用户修订的出发故事。"
    assert not chapter_generation.chapter_draft_render_policy_current(context, latest)


@pytest.mark.parametrize("input_kind", ["short", "long", "multiline"])
async def test_new_material_selects_only_evidence_backed_related_section(monkeypatch, input_kind):
    context = _two_section_context()
    if input_kind == "long":
        material = replace(context.materials[1], text="开头。" * 1500 + "终于抵达" * 100 + "后来。" * 1500)
        context = replace(context, materials=(context.materials[0], material), sections=(
            context.sections[0], replace(context.sections[1], materials=(material,))))
    evidence = "终于抵达"
    if input_kind == "multiline":
        material = replace(context.materials[1], text="到城里后\n\n我进了厂。")
        context = replace(context, materials=(context.materials[0], material), sections=(
            context.sections[0], replace(context.sections[1], materials=(material,))))
        evidence = "到城里后 我进了厂"
    latest = SimpleNamespace(
        material_snapshot_json=chapter_generation._snapshot(context, used_material_ids={"material-1"}),
    )
    changed = chapter_generation._changed_material_ids(context, latest)
    assert changed == {"material-2"}
    previous = chapter_generation.GeneratedChapterSection(
        section_id="section-1",
        title="出发",
        content=("出发。" * 200 + "终于抵达" * 50 + "结尾。" * 200) if input_kind == "long" else "那天我从家里出发，傍晚终于抵达了目的地。",
        used_material_ids=("material-1",),
    )
    if input_kind == "multiline":
        previous = replace(previous, content="到城里后  \n\n我进了厂。")
    plan = chapter_generation.ChapterDraftGenerationPlan(
        target_context=chapter_generation._single_section_context(context, context.sections[1]),
        preserved_sections=(previous,),
        previous_sections=(),
        protected_target_section_ids=(),
        has_unmapped_manual_content=False,
        previous_model=None,
    )
    result = chapter_generation._RelatedSectionSelection(sections=[
        chapter_generation._RelatedSectionTarget(
            section_id="S1",
            new_evidence=evidence,
            existing_evidence=evidence,
            reason="旧小节提前说了抵达结果，应把结果留给新增的抵达小节。",
        ),
    ])

    async def fake_selection(*args, **kwargs):
        assert evidence in args[2]
        return SimpleNamespace(value=result)

    monkeypatch.setattr(chapter_generation, "generate_structured_with_client", fake_selection)
    selected = await chapter_generation.select_related_sections(
        context, plan, changed_material_ids=changed, ai_model=None, ai_client=object()
    )
    assert selected == {"section-1": result.sections[0].reason}

    result.sections[0].existing_evidence = "没有出现的内容"
    assert await chapter_generation.select_related_sections(
        context, plan, changed_material_ids=changed, ai_model=None, ai_client=object()
    ) == {}


async def test_chapter_generation_does_not_copy_previous_section_boundary():
    repeated_boundary = (
        "就这样搞到二零零四年，不行了，已经太老了，七十几岁了。"
        "就回来，真的退休回来了。但退休回来又出去，再组织钢材。"
        "二零零四年，你们婆病了，癌症，病故了。二零零五年、"
        "二零零六年又在外面帮忙组织钢材。二零零六年，就跟温婆婆结婚了。"
    )
    repeated_boundary_with_newline = repeated_boundary.replace(
        "二零零四年，你们婆病了",
        "二零零四年，\n你们婆病了",
    )

    class BoundaryCopyingAI(AITextServiceBase):
        provider_name = "test"
        model_name = "test-boundary-copy"

        def __init__(self):
            self.prompts: list[str] = []

        async def _call_llm(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(user_prompt)
            material_id = next(
                line.removeprefix("material_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("material_id: ")
            )
            content = (
                repeated_boundary
                if "小节标题: 出发" in user_prompt
                else (
                    f"{repeated_boundary_with_newline}\n\n"
                    if "上次输出未通过校验" not in user_prompt
                    else ""
                )
                + "退休了以后，好多人到处聘我去。"
            )
            return json.dumps(
                {"body_digest": {"summary": "Saved body summary", "points": []},
                    "content": content,
                    "used_material_ids": [material_id],
                    "missing_source_candidates": [],
                    "editor_notes": [],
                },
                ensure_ascii=False,
            )

    fake = BoundaryCopyingAI()
    generated = await chapter_generation.generate_chapter(
        _two_section_context(),
        ai_client=fake,
    )

    assert len(fake.prompts) == 3
    assert repeated_boundary not in fake.prompts[0]
    assert repeated_boundary in fake.prompts[1]
    assert "完整正文" in fake.prompts[1]
    assert "小节开头大段重复了上一小节结尾" in fake.prompts[2]
    assert generated.sections[1].content == "退休了以后，好多人到处聘我去。"
    assert generated.content.count(repeated_boundary) == 1


def test_previous_section_context_uses_digest_for_long_body():
    context = _two_section_context()
    long_body = "这一段正文很长。" * 300

    result = chapter_generation.previous_section_context(
        section=context.sections[0],
        content=long_body,
        continuity=chapter_generation.ChapterSectionContinuity(
            summary="我从家中出发，同行的是老同事。",
            focus="这次出发意味着重新开始。",
            key_points=("老同事同行", "清晨从家出发"),
        ),
    )

    assert long_body not in result
    assert "摘要：我从家中出发，同行的是老同事。" in result
    assert "关键要素（人物、地点、事件等）：老同事同行；清晨从家出发" in result


async def _chapter(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(
        client,
        test_db,
        "朋友发来一张山桃花照片，后来邀请我一起爬山。",
        project_id=project_id,
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        "那天六个人一起爬了西山，正赶上花开。",
        project_id=project_id,
    )
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["山桃花开的心动"],
        source_refs_by_chapter=[
            [memoir_source_ref(first), memoir_source_ref(second)]
        ],
    )
    response = await client.post(
        f"/api/memoir/projects/{project_id}/sections/bootstrap"
    )
    assert response.status_code == 200
    return (
        project_id,
        chapters[0],
        response.json()["sections"][0],
        (first, second),
    )


async def test_chapter_digest_uses_section_digests_and_allowlists(client, test_db):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    fake = FakeChapterAI(
        section_material_id=materials[0]["utterance_id"],
        chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=[materials[0]["utterance_id"]],
    )

    await chapter_generation.generate_and_save_chapter_draft(test_db, project_id=project_id, chapter_id=chapter.id, ai_client=fake)
    digest = await chapter_digests.generate_or_reuse_chapter_digest(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_client=fake,
    )
    await test_db.commit()
    reused = await chapter_digests.generate_or_reuse_chapter_digest(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_client=fake,
    )

    assert digest.status == MemoirSemanticCacheStatus.READY.value
    assert reused.id == digest.id
    assert len(fake.prompts) == 2
    assert "朋友发来一张山桃花照片，后来邀请我一起爬山" in fake.prompts[0]
    assert "摘要:" in fake.prompts[1]
    assert "删掉重复的爬山经过" not in fake.prompts[1]
    assert "这次重新联系为什么重要" not in fake.prompts[1]
    assert section["id"] not in fake.prompts[1]
    assert "section_id: S1" in fake.prompts[1]
    assert digest.digest_json["candidate_issues"][0]["section_ids"] == [
        section["id"]
    ]
    issue = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.chapter_id == chapter.id,
        )
    )
    assert issue is not None
    assert issue.question == "这次见面对你意味着什么？"
    issues = list(
        (
            await test_db.scalars(
                select(MemoirCompletionIssue).where(
                    MemoirCompletionIssue.project_id == project_id,
                    MemoirCompletionIssue.chapter_id == chapter.id,
                )
            )
        ).all()
    )
    assert len(issues) == 1
    assert issues[0].scope == "chapter"


def test_chapter_digest_keeps_all_independent_user_questions():
    candidates = [
        {
            "issue_type": "clarity",
            "channel": "interview",
            "question": f"需要确认的独立问题{i}",
            "reason": f"问题{i}会影响整章理解。",
            "priority": "medium",
            "section_ids": ["S1"],
        }
        for i in range(5)
    ]

    normalized = chapter_digests.normalize_chapter_digest(
        {
            "summary": "S1 的章节摘要",
            "focus": "章节重点",
            "structure_guidance": [],
            "candidate_issues": candidates,
        },
        section_references=ModelReferenceMap(
            "S",
            ["section-1"],
            labels={"section-1": "第1小节《比赛》"},
        ),
    )

    assert len(normalized["candidate_issues"]) == 5
    assert normalized["summary"] == "第1小节《比赛》 的章节摘要"


def test_chapter_digest_ignores_included_section_without_materials():
    material = SimpleNamespace(utterance_id="material-1", include_in_book=True)
    assert book_output_snapshots.legacy_chapter_version_needs_upgrade(
        SimpleNamespace(content="旧正文", section_snapshot_json=[])
    )

    def section(section_id, title, order, materials):
        return SimpleNamespace(
            id=section_id, title=title, section_type="life_fragment",
            sort_order=order, status="active", include_in_book=True,
            materials=materials,
        )

    populated = section("section-1", "童年回忆", 0, [material])
    empty = section("section-2", "待补充的小节", 1, [])
    chapter = SimpleNamespace(
        id="chapter-1", project_id="project-1", title="童年回忆",
        sort_order=0, include_in_book=True, sections=[empty, populated],
    )
    context = chapter_digests.chapter_digest_context(
        chapter,
        section_digests_by_id={populated.id: SimpleNamespace(
            id="digest-1", input_signature="signature",
            digest_json={"summary": "童年往事。"},
        )},
    )

    assert [section.section_id for section in context.sections] == [populated.id]
    assert context.allowed_material_ids == frozenset({material.utterance_id})


def test_automatic_partial_book_keeps_legacy_flat_chapter():
    version = SimpleNamespace(
        id="version-1",
        draft_id="draft-1",
        version=1,
        created_at=datetime(2026, 9, 15),
        created_by="ai",
        feedback=None,
        content="这是旧格式保存的完整章节正文。",
        section_snapshot_json=[],
    )
    chapter = SimpleNamespace(
        id="chapter-1",
        title="童年回忆",
        sort_order=0,
        include_in_book=True,
        metadata_json={},
        draft=SimpleNamespace(
            versions=[version],
            status="draft",
            metadata_json={"confirmed_version_id": version.id},
        ),
    )
    plan = SimpleNamespace(chapters=[chapter])

    assert book_output_snapshots.readable_chapter_versions(
        plan,
        allow_partial=True,
    ) == [(chapter, version)]
    snapshot = book_output_snapshots.chapter_snapshot(
        chapter,
        version,
        chapter_index=1,
    )
    assert snapshot["sections"] == []
    assert snapshot["content"] == version.content
    assert "这是旧格式保存的完整章节正文。" in (
        book_output_snapshots.book_content_from_chapter_snapshots(
            title="我的回忆录",
            snapshots=[snapshot],
        )
    )

    chapter.draft.metadata_json = {}
    assert book_output_snapshots.readable_chapter_versions(
        plan,
        allow_partial=True,
    ) == []


async def test_prepare_chapter_preserves_section_digest_when_chapter_json_is_truncated(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    fake = TruncatedChapterDigestAI(
        section_material_id=materials[0]["utterance_id"],
        chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=[materials[0]["utterance_id"]],
    )
    monkeypatch.setattr(chapter_generation, "get_ai_service", lambda _model: fake)
    monkeypatch.setattr(section_digests, "get_ai_service", lambda _model: fake)
    monkeypatch.setattr(chapter_digests, "get_ai_service", lambda _model: fake)

    queued = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/jobs",
        json={
            "operation": "prepare",
            "ai_model": DEFAULT_AI_TEXT_MODEL,
        },
    )
    assert queued.status_code == 202, queued.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(
            test_db,
            queued.json()["job_id"],
        ) is False
    finally:
        clear_editing_job_handlers()
    response = await client.get(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/jobs/current",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_message"] == "章节审阅输出被截断，请重新检查本章"
    assert fake.chapter_max_tokens == [
        chapter_digests.CHAPTER_DIGEST_MAX_TOKENS,
        chapter_digests.CHAPTER_DIGEST_MAX_TOKENS,
    ]
    assert fake.chapter_max_tokens[0] == 8000

    section_digest = await section_digests.get_current_section_digest(test_db,
        project_id=project_id, section_id=section["id"])
    assert section_digest is not None
    chapter_digest = await test_db.scalar(
        select(MemoirChapterDigest).where(
            MemoirChapterDigest.chapter_id == chapter.id,
        )
    )
    assert chapter_digest is not None
    assert chapter_digest.status == MemoirSemanticCacheStatus.FAILED.value
    generation = chapter_digest.digest_json["generation"]
    assert generation["requested_model"] == DEFAULT_AI_TEXT_MODEL
    assert generation["provider"] == "test"
    assert generation["model"] == "test-chapter"
    assert generation["engine"] == chapter_digests.CHAPTER_DIGEST_ENGINE
    assert generation["timeout_seconds"] == 180.0
    assert generation["finish_reason"] == "length"
    assert generation["response_char_count"] == 28
    assert generation["attempt_count"] == 2
    assert generation["error_type"] == "AIOutputTruncatedError"


async def test_chapter_generation_saves_material_allowlist_snapshot(client, test_db):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    fake = FakeChapterAI(
        section_material_id=materials[0]["utterance_id"],
        chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=[materials[0]["utterance_id"]],
    )

    draft, version = await chapter_generation.generate_and_save_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_client=fake,
    )
    await test_db.commit()

    assert draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert version.used_material_ids_json == [material["utterance_id"] for material in materials]
    assert version.material_snapshot_json == [
        {
            "material_id": material["utterance_id"],
            "section_id": section["id"],
            "transcript_version_id": material["transcript_version_id"],
            "text_hash": version.material_snapshot_json[index]["text_hash"],
            "section_order": 0,
            "material_order": index,
        }
        for index, material in enumerate(materials)
    ]
    assert version.section_snapshot_json == [
        {
            "section_id": section["id"],
            "title": section["title"],
            "section_type": section["section_type"],
            "section_version": section["version"],
            "sort_order": section["sort_order"],
            "input_signature": version.section_snapshot_json[0][
                "input_signature"
            ],
            "content": (
                "朋友发来一张山桃花照片，后来我们一起去爬山。"
                "这次见面让一段疏远的关系重新有了联系。"
            ),
            "used_material_ids": [material["utterance_id"] for material in materials],
            **section_digests.digest_snapshot(version.section_snapshot_json[0]["content"],
                section_digests.snapshot_digest(version.section_snapshot_json[0])),
        }
    ]
    assert version.input_signature
    assert "朋友发来一张山桃花照片" in fake.prompts[0]
    assert "那天六个人一起爬了西山" in fake.prompts[0]

    persisted = await test_db.scalar(
        select(MemoirBookChapterDraftVersion).where(
            MemoirBookChapterDraftVersion.id == version.id
        )
    )
    assert persisted is not None
    assert persisted.input_signature == version.input_signature


async def test_chapter_draft_update_generates_only_section_with_new_material(
    client,
    test_db,
):
    class PerSectionAI(AITextServiceBase):
        provider_name = "test"
        model_name = "test-incremental-save"

        def __init__(self):
            self.prompts: list[str] = []

        async def _call_llm(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(user_prompt)
            material_ids = [
                line.removeprefix("material_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("material_id: ")
            ]
            material_ids = next(line for line in user_prompt.splitlines() if line.startswith("全部已归属素材编号")).split("：", 1)[1].split("、")
            return json.dumps(
                {"body_digest": {"summary": "Saved body summary", "points": []},
                    "content": (
                        f"这一小节使用了 {len(material_ids)} 条素材。"
                    ),
                    "used_material_ids": material_ids,
                    "missing_source_candidates": [],
                    "editor_notes": [],
                },
                ensure_ascii=False,
            )

    project_id, chapter, first_data, materials = await _chapter(
        client,
        test_db,
    )
    first = await test_db.get(MemoirSection, first_data["id"])
    assert first is not None
    second = MemoirSection(
        project_id=project_id,
        chapter_id=chapter.id,
        title="一起爬山",
        section_type="event",
        sort_order=1,
    )
    test_db.add(second)
    await test_db.flush()
    second_material = await test_db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id
            == materials[1]["utterance_id"],
        )
    )
    assert second_material is not None
    second_material.section_id = second.id
    second_material.sort_order = 0
    first.version += 1
    test_db.add_all([first, second_material])
    await test_db.commit()

    initial_ai = PerSectionAI()
    _, initial_version = await chapter_generation.generate_and_save_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_client=initial_ai,
    )
    await test_db.commit()
    assert len(initial_ai.prompts) == 2
    initial_sections = {
        item["section_id"]: item
        for item in initial_version.section_snapshot_json
    }

    added = await create_test_voice_transcript(
        client,
        test_db,
        "后来我又想起了爬山路上的一件小事。",
        project_id=project_id,
    )
    _, changed_ids = await sections.assign_material_to_section(
        test_db,
        project_id=project_id,
        section_id=second.id,
        utterance_id=added["utterance_id"],
        assigned_by=MemoirSectionMaterialAssignedBy.USER,
        assignment_reason="completion_issue_answer",
    )
    await sections.finalize_section_changes(test_db, project_id=project_id, section_ids=changed_ids)
    await test_db.commit()

    progress: list[dict[str, object]] = []

    async def record_progress(patch: dict[str, object]) -> None:
        progress.append(patch)

    update_ai = PerSectionAI()
    _, updated_version = await chapter_generation.generate_and_save_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_client=update_ai,
        progress_callback=record_progress,
    )
    await test_db.commit()

    assert len(update_ai.prompts) == 1
    assert first.id not in update_ai.prompts[0]
    assert second.id not in update_ai.prompts[0]
    updated_sections = {
        item["section_id"]: item
        for item in updated_version.section_snapshot_json
    }
    assert (
        updated_sections[first.id]["content"]
        == initial_sections[first.id]["content"]
    )
    assert updated_sections[second.id]["content"].endswith("使用了 2 条素材。")
    assert {patch["total_section_count"] for patch in progress} == {1}
    assert progress[-1]["completed_section_count"] == 1


async def test_excluded_material_is_not_sent_to_chapter_generator(client, test_db):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    excluded = await test_db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == materials[1]["utterance_id"],
        )
    )
    assert excluded is not None
    excluded.include_in_book = False
    await test_db.flush()
    fake = FakeChapterAI(
        section_material_id=materials[0]["utterance_id"],
        chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=[materials[0]["utterance_id"]],
    )

    await chapter_generation.generate_and_save_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_client=fake,
    )

    assert materials[1]["utterance_id"] not in fake.prompts[0]
    assert "那天六个人一起爬了西山" not in fake.prompts[0]


async def test_pending_assignment_material_is_excluded_without_blocking_draft(
    client,
    test_db,
):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    pending_section = MemoirSection(
        project_id=project_id,
        chapter_id=chapter.id,
        title=PENDING_ASSIGNMENT_SECTION_TITLE,
        section_type="life_fragment",
        sort_order=1,
        include_in_book=False,
        metadata_json={
            SECTION_SYSTEM_ROLE_KEY: PENDING_ASSIGNMENT_SECTION_ROLE,
        },
    )
    test_db.add(pending_section)
    await test_db.flush()
    pending_material = await test_db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id
            == materials[1]["utterance_id"],
        )
    )
    assert pending_material is not None
    pending_material.section_id = pending_section.id
    pending_material.sort_order = 0
    test_db.add(pending_material)
    await test_db.commit()
    fake = FakeChapterAI(
        section_material_id=materials[0]["utterance_id"],
        chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=[materials[0]["utterance_id"]],
    )

    draft, version = await chapter_generation.generate_and_save_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_client=fake,
    )
    await test_db.commit()

    assert version.used_material_ids_json == [materials[0]["utterance_id"]]
    assert materials[1]["utterance_id"] not in fake.prompts[0]
    assert "那天六个人一起爬了西山" not in fake.prompts[0]

    confirmed, _ = await chapter_generation.confirm_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    assert confirmed.id == draft.id
    assert confirmed.status == MemoirBookChapterDraftStatus.CONFIRMED.value


async def test_chapter_generator_rejects_material_outside_allowlist(
    client,
    test_db,
):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    fake = FakeChapterAI(
        section_material_id=materials[0]["utterance_id"],
        chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=["foreign-material"],
    )

    with pytest.raises(ValueError, match="unknown reference"):
        await chapter_generation.generate_and_save_chapter_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
            ai_client=fake,
        )
    draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter.id,
        )
    )
    assert draft is None


@pytest.mark.parametrize("via_voice", [False, True])
async def test_section_rename_preserves_confirmed_prose_and_voice_undo(client, test_db, monkeypatch, via_voice):
    from app.schemas.memoir_voice_edit import VoiceEditOperation
    from tests.test_memoir_voice_edit import preview, apply

    project_id, chapter, section, materials = await _chapter(client, test_db)
    fake = FakeChapterAI(section_material_id=materials[0]["utterance_id"], chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"], draft_material_ids=[m["utterance_id"] for m in materials])
    _, initial = await chapter_generation.generate_and_save_chapter_draft(
        test_db, project_id=project_id, chapter_id=chapter.id, ai_client=fake,
    )
    initial.section_snapshot_json = [{**item, "user_edited": True, "user_edited_at": "2026-01-02T00:00:00"}
                                     for item in initial.section_snapshot_json]
    await test_db.commit()
    draft, _ = await chapter_generation.confirm_chapter_draft(test_db, project_id=project_id, chapter_id=chapter.id)
    await test_db.commit()
    original_content, original_created_at = initial.content, initial.created_at
    original_snapshot = initial.section_snapshot_json[0]
    new_title = "重新联系与第一次见面"
    if via_voice:
        story = {"pid": project_id}
        operation = VoiceEditOperation(action="rename_section", target_ref=section["id"], new_title=new_title)
        proposal, _ = await preview(client, monkeypatch, story, [operation.model_dump()])
        assert proposal.status_code == 200, proposal.text
        result = await apply(client, story, proposal.json())
    else:
        result = await client.patch(f"/api/memoir/projects/{project_id}/sections/{section['id']}",
            json={"title": new_title, "expected_version": section["version"]})
    assert result.status_code == 200, result.text
    latest = await chapter_generation.latest_chapter_draft_version(test_db, project_id=project_id, chapter_id=chapter.id)
    context = await chapter_generation.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter.id)
    assert latest.content == original_content
    assert latest.created_at == original_created_at
    assert latest.section_snapshot_json[0]["title"] == new_title
    for key in ("content", "used_material_ids", "body_digest", "user_edited", "user_edited_at"):
        assert latest.section_snapshot_json[0].get(key) == original_snapshot.get(key)
    assert chapter_generation.chapter_draft_is_current(context, latest)
    assert not chapter_generation.chapter_draft_generation_plan(context, latest).target_context.sections
    assert chapter_generation.chapter_draft_render_policy_current(context, latest)
    snapshot = book_output_snapshots.chapter_snapshot(chapter, latest, chapter_index=1)
    assert snapshot["sections"][0]["title"] == new_title
    assert snapshot["sections"][0]["content"] == original_snapshot["content"]
    await test_db.refresh(draft)
    assert draft.status == "confirmed"
    assert draft.metadata_json["confirmed_version_id"] == latest.id
    if via_voice:
        result = await client.post(f"/api/memoir/projects/{project_id}/voice-edit/undo")
        assert result.status_code == 200, result.text
        latest = await chapter_generation.latest_chapter_draft_version(test_db, project_id=project_id, chapter_id=chapter.id)
        context = await chapter_generation.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter.id)
        assert latest.content == original_content
        assert latest.section_snapshot_json[0]["title"] == section["title"]
        assert chapter_generation.chapter_draft_is_current(context, latest)


async def test_section_rename_keeps_pending_material_update(client, test_db):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    fake = FakeChapterAI(section_material_id=materials[0]["utterance_id"], chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"], draft_material_ids=[m["utterance_id"] for m in materials])
    _, initial = await chapter_generation.generate_and_save_chapter_draft(
        test_db, project_id=project_id, chapter_id=chapter.id, ai_client=fake,
    )
    await test_db.commit()
    added = await create_test_voice_transcript(client, test_db, "后来我们又去爬了一次山。", project_id=project_id)
    await sections.assign_material_to_section(test_db, project_id=project_id, section_id=section["id"],
        utterance_id=added["utterance_id"], assigned_by=MemoirSectionMaterialAssignedBy.USER, assignment_reason="user")
    await test_db.commit()
    current = await test_db.get(MemoirSection, section["id"], populate_existing=True)
    result = await client.patch(f"/api/memoir/projects/{project_id}/sections/{section['id']}",
        json={"title": "改名后仍有新素材", "expected_version": current.version})
    assert result.status_code == 200, result.text
    latest = await chapter_generation.latest_chapter_draft_version(test_db, project_id=project_id, chapter_id=chapter.id)
    context = await chapter_generation.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter.id)
    assert latest.content == initial.content
    assert [s.section_id for s in chapter_generation.chapter_draft_generation_plan(context, latest).target_context.sections] == [section["id"]]


async def test_chapter_confirmation_rejects_changed_material_structure(
    client,
    test_db,
):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    fake = FakeChapterAI(
        section_material_id=materials[0]["utterance_id"],
        chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=[materials[0]["utterance_id"]],
    )
    await chapter_generation.generate_and_save_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_client=fake,
    )
    await test_db.commit()
    confirmed, _ = await chapter_generation.confirm_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
    )
    await test_db.commit()
    assert confirmed.status == MemoirBookChapterDraftStatus.CONFIRMED.value

    update = await client.patch(
        f"/api/memoir/projects/{project_id}/sections/{section['id']}",
        json={
            "title": "重新联系与第一次见面",
            "section_type": "viewpoint_reflection",
            "expected_version": section["version"],
        },
    )
    assert update.status_code == 200
    with pytest.raises(HTTPException) as exc:
        await chapter_generation.confirm_chapter_draft(
            test_db,
            project_id=project_id,
            chapter_id=chapter.id,
        )
    assert exc.value.status_code == 409
    assert "素材已变化" in exc.value.detail


@pytest.mark.ci_smoke
@pytest.mark.parametrize("section_edit_at", [None, datetime(2026, 1, 2)])
def test_confirmed_body_only_accepts_later_source_edits_and_new_narration(section_edit_at):
    from app.services.memoir.fact_decisions import FactDecision
    from app.services.memoir.transcript_projection import TranscriptEdit
    context = _two_section_context()
    material = replace(context.materials[0], text="我十五岁那年家里发洪水。",
        previous_text="我十五岁那年家里发洪水。",
        edits=(TranscriptEdit(datetime(2026, 1, 3), "原文旧称呼", "后来更正的称呼"),))
    new = replace(material, material_id="new-source", text="后来我们搬到绵阳。", edits=())
    old = FactDecision("old", "年龄？", "早先确认十五岁", frozenset({material.material_id}),
                       frozenset({context.chapter_id}), datetime(2026, 1, 1))
    recent = replace(old, issue_id="recent", answer="本次确认七岁", confirmed_at=datetime(2026, 1, 3))
    section = replace(context.sections[0], materials=(material, new), decisions=(old, recent))
    context = replace(context, sections=(section,), materials=(material, new),
                      previous_draft_at=datetime(2026, 1, 4) if section_edit_at else datetime(2026, 1, 2))
    prompt = chapter_generation.build_chapter_generation_prompt(context,         current_section_content="我五岁那年家里发洪水。", current_section_user_edited=True,
        current_section_user_edited_at=section_edit_at,
        previous_material_ids={material.material_id}, changed_material_ids={material.material_id, new.material_id})
    assert "我五岁那年家里发洪水。" in prompt
    assert "我十五岁那年家里发洪水。" not in prompt
    assert "早先确认十五岁" not in prompt
    assert "本次确认七岁" in prompt
    assert "后来更正的" in prompt
    assert new.text in prompt


@pytest.mark.ci_smoke
def test_confirmed_body_receives_only_recognition_delta():
    context = _two_section_context()
    material = replace(context.materials[0],
        previous_text="我十五岁时搬到成都。师傅叫王明。",
        text="我十五岁时搬到绵阳。师傅叫王明。")
    section = replace(context.sections[0], materials=(material,))
    context = replace(context, sections=(section,), materials=(material,))
    prompt = chapter_generation.build_chapter_generation_prompt(context,         current_section_content="我五岁时搬到成都。师傅叫李明。", current_section_user_edited=True,
        previous_material_ids={material.material_id}, changed_material_ids={material.material_id})
    assert "绵阳" in prompt
    assert "我五岁时搬到成都。师傅叫李明。" in prompt
    assert "原始讲述（本次新增或更正）" not in prompt
    assert "机器" in prompt
    assert "用户确认" in prompt


@pytest.mark.ci_smoke
def test_changed_confirmed_material_without_previous_text_is_not_silently_applied():
    context = _two_section_context()
    material = context.materials[0]
    context = replace(context, sections=(context.sections[0],), materials=(material,))
    prompt = chapter_generation.build_chapter_generation_prompt(context,         current_section_content="我五岁那年离开家。", current_section_user_edited=True,
        previous_material_ids={material.material_id}, changed_material_ids={material.material_id})
    assert "历史转写版本不可用" in prompt
    assert material.text not in prompt
    assert "我五岁那年离开家。" in prompt


@pytest.mark.parametrize("multiline,history", [(False, "pinned"), (True, "pinned"), (True, "unversioned"), (True, "mismatch"), (True, "future")])
async def test_recognition_delta_uses_version_pinned_by_previous_draft(client, test_db, multiline, history):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    from app.models.memoir import MemoirTranscriptVersion
    material = await test_db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.utterance_id == materials[0]["utterance_id"]))
    transcript = await test_db.get(MemoirTranscriptVersion, material.transcript_version_id)
    if multiline:
        transcript.content = "师傅教我修车。\n\n后来我  学会了拆轮胎。"
        await test_db.commit()
    fake = FakeChapterAI(section_material_id=materials[0]["utterance_id"],
        chapter_section_id=section["id"], chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=[materials[0]["utterance_id"]])
    _, version = await chapter_generation.generate_and_save_chapter_draft(test_db,
        project_id=project_id, chapter_id=chapter.id, ai_client=fake)
    context = await chapter_generation.load_chapter_generation_context(test_db,
        project_id=project_id, chapter_id=chapter.id)
    original = context.materials[0].text
    updated = replace(context.materials[0], text=original + "后来我学会了补胎。")
    changed = replace(context, materials=(updated,),
        sections=(replace(context.sections[0], materials=(updated,)),))
    if history != "pinned":
        version.material_snapshot_json = [{**item, "transcript_version_id": None} for item in version.material_snapshot_json]
    if history == "mismatch":
        transcript.content = "与快照不一致的历史文字。"
    elif history == "future":
        transcript.created_at = version.created_at + timedelta(seconds=1)
    await test_db.flush()
    # Ignore malformed metadata without discarding the valid pinned evidence.
    if not multiline:
        version.material_snapshot_json = [*version.material_snapshot_json, None, {"unrelated": True}]
    loaded = await chapter_generation._with_previous_material_text(test_db, changed, version)
    assert loaded.materials[0].previous_text == (None if history in {"mismatch", "future"} else original)
    assert loaded.materials[0].previous_interviewer_line == context.materials[0].interviewer_line
    assert loaded.sections[0].materials[0] == loaded.materials[0]
    assert loaded.materials[0].text.endswith("后来我学会了补胎。")


async def test_missing_history_is_visible_even_when_model_omits_warning():
    context = _two_section_context()
    material = context.materials[0]
    context = replace(context, sections=(context.sections[0],), materials=(material,))
    previous = chapter_generation.GeneratedChapterSection(section_id=material.section_id,
        title=material.section_title, content="我五岁那年离开家。",
        used_material_ids=(material.material_id,), user_edited=True, user_edited_at=datetime(2026, 1, 2))
    fake = FakeChapterAI(section_material_id=material.material_id,
        chapter_section_id=material.section_id, chapter_material_id=material.material_id,
        draft_material_ids=[material.material_id])
    result = await chapter_generation.generate_chapter(context,         previous_sections=(previous,), changed_material_ids={material.material_id}, ai_client=fake)
    assert any("机器更新未合并" in item for item in result.missing_source_candidates)
    assert result.sections[0].user_edited

    assert result.sections[0].user_edited_at == previous.user_edited_at
    snapshot = chapter_generation._section_snapshot(context, sections=result.sections)
    assert chapter_generation._previous_draft_section(context.sections[0], snapshot[0]) == result.sections[0]
    checkpoint = chapter_generation._generated_chapter_checkpoint(context, result, current_section=None)
    assert chapter_generation._generated_chapter_from_checkpoint(context, checkpoint).sections == result.sections


async def test_changed_interview_question_invalidates_generated_body(client, test_db):
    from app.models.memoir import MemoirChatMessage, MemoirVoiceUtterance
    project_id, chapter, section, materials = await _chapter(client, test_db)
    source = await test_db.get(MemoirVoiceUtterance, materials[0]["utterance_id"])
    answer = await test_db.get(MemoirChatMessage, source.chat_message_id)
    question = MemoirChatMessage(project_id=project_id, voice_session_id=answer.voice_session_id,
        role="assistant", content_text="是谁教您修车？", content_status="final", turn_index=answer.turn_index - 1)
    test_db.add(question)
    await test_db.commit()
    fake = FakeChapterAI(section_material_id=source.id, chapter_section_id=section["id"],
        chapter_material_id=source.id, draft_material_ids=[source.id])
    _, version = await chapter_generation.generate_and_save_chapter_draft(test_db,
        project_id=project_id, chapter_id=chapter.id, ai_client=fake)
    before = await chapter_generation.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter.id)
    assert question.content_text in fake.prompts[-1]
    assert not chapter_generation.chapter_draft_generation_plan(before, version).target_context.sections
    question.content_text = "是谁教您修摩托车？"
    await test_db.commit()
    after = await chapter_generation.load_chapter_generation_context(test_db, project_id=project_id, chapter_id=chapter.id)
    assert before.input_signature != after.input_signature
    assert chapter_generation.chapter_draft_generation_plan(after, version).target_context.sections
    assert source.id in chapter_generation._changed_material_ids(after, version)


@pytest.mark.parametrize("new_question", ["母亲当过老师吗？", ""])
@pytest.mark.parametrize("legacy", [False, True])
async def test_question_change_preserves_manual_body_and_prior_evidence_until_review(client, test_db, new_question, legacy):
    from app.models.memoir import MemoirChatMessage, MemoirTranscriptVersion, MemoirVoiceUtterance
    project_id, chapter, section, materials = await _chapter(client, test_db)
    source = await test_db.get(MemoirVoiceUtterance, materials[0]["utterance_id"])
    answer = await test_db.get(MemoirChatMessage, source.chat_message_id)
    transcript = await test_db.get(MemoirTranscriptVersion, materials[0]["transcript_version_id"])
    transcript.content = "没有。"
    question = MemoirChatMessage(project_id=project_id, voice_session_id=answer.voice_session_id,
        role="assistant", content_text="父亲当过老师吗？", content_status="final", turn_index=answer.turn_index - 1)
    test_db.add(question)
    await test_db.commit()
    fake = FakeChapterAI(section_material_id=source.id, chapter_section_id=section["id"],
        chapter_material_id=source.id, draft_material_ids=[source.id])
    _, version = await chapter_generation.generate_and_save_chapter_draft(test_db,
        project_id=project_id, chapter_id=chapter.id, ai_client=fake)
    version.section_snapshot_json = [{**item, **({} if legacy else {"user_edited": True}), "content": "父亲没有当过老师。"}
        for item in version.section_snapshot_json]
    if legacy:
        version.created_by = "user"
    version.content = "父亲没有当过老师。"
    await test_db.commit()
    question.content_text = new_question
    await test_db.commit()
    prior_snapshot = list(version.material_snapshot_json)
    prior_signature, prior_version_id = version.input_signature, version.id
    request_count = len(fake.prompts)
    with pytest.raises(HTTPException) as error:
        await chapter_generation.generate_and_save_chapter_draft(test_db,
            project_id=project_id, chapter_id=chapter.id, ai_client=fake)
    assert error.value.status_code == 409
    assert "人工正文尚待核对" in error.value.detail
    assert len(fake.prompts) == request_count
    latest = await chapter_generation.latest_chapter_draft_version(test_db,
        project_id=project_id, chapter_id=chapter.id)
    assert latest.id == prior_version_id and latest.input_signature == prior_signature
    assert latest.material_snapshot_json == prior_snapshot
    assert latest.content == "父亲没有当过老师。"
    context = await chapter_generation.load_chapter_generation_context(test_db,
        project_id=project_id, chapter_id=chapter.id)
    assert context.input_signature != latest.input_signature
    if not legacy:
        # Explicitly requesting a fresh chapter remains an available recovery.
        await chapter_generation.generate_and_save_chapter_draft(test_db,
            project_id=project_id, chapter_id=chapter.id, ai_client=fake,
            force_refresh=True)
        assert len(fake.prompts) > request_count
        return
    # Editing the affected section acknowledges the changed question without
    # discarding the user's other prose or accepting unrelated source changes.
    context = await chapter_generation.load_chapter_generation_context(test_db,
        project_id=project_id, chapter_id=chapter.id)
    assert chapter_generation.chapter_draft_question_changes(context, latest) == {section["id"]}
    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/revise",
        json={"expected_version_id": latest.id, "expected_input_signature": context.input_signature, "sections": [{"section_id": item["section_id"],
            "content": "母亲没有当过老师。这一点我已核对。"} for item in latest.section_snapshot_json]})
    assert response.status_code == 200, response.text
    revised = await chapter_generation.latest_chapter_draft_version(test_db,
        project_id=project_id, chapter_id=chapter.id)
    assert revised.content == "母亲没有当过老师。这一点我已核对。"
    assert chapter_generation.chapter_draft_is_current(context, revised)
    assert revised.material_snapshot_json[0].get("interviewer_line", "") == new_question


@pytest.mark.parametrize("legacy", [False, True])
async def test_manual_review_acknowledges_only_the_edited_sections_question(client, test_db, legacy):
    from app.models.memoir import MemoirChatMessage, MemoirVoiceUtterance
    project_id, chapter, first, materials = await _chapter(client, test_db)
    second = MemoirSection(project_id=project_id, chapter_id=chapter.id,
        title="母亲往事", section_type="event", sort_order=1)
    test_db.add(second)
    await test_db.flush()
    membership = await test_db.scalar(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.utterance_id == materials[1]["utterance_id"]))
    membership.section_id = second.id
    questions = []
    for material in materials[:2]:
        source = await test_db.get(MemoirVoiceUtterance, material["utterance_id"])
        answer = await test_db.get(MemoirChatMessage, source.chat_message_id)
        question = MemoirChatMessage(project_id=project_id, voice_session_id=answer.voice_session_id,
            role="assistant", content_text="父亲当过老师吗？", content_status="final", turn_index=answer.turn_index - 1)
        test_db.add(question)
        await test_db.flush()
        answer.parent_message_id = question.id
        questions.append(question)
    await test_db.commit()
    class ShortSectionAI(FakeChapterAI):
        async def _call_llm(self, system_prompt, user_prompt, **kwargs):
            payload = json.loads(await super()._call_llm(system_prompt, user_prompt, **kwargs))
            payload["content"] = "原有正文：" + next(line for line in user_prompt.splitlines() if line.startswith("小节标题:"))
            return json.dumps(payload, ensure_ascii=False)
    fake = ShortSectionAI(section_material_id=materials[0]["utterance_id"],
        chapter_section_id=first["id"], chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=[material["utterance_id"] for material in materials])
    _, version = await chapter_generation.generate_and_save_chapter_draft(test_db,
        project_id=project_id, chapter_id=chapter.id, ai_client=fake)
    if legacy:
        version.created_by = "user"
    else:
        version.section_snapshot_json = [{**item, "user_edited": True} for item in version.section_snapshot_json]
    stale_signature = version.input_signature
    for question in questions:
        question.content_text = "母亲当过老师吗？"
    await test_db.commit()
    for index, section_id in enumerate([first["id"], second.id]):
        context = await chapter_generation.load_chapter_generation_context(test_db,
            project_id=project_id, chapter_id=chapter.id)
        latest = await chapter_generation.latest_chapter_draft_version(test_db,
            project_id=project_id, chapter_id=chapter.id)
        if index == 0:
            for signature in [None, stale_signature]:
                response = await client.post(
                    f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/revise",
                    json={"expected_version_id": latest.id, "expected_input_signature": signature,
                        "sections": [{"section_id": item["section_id"], "content": item["content"] + "。"}
                            for item in latest.section_snapshot_json]})
                assert response.status_code == 409, response.text
        response = await client.post(
            f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/revise",
            json={"expected_version_id": latest.id, "expected_input_signature": context.input_signature, "sections": [{"section_id": item["section_id"],
                "content": "已核对本节关于母亲的讲述。" if item["section_id"] == section_id else item["content"]}
                for item in latest.section_snapshot_json]})
        assert response.status_code == 200, response.text
        latest = await chapter_generation.latest_chapter_draft_version(test_db,
            project_id=project_id, chapter_id=chapter.id)
        context = await chapter_generation.load_chapter_generation_context(test_db,
            project_id=project_id, chapter_id=chapter.id)
        assert chapter_generation.chapter_draft_is_current(context, latest) is bool(index)
        if index == 0:
            assert chapter_generation.chapter_draft_question_changes(context, latest) == {second.id}
            assert all(item.get("user_edited") is True for item in latest.section_snapshot_json)
            request_count = len(fake.prompts)
            with pytest.raises(HTTPException) as error:
                await chapter_generation.generate_and_save_chapter_draft(test_db,
                    project_id=project_id, chapter_id=chapter.id, ai_client=fake, automatic=True)
            assert error.value.status_code == 409
            assert len(fake.prompts) == request_count
            # An unrelated text update must not be accepted as question-only.
            changed = replace(context.materials[0], text="本次另外改了素材事实。")
            by_id = {item.material_id: item for item in (*context.materials, changed)}
            altered = replace(context, materials=tuple(by_id.values()), sections=tuple(
                replace(section, materials=tuple(by_id[item.material_id] for item in section.materials))
                for section in context.sections))
            assert chapter_generation.chapter_draft_question_changes(altered, latest) == set()


async def test_chapter_retry_receives_specific_structured_field_failure():
    class MissingContentAI(AITextServiceBase):
        provider_name = "test"
        model_name = "test"
        def __init__(self):
            self.prompts = []
        async def _call_llm(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(user_prompt)
            return json.dumps({"body_digest": {"summary": "Saved body summary", "points": []}, "used_material_ids": ["M1"], **({"content": "那天我从家里出发。"} if len(self.prompts) > 1 else {})})
    context = _two_section_context()
    ai = MissingContentAI()
    generated = await chapter_generation.generate_chapter_section(context, section=context.sections[0],
        chapter_sections=context.sections, ai_client=ai)
    assert generated.sections[0].content == "那天我从家里出发。"
    assert len(ai.prompts) == 2
    assert "content: 缺少必填字段 (missing)" in ai.prompts[1]


def test_incremental_prompt_uses_only_new_source_and_its_question():
    context = _two_section_context()
    old = context.materials[0]
    new = replace(old, material_id="new-material", text="后来又到了成都。", interviewer_line="后来去了哪里？")
    section = replace(context.sections[0], materials=(old, new))
    context = replace(context, sections=(section,), materials=(old, new))
    prompt = chapter_generation.build_chapter_generation_prompt(context,
        current_section_content="十八岁时我第一次离家。", previous_material_ids={old.material_id},
        changed_material_ids={new.material_id})
    assert new.text in prompt and new.interviewer_line in prompt
    assert old.text not in prompt
    assert "material_id: M1" not in prompt
    assert "material_id: M2" in prompt
    assert "十八岁时我第一次离家。" in prompt
    assert "M1、M2" in prompt


def test_generated_body_requires_summary_and_checkpoint_binds_it_to_text():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        chapter_generation._GeneratedChapterSectionOutput.model_validate({"content": "正文", "used_material_ids": ["M1"]})
    context = _two_section_context()
    section = context.sections[0]
    generated = chapter_generation.GeneratedChapterSection(section_id=section.section_id, title=section.title,
        content="正文", used_material_ids=(section.materials[0].material_id,), body_digest=MemoirBodyDigest(summary="正文摘要"))
    partial = chapter_generation._partial_generated_chapter([generated], missing_source_candidates=[], editor_notes=[], model="test")
    checkpoint = chapter_generation._generated_chapter_checkpoint(context, partial, current_section=None)
    restored = chapter_generation._generated_chapter_from_checkpoint(context, checkpoint)
    assert restored.sections[0].body_digest.summary == "正文摘要"
    checkpoint["checkpoint_sections"][0]["content"] = "其他正文"
    assert chapter_generation._generated_chapter_from_checkpoint(context, checkpoint) is None
