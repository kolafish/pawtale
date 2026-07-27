import json

from sqlalchemy import select

from app.domain.memoir.types import MemoirSemanticCacheStatus
from app.models.memoir import MemoirSectionDigest, MemoirSectionMaterial
from app.services.ai_timeouts import AIProviderTimeoutError
from app.services.memoir import section_digests
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)


class FakeDigestAI:
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


class TimeoutDigestAI(FakeDigestAI):
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
        raise AIProviderTimeoutError(
            provider=self.provider_name,
            model=self.model_name,
            timeout_seconds=timeout,
        )


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


def _valid_digest_payload(material_id: str) -> dict:
    return {
        "summary": "朋友发来山桃花照片，随后两人重新联系并一起参加爬山。",
        "focus": "一段疏远的关系如何重新搭上线。",
        "covered_points": ["重新联系的契机", "第一次共同活动"],
        "candidate_issues": [
            {
                "issue_type": "meaning",
                "question": "这次重新联系对你来说，最特别的地方是什么？",
                "reason": "现在经过已经清楚，再补充个人意义会让重点更鲜明。",
                "priority": "high",
                "material_ids": [material_id],
            }
        ],
    }


async def test_section_digest_signature_reuses_same_material_input(client, test_db):
    project_id, section, created = await _bootstrapped_section(client, test_db)
    fake = FakeDigestAI(_valid_digest_payload("M1"))

    first = await section_digests.generate_or_reuse_section_digest(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=fake,
    )
    await test_db.commit()
    second = await section_digests.generate_or_reuse_section_digest(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=fake,
    )
    await test_db.commit()

    assert second.id == first.id
    assert second.input_signature == first.input_signature
    assert fake.calls == 1
    assert fake.max_tokens == [4000]
    assert created[0]["utterance_id"] not in fake.prompts[0]
    assert "material_id: M1" in fake.prompts[0]
    assert "不要逐条检查时间、地点、人物" in fake.prompts[0]
    assert first.digest_json["candidate_issues"][0]["material_ids"] == [
        created[0]["utterance_id"]
    ]

    response = await client.get(
        f"/api/memoir/projects/{project_id}/sections/{section['id']}/digest"
    )
    assert response.status_code == 200
    assert response.json()["digest"]["focus"] == "一段疏远的关系如何重新搭上线。"


async def test_section_change_marks_previous_digest_stale(client, test_db):
    project_id, section, created = await _bootstrapped_section(client, test_db)
    fake = FakeDigestAI(_valid_digest_payload("M1"))
    digest = await section_digests.generate_or_reuse_section_digest(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=fake,
    )
    await test_db.commit()

    update = await client.patch(
        f"/api/memoir/projects/{project_id}/sections/{section['id']}",
        json={
            "title": "重新联系与第一次爬山",
            "expected_version": section["version"],
        },
    )
    assert update.status_code == 200

    stale = await test_db.get(MemoirSectionDigest, digest.id)
    assert stale is not None
    assert stale.status == MemoirSemanticCacheStatus.STALE.value
    current = await client.get(
        f"/api/memoir/projects/{project_id}/sections/{section['id']}/digest"
    )
    assert current.status_code == 404


async def test_section_digest_rejects_material_outside_allowlist(client, test_db):
    project_id, section, _ = await _bootstrapped_section(client, test_db)
    fake = FakeDigestAI(_valid_digest_payload("M99"))

    digest = await section_digests.generate_or_reuse_section_digest(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=fake,
    )
    await test_db.commit()

    assert digest.status == MemoirSemanticCacheStatus.FAILED.value
    assert digest.digest_json["generation"]["error_type"] == "ValueError"
    assert digest.error_message == "小节审阅服务暂时不可用，请稍后重试"


async def test_section_digest_timeout_persists_model_and_deadline(client, test_db):
    project_id, section, _ = await _bootstrapped_section(client, test_db)
    fake = TimeoutDigestAI({})

    digest = await section_digests.generate_or_reuse_section_digest(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_model="claude-opus-4-8",
        ai_client=fake,
    )
    await test_db.commit()

    generation = digest.digest_json["generation"]
    assert digest.status == MemoirSemanticCacheStatus.FAILED.value
    assert digest.error_message == "模型处理超过 180 秒，请稍后重试"
    assert generation["requested_model"] == "claude-opus-4-8"
    assert generation["provider"] == "test"
    assert generation["model"] == "test-section-digest"
    assert generation["timeout_seconds"] == 180.0
    assert generation["error_type"] == "AIProviderTimeoutError"


async def test_material_order_changes_section_digest_signature(client, test_db):
    project_id, section, created = await _bootstrapped_section(client, test_db)
    before = await section_digests.load_section_digest_context(
        test_db,
        project_id=project_id,
        section_id=section["id"],
    )

    move = await client.post(
        f"/api/memoir/projects/{project_id}/section-materials/{created[1]['utterance_id']}/move",
        json={
            "target_section_id": section["id"],
            "target_sort_order": 0,
        },
    )
    assert move.status_code == 200
    after = await section_digests.load_section_digest_context(
        test_db,
        project_id=project_id,
        section_id=section["id"],
    )

    assert before.input_signature != after.input_signature
    assert [item.material_id for item in after.materials] == [
        created[1]["utterance_id"],
        created[0]["utterance_id"],
    ]


async def test_excluded_material_is_not_sent_to_digest_model(client, test_db):
    project_id, section, created = await _bootstrapped_section(client, test_db)
    excluded = await test_db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.section_id == section["id"],
            MemoirSectionMaterial.utterance_id == created[1]["utterance_id"],
        )
    )
    assert excluded is not None
    excluded.include_in_book = False
    await test_db.flush()

    fake = FakeDigestAI(_valid_digest_payload("M1"))
    digest = await section_digests.generate_or_reuse_section_digest(
        test_db,
        project_id=project_id,
        section_id=section["id"],
        ai_client=fake,
    )
    await test_db.commit()

    assert digest.status == MemoirSemanticCacheStatus.READY.value
    assert created[0]["utterance_id"] not in fake.prompts[0]
    assert "material_id: M1" in fake.prompts[0]
    assert "朋友发来一张山桃花照片" in fake.prompts[0]
    assert created[1]["utterance_id"] not in fake.prompts[0]
    assert "那天六个人一起爬了西山" not in fake.prompts[0]
