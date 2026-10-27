import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from app.models.memoir import MemoirBookChapterDraftVersion, MemoirSectionDigest
from app.schemas.memoir_section_digests import MemoirBodyDigest
from app.services.ai_base import AITextServiceBase
from app.services.memoir import section_digests
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters, create_test_voice_transcript,
    create_test_chapter_draft, ensure_mobile_memoir_project, memoir_source_ref,
)

class FakeDigestAI(AITextServiceBase):
    provider_name = "test"
    model_name = "test-section-digest"

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0
        self.prompts: list[str] = []
        self.max_tokens: list[int] = []

    async def _call_llm(
        self,
        system_prompt,
        user_prompt,
        temperature=0.7,
        max_tokens=4000,
        timeout=60.0,
        return_raw=False,
    ):
        self.calls += 1
        self.prompts.append(user_prompt)
        self.max_tokens.append(max_tokens)
        return json.dumps(self.payload, ensure_ascii=False)


async def _bootstrapped_section(client, test_db):
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
    await create_test_book_plan_with_chapters(
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
    return project_id, response.json()["sections"][0], (first, second)


async def _body(client, test_db):
    project_id, section, created = await _bootstrapped_section(client, test_db)
    await create_test_chapter_draft(test_db, project_id=project_id, chapter_id=section["chapter_id"],
        content="他们全部都是新都本地人，并不是都姓杜。")
    version = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.project_id == project_id))
    return project_id, section, created, version


async def test_backfill_reads_only_body_and_reuses_cache(client, test_db):
    project_id, section, created, version = await _body(client, test_db)
    original = version.content
    fake = FakeDigestAI({"summary": "家人都是新都本地人，不是都姓杜。", "points": []})
    first = await section_digests.generate_or_reuse_section_digest(test_db,
        project_id=project_id, section_id=section["id"], ai_client=fake)
    await test_db.commit()
    second = await section_digests.generate_or_reuse_section_digest(test_db,
        project_id=project_id, section_id=section["id"], ai_client=fake)
    assert first.id == second.id
    assert fake.calls == 1
    assert original in fake.prompts[0]
    assert "朋友发来一张山桃花照片" not in fake.prompts[0]
    assert created[0]["utterance_id"] not in fake.prompts[0]
    assert version.content == original
    assert "editorial_guidance" not in first.digest_json


async def test_embedded_summary_is_reused_without_second_model_call(client, test_db):
    project_id, section, _, version = await _body(client, test_db)
    summary = MemoirBodyDigest(summary="家人来自新都。")
    version.section_snapshot_json = [{**s, **section_digests.digest_snapshot(s["content"], summary)}
        for s in version.section_snapshot_json]
    await test_db.flush()
    fake = FakeDigestAI({})
    first = await section_digests.get_current_section_digest(test_db, project_id=project_id, section_id=section["id"])
    second = await section_digests.generate_or_reuse_section_digest(test_db,
        project_id=project_id, section_id=section["id"], ai_client=fake)
    assert first.id == second.id
    assert first.created_at == second.created_at == version.created_at
    assert second.digest_json["summary"] == "家人来自新都。"
    assert fake.calls == 0
    assert not (await test_db.scalars(select(MemoirSectionDigest))).all()


async def test_body_edit_invalidates_old_summary_even_if_sources_unchanged(client, test_db):
    project_id, section, _, version = await _body(client, test_db)
    fake = FakeDigestAI({"summary": "家人来自新都。"})
    old = await section_digests.generate_or_reuse_section_digest(test_db,
        project_id=project_id, section_id=section["id"], ai_client=fake)
    old_signature = old.input_signature
    version.section_snapshot_json = [{**s, "content": "我后来住在成都。"} for s in version.section_snapshot_json]
    await test_db.flush()
    assert await section_digests.get_current_section_digest(test_db, project_id=project_id, section_id=section["id"]) is None
    current = await section_digests.generate_or_reuse_section_digest(test_db,
        project_id=project_id, section_id=section["id"], ai_client=fake)
    assert current.input_signature != old_signature
    assert fake.calls == 2


async def test_no_summary_before_body_exists(client, test_db):
    project_id, section, _ = await _bootstrapped_section(client, test_db)
    assert await section_digests.get_current_section_digest(test_db, project_id=project_id, section_id=section["id"]) is None
    with pytest.raises(HTTPException) as exc:
        await section_digests.generate_or_reuse_section_digest(test_db,
            project_id=project_id, section_id=section["id"], ai_client=FakeDigestAI({}))
    assert exc.value.status_code == 409


def test_summary_rejects_editorial_instructions_and_mismatched_body():
    with pytest.raises(ValidationError):
        MemoirBodyDigest.model_validate({"summary": "新都", "editorial_guidance": ["合并姓杜更正"]})
    data = {"content": "旧正文", **section_digests.digest_snapshot("旧正文", MemoirBodyDigest(summary="旧摘要"))}
    assert section_digests.snapshot_digest(data).summary == "旧摘要"
    assert section_digests.snapshot_digest({**data, "content": "新正文"}) is None


async def test_backfill_does_not_publish_summary_after_concurrent_body_edit(client, test_db):
    from sqlalchemy.ext.asyncio import AsyncSession
    project_id, section, _, version = await _body(client, test_db)
    version_id = version.id

    class ConcurrentEditAI(FakeDigestAI):
        async def _call_llm(self, *args, **kwargs):
            async with AsyncSession(test_db.bind) as writer:
                saved = await writer.get(MemoirBookChapterDraftVersion, version_id)
                saved.section_snapshot_json = [{**item, "content": "用户刚刚修改的正文。"} for item in saved.section_snapshot_json]
                await writer.commit()
            return await super()._call_llm(*args, **kwargs)

    with pytest.raises(HTTPException) as exc:
        await section_digests.generate_or_reuse_section_digest(test_db,
            project_id=project_id, section_id=section["id"], ai_client=ConcurrentEditAI({"summary": "过时摘要"}))
    assert exc.value.status_code == 409
    assert not (await test_db.scalars(select(MemoirSectionDigest))).all()
