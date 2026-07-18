import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base, register_models
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirSemanticCacheStatus,
)
from app.migrations.memoir_chapters import (
    assert_memoir_chapter_schema,
    migrate_memoir_chapter_schema,
)
from app.migrations.runtime import migrate_runtime_schema
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirCompletionIssue,
    MemoirSectionMaterial,
)
from app.services.memoir import chapter_digests, chapter_generation
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
        if "请审阅下面这个回忆录小章节" in user_prompt:
            payload = {
                "summary": "朋友发来山桃花照片，随后一起参加爬山。",
                "focus": "一段疏远的关系重新搭上线。",
                "covered_points": ["重新联系", "第一次共同活动"],
                "candidate_issues": [],
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
                        "section_ids": [self.chapter_section_id],
                        "material_ids": [self.chapter_material_id],
                    }
                ],
            }
        else:
            payload = {
                "content": "朋友发来一张山桃花照片，后来我们一起去爬山。"
                "这次见面让一段疏远的关系重新有了联系。",
                "used_material_ids": self.draft_material_ids,
                "missing_source_candidates": [],
                "editor_notes": ["合并了重复描述。"],
            }
        return json.dumps(payload, ensure_ascii=False)


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

        first = await migrate_memoir_chapter_schema(engine)
        second = await migrate_memoir_chapter_schema(engine)
        assert first["changed_tables"] == ["memoir_chapter_digests"]
        assert len(first["changed_columns"]) == 3
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
                    "include_in_book, source_refs_json, metadata_json, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "'chapter-1', 'plan-1', 'project-1', '旧章节', 0, "
                    "1, '[]', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
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
                        "used_material_ids_json, material_snapshot_json "
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
        assert row == ("不能丢失的旧正文", "", "[]", "[]")
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

    with pytest.raises(ValueError, match="outside allowlist"):
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
