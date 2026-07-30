import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, register_models
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirSectionMaterialAssignedBy,
    MemoirSemanticCacheStatus,
    MemoirTranscriptPolishLevel,
)
from app.migrations.memoir_chapters import (
    assert_memoir_chapter_schema,
    migrate_memoir_chapter_schema,
)
from app.migrations.runtime import migrate_runtime_schema
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirChapterDigest,
    MemoirCompletionIssue,
    MemoirSection,
    MemoirSectionDigest,
    MemoirSectionMaterial,
)
from app.services.ai_references import ModelReferenceMap
from app.services.memoir import (
    chapter_digests,
    chapter_generation,
    section_digests,
    sections,
)
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
)


class FakeChapterAI:
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
        if "请审阅下面这个回忆录小章节" in user_prompt:
            payload = {
                "summary": "朋友发来山桃花照片，随后一起参加爬山。",
                "focus": "一段疏远的关系重新搭上线。",
                "covered_points": ["重新联系", "第一次共同活动"],
                "editorial_guidance": ["删掉重复的爬山经过。"],
                "candidate_issues": [
                    {
                        "issue_type": "meaning",
                        "question": "这次重新联系为什么重要？",
                        "reason": "有助于理解这段关系。",
                        "priority": "medium",
                        "material_ids": prompt_material_ids[:1],
                    }
                ],
            }
        elif "请审阅回忆录章节" in user_prompt:
            payload = {
                "summary": "这一章讲重新联系和共同爬山。",
                "focus": "重新建立联系。",
                "structure_guidance": ["把照片邀请作为开头。"],
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
                "sections": [
                    {
                        "section_id": prompt_section_ids[0],
                        "content": (
                            "朋友发来一张山桃花照片，后来我们一起去爬山。"
                            "这次见面让一段疏远的关系重新有了联系。"
                        ),
                        "used_material_ids": (
                            ["M99"]
                            if self.draft_material_ids == ["foreign-material"]
                            else prompt_material_ids[
                                :len(self.draft_material_ids)
                            ]
                        ),
                    }
                ],
                "missing_source_candidates": [],
                "editor_notes": ["合并了重复描述。"],
            }
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
        if "请审阅回忆录章节" not in user_prompt:
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


def test_chapter_generation_preserves_explicit_section_boundaries():
    generated = chapter_generation.normalize_generated_chapter(
        {
            "sections": [
                {
                    "section_id": "S1",
                    "content": "那天我从家里出发。",
                    "used_material_ids": ["M1"],
                },
                {
                    "section_id": "S2",
                    "content": "傍晚时我终于抵达。",
                    "used_material_ids": ["M2"],
                },
            ]
        },
        context=_two_section_context(),
        model="test",
    )

    assert [section.section_id for section in generated.sections] == [
        "section-1",
        "section-2",
    ]
    assert generated.content == "那天我从家里出发。\n\n傍晚时我终于抵达。"
    assert generated.used_material_ids == ("material-1", "material-2")


def test_chapter_generation_prompt_requests_natural_paragraphs():
    context = replace(
        _two_section_context(),
        polish_level=MemoirTranscriptPolishLevel.HIGH,
    )
    prompt = chapter_generation.build_chapter_generation_prompt(
        context,
        editorial_guidance=[],
    )

    assert "正文根据内容自然分段，段落之间用换行分隔" in prompt
    assert "可较大幅度重组句式与表达层次" in prompt
    assert "按时间、场景、事件推进或感受转折" not in prompt
    assert "不把一条 Material 拆散后穿插到多处" not in prompt


def test_chapter_generation_rejects_reordered_sections():
    with pytest.raises(ValueError, match="complete section order"):
        chapter_generation.normalize_generated_chapter(
            {
                "sections": [
                    {
                        "section_id": "S2",
                        "content": "傍晚时我终于抵达。",
                        "used_material_ids": ["M2"],
                    },
                    {
                        "section_id": "S1",
                        "content": "那天我从家里出发。",
                        "used_material_ids": ["M1"],
                    },
                ]
            },
            context=_two_section_context(),
            model="test",
        )


def test_chapter_generation_rejects_cross_section_material():
    with pytest.raises(ValueError, match="outside allowlist"):
        chapter_generation.normalize_generated_chapter(
            {
                "sections": [
                    {
                        "section_id": "S1",
                        "content": "傍晚时我终于抵达。",
                        "used_material_ids": ["M2"],
                    },
                    {
                        "section_id": "S2",
                        "content": "那天我从家里出发。",
                        "used_material_ids": ["M1"],
                    },
                ]
            },
            context=_two_section_context(),
            model="test",
        )


async def test_chapter_generation_retries_only_the_invalid_section():
    class RetryingSectionAI:
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
            section_id = next(
                line.removeprefix("section_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("section_id: ")
            )
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
                {
                    "sections": [
                        {
                            "section_id": section_id,
                            "content": (
                                "第一小节的正文"
                                if target == "section-1"
                                else "第二小节的正文"
                            ),
                            "used_material_ids": used_material_ids,
                        }
                    ],
                    "missing_source_candidates": [],
                    "editor_notes": [],
                },
                ensure_ascii=False,
            )

    context = _two_section_context()
    fake = RetryingSectionAI()

    generated = await chapter_generation.generate_chapter(
        context,
        editorial_guidance=[],
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
    class SectionAI:
        provider_name = "test"
        model_name = "test-section-checkpoint"

        def __init__(self):
            self.prompts: list[str] = []

        async def _call_llm(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(user_prompt)
            section_id = next(
                line.removeprefix("section_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("section_id: ")
            )
            material_id = next(
                line.removeprefix("material_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("material_id: ")
            )
            return json.dumps(
                {
                    "sections": [{
                        "section_id": section_id,
                        "content": (
                            "那天我从家里出发。"
                            if "小节标题: 出发" in user_prompt
                            else "傍晚时我终于抵达。"
                        ),
                        "used_material_ids": [material_id],
                    }],
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
            editorial_guidance=[],
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
        editorial_guidance=[],
        ai_client=second,
        checkpoint=checkpoint,
    )

    assert len(second.prompts) == 1
    assert "小节标题: 抵达" in second.prompts[0]
    assert "小节标题: 出发" not in second.prompts[0]
    assert "section_id: S1" in second.prompts[0]
    assert [section.section_id for section in completed.sections] == [
        "section-1",
        "section-2",
    ]


async def test_chapter_generation_reuses_unchanged_draft_sections():
    class ChangedSectionAI:
        provider_name = "test"
        model_name = "test-incremental-section"

        def __init__(self):
            self.prompts: list[str] = []

        async def _call_llm(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(user_prompt)
            section_id = next(
                line.removeprefix("section_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("section_id: ")
            )
            material_ids = [
                line.removeprefix("material_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("material_id: ")
            ]
            return json.dumps(
                {
                    "sections": [{
                        "section_id": section_id,
                        "content": "这是补充素材整理后的第二小节正文。",
                        "used_material_ids": material_ids,
                    }],
                    "missing_source_candidates": [],
                    "editor_notes": [],
                },
                ensure_ascii=False,
            )

    context = _two_section_context()
    latest = SimpleNamespace(
        model="previous-model",
        section_snapshot_json=[
            {
                "section_id": "section-1",
                "title": "出发",
                "section_type": "event",
                "section_version": 1,
                "sort_order": 0,
                "input_signature": (
                    chapter_generation._section_input_signature(
                        context,
                        context.sections[0]
                    )
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
    ] == ["section-1", "section-2"]
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
    ] == ["section-1", "section-2"]

    fake = ChangedSectionAI()
    generated = await chapter_generation.generate_chapter(
        plan.target_context,
        editorial_guidance=[],
        ai_client=fake,
        chapter_sections=context.sections,
        preserved_sections=plan.preserved_sections,
        preserved_model=plan.previous_model,
    )

    assert len(fake.prompts) == 1
    assert "小节标题: 抵达" in fake.prompts[0]
    assert "section_id: S1" in fake.prompts[0]
    assert "material_id: M1" in fake.prompts[0]
    assert "material-1" not in fake.prompts[0]
    assert "material-2" not in fake.prompts[0]
    assert [section.content for section in generated.sections] == [
        "保留用户已经通读过的第一小节正文。",
        "这是补充素材整理后的第二小节正文。",
    ]


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

    class BoundaryCopyingAI:
        model_name = "test-boundary-copy"

        def __init__(self):
            self.prompts: list[str] = []

        async def _call_llm(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(user_prompt)
            section_id = next(
                line.removeprefix("section_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("section_id: ")
            )
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
                {
                    "sections": [{
                        "section_id": section_id,
                        "content": content,
                        "used_material_ids": [material_id],
                    }],
                    "missing_source_candidates": [],
                    "editor_notes": [],
                },
                ensure_ascii=False,
            )

    fake = BoundaryCopyingAI()
    generated = await chapter_generation.generate_chapter(
        _two_section_context(),
        editorial_guidance=[],
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


async def test_chapter_migration_adds_digest_and_draft_provenance():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("DROP TABLE memoir_chapter_digests"))
            await conn.execute(
                text(
                    "DROP INDEX "
                    "ix_memoir_book_chapter_draft_versions_input_signature"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_chapter_draft_versions "
                    "DROP COLUMN input_signature"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_chapter_draft_versions "
                    "DROP COLUMN used_material_ids_json"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_chapter_draft_versions "
                    "DROP COLUMN material_snapshot_json"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE memoir_book_chapter_draft_versions "
                    "DROP COLUMN section_snapshot_json"
                )
            )

        first = await migrate_memoir_chapter_schema(engine)
        second = await migrate_memoir_chapter_schema(engine)
        assert first["changed_tables"] == ["memoir_chapter_digests"]
        assert len(first["changed_columns"]) == 4
        assert second["changed_tables"] == []
        assert second["changed_columns"] == []
        await assert_memoir_chapter_schema(engine)
    finally:
        await engine.dispose()


async def test_runtime_cutover_preserves_legacy_draft_rows():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "DROP INDEX "
                    "ix_memoir_book_chapter_draft_versions_input_signature"
                )
            )
            for column_name in (
                "input_signature",
                "used_material_ids_json",
                "material_snapshot_json",
                "section_snapshot_json",
            ):
                await conn.execute(
                    text(
                        "ALTER TABLE memoir_book_chapter_draft_versions "
                        f"DROP COLUMN {column_name}"
                    )
                )
            await conn.execute(
                text(
                    "INSERT INTO projects ("
                    "id, type, title, metadata_json, created_at, updated_at"
                    ") VALUES ("
                    "'project-1', 'memoir', '旧项目', '{}', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_book_plans ("
                    "id, project_id, title, status, created_by, "
                    "prompt_snapshot, metadata_json, created_at, updated_at"
                    ") VALUES ("
                    "'plan-1', 'project-1', '旧规划', 'confirmed', 'user', "
                    "'', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_book_plan_chapters ("
                    "id, plan_id, project_id, title, sort_order, "
                    "include_in_book, metadata_json, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "'chapter-1', 'plan-1', 'project-1', '旧章节', 0, "
                    "1, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_book_chapter_drafts ("
                    "id, project_id, chapter_id, status, metadata_json, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "'draft-1', 'project-1', 'chapter-1', 'draft', '{}', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO memoir_book_chapter_draft_versions ("
                    "id, draft_id, project_id, chapter_id, version, content, "
                    "created_by, model, prompt_snapshot, feedback, created_at"
                    ") VALUES ("
                    "'version-1', 'draft-1', 'project-1', 'chapter-1', 1, "
                    "'不能丢失的旧正文', 'user', NULL, '', NULL, CURRENT_TIMESTAMP"
                    ")"
                )
            )

        result = await migrate_runtime_schema(engine)

        assert (
            "memoir_book_chapter_draft_versions"
            in result["memoir_schema_cutover"]["rebuilt_tables"]
        )
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT content, input_signature, "
                        "used_material_ids_json, material_snapshot_json, "
                        "section_snapshot_json "
                        "FROM memoir_book_chapter_draft_versions "
                        "WHERE id = 'version-1'"
                    )
                )
            ).one()
            temp = await conn.scalar(
                text(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = "
                    "'_memoir_book_chapter_draft_versions_before_cutover'"
                )
            )
        assert row == ("不能丢失的旧正文", "", "[]", "[]", "[]")
        assert temp is None
    finally:
        await engine.dispose()


async def test_chapter_digest_uses_section_digests_and_allowlists(client, test_db):
    project_id, chapter, section, materials = await _chapter(client, test_db)
    fake = FakeChapterAI(
        section_material_id=materials[0]["utterance_id"],
        chapter_section_id=section["id"],
        chapter_material_id=materials[0]["utterance_id"],
        draft_material_ids=[materials[0]["utterance_id"]],
    )

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
    assert "原始讲述" in fake.prompts[0]
    assert "摘要:" in fake.prompts[1]
    assert "删掉重复的爬山经过" in fake.prompts[1]
    assert "这次重新联系为什么重要" in fake.prompts[1]
    assert section["id"] not in fake.prompts[1]
    assert "section_id: S1" in fake.prompts[1]
    assert digest.digest_json["candidate_issues"][0]["section_ids"] == [
        section["id"]
    ]
    issue = await test_db.scalar(
        select(MemoirCompletionIssue).where(
            MemoirCompletionIssue.project_id == project_id,
            MemoirCompletionIssue.chapter_id == chapter.id,
            MemoirCompletionIssue.section_id.is_(None),
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
            "summary": "章节摘要",
            "focus": "章节重点",
            "structure_guidance": [],
            "candidate_issues": candidates,
        },
        section_references=ModelReferenceMap("S", ["section-1"]),
    )

    assert len(normalized["candidate_issues"]) == 5


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
    monkeypatch.setattr(section_digests, "get_ai_service", lambda _model: fake)
    monkeypatch.setattr(chapter_digests, "get_ai_service", lambda _model: fake)

    response = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter.id}/prepare",
        json={"ai_model": "claude-opus-4-8"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "章节审阅输出被截断，请重新检查本章"
    assert fake.chapter_max_tokens == [
        chapter_digests.CHAPTER_DIGEST_MAX_TOKENS,
        chapter_digests.CHAPTER_DIGEST_MAX_TOKENS,
    ]
    assert fake.chapter_max_tokens[0] == 8000

    section_digest = await test_db.scalar(
        select(MemoirSectionDigest).where(
            MemoirSectionDigest.section_id == section["id"],
            MemoirSectionDigest.status == MemoirSemanticCacheStatus.READY.value,
        )
    )
    assert section_digest is not None
    chapter_digest = await test_db.scalar(
        select(MemoirChapterDigest).where(
            MemoirChapterDigest.chapter_id == chapter.id,
        )
    )
    assert chapter_digest is not None
    assert chapter_digest.status == MemoirSemanticCacheStatus.FAILED.value
    generation = chapter_digest.digest_json["generation"]
    assert generation["requested_model"] == "claude-opus-4-8"
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
        editorial_guidance=["把照片邀请作为开头。"],
        ai_client=fake,
    )
    await test_db.commit()

    assert draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert version.used_material_ids_json == [materials[0]["utterance_id"]]
    assert version.material_snapshot_json == [
        {
            "material_id": materials[0]["utterance_id"],
            "section_id": section["id"],
            "transcript_version_id": materials[0]["transcript_version_id"],
            "text_hash": version.material_snapshot_json[0]["text_hash"],
            "section_order": 0,
            "material_order": 0,
        }
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
            "used_material_ids": [materials[0]["utterance_id"]],
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
    class PerSectionAI:
        provider_name = "test"
        model_name = "test-incremental-save"

        def __init__(self):
            self.prompts: list[str] = []

        async def _call_llm(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(user_prompt)
            section_id = next(
                line.removeprefix("section_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("section_id: ")
            )
            material_ids = [
                line.removeprefix("material_id: ").strip()
                for line in user_prompt.splitlines()
                if line.startswith("material_id: ")
            ]
            return json.dumps(
                {
                    "sections": [{
                        "section_id": section_id,
                        "content": (
                            f"这一小节使用了 {len(material_ids)} 条素材。"
                        ),
                        "used_material_ids": material_ids,
                    }],
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
        editorial_guidance=[],
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
    await sections.assign_material_to_section(
        test_db,
        project_id=project_id,
        section_id=second.id,
        utterance_id=added["utterance_id"],
        assigned_by=MemoirSectionMaterialAssignedBy.USER,
        assignment_reason="completion_issue_answer",
    )
    await test_db.commit()

    progress: list[dict[str, object]] = []

    async def record_progress(patch: dict[str, object]) -> None:
        progress.append(patch)

    update_ai = PerSectionAI()
    _, updated_version = await chapter_generation.generate_and_save_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        editorial_guidance=[],
        ai_client=update_ai,
        progress_callback=record_progress,
    )
    await test_db.commit()

    assert len(update_ai.prompts) == 1
    assert "section_id: S1" in update_ai.prompts[0]
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
        editorial_guidance=[],
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
        editorial_guidance=[],
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
            editorial_guidance=[],
            ai_client=fake,
        )
    draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(
            MemoirBookChapterDraft.project_id == project_id,
            MemoirBookChapterDraft.chapter_id == chapter.id,
        )
    )
    assert draft is None


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
        editorial_guidance=[],
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
