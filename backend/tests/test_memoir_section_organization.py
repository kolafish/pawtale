import json

from sqlalchemy import select

from app.domain.memoir.types import MemoirChatMessageRole
from app.models.memoir import (
    MemoirChatMessage,
    MemoirSection,
    MemoirSectionMaterial,
)
from app.services.memoir.material_organization import organize_material
from app.services.memoir.utterance_editing_pipeline import source_text_hash
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)


class FakeMaterialOrganizationAI:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.calls = 0

    async def _call_llm(self, *_args, **_kwargs):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


async def _bootstrap_two_chapters(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(
        client,
        test_db,
        "朋友发来山桃花的照片，我后来答应一起去爬山。",
        project_id=project_id,
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        "那天我们六个人爬了西山，正赶上山桃花盛开。",
        project_id=project_id,
    )
    third = await create_test_voice_transcript(
        client,
        test_db,
        "工作后我从程序员转去做项目管理。",
        project_id=project_id,
    )
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["山桃花开的心动", "工作这些年"],
        source_refs_by_chapter=[
            [memoir_source_ref(first), memoir_source_ref(second)],
            [memoir_source_ref(third)],
        ],
    )
    response = await client.post(
        f"/api/memoir/projects/{project_id}/sections/bootstrap"
    )
    assert response.status_code == 200
    return project_id, chapters, (first, second, third), response.json()


async def test_bootstrap_creates_one_section_per_chapter_and_preserves_material(
    client,
    test_db,
):
    project_id, chapters, created, data = await _bootstrap_two_chapters(
        client,
        test_db,
    )

    assert data["project_id"] == project_id
    assert data["unassigned_material_count"] == 0
    assert [
        (section["chapter_id"], section["title"])
        for section in data["sections"]
    ] == [
        (chapters[0].id, "山桃花开的心动"),
        (chapters[1].id, "工作这些年"),
    ]
    assert [
        material["utterance_id"]
        for material in data["sections"][0]["materials"]
    ] == [created[0]["utterance_id"], created[1]["utterance_id"]]
    assert data["sections"][0]["materials"][0]["text"].startswith(
        "朋友发来山桃花"
    )

    repeated = await client.post(
        f"/api/memoir/projects/{project_id}/sections/bootstrap"
    )
    assert repeated.status_code == 200
    assert len(repeated.json()["sections"]) == 2
    assert len(
        (await test_db.execute(select(MemoirSectionMaterial))).scalars().all()
    ) == 3


async def test_split_move_merge_and_reorder_sections(client, test_db):
    project_id, chapters, created, bootstrapped = await _bootstrap_two_chapters(
        client,
        test_db,
    )
    first_section = bootstrapped["sections"][0]

    split = await client.post(
        f"/api/memoir/projects/{project_id}/sections/{first_section['id']}/split",
        json={
            "title": "第一次一起爬山",
            "section_type": "event",
            "utterance_ids": [created[1]["utterance_id"]],
        },
    )
    assert split.status_code == 200, split.text
    chapter_sections = [
        section
        for section in split.json()["sections"]
        if section["chapter_id"] == chapters[0].id
    ]
    assert [section["title"] for section in chapter_sections] == [
        "山桃花开的心动",
        "第一次一起爬山",
    ]
    assert [
        material["utterance_id"]
        for material in chapter_sections[1]["materials"]
    ] == [created[1]["utterance_id"]]

    extra = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/sections",
        json={
            "title": "后来的联系",
            "section_type": "life_fragment",
            "sort_order": 1,
        },
    )
    assert extra.status_code == 200
    chapter_sections = [
        section
        for section in extra.json()["sections"]
        if section["chapter_id"] == chapters[0].id
    ]
    assert [section["title"] for section in chapter_sections] == [
        "山桃花开的心动",
        "后来的联系",
        "第一次一起爬山",
    ]

    reordered = await client.put(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/sections/order",
        json={
            "section_ids": [
                chapter_sections[2]["id"],
                chapter_sections[0]["id"],
                chapter_sections[1]["id"],
            ]
        },
    )
    assert reordered.status_code == 200
    chapter_sections = [
        section
        for section in reordered.json()["sections"]
        if section["chapter_id"] == chapters[0].id
    ]
    assert [section["title"] for section in chapter_sections] == [
        "第一次一起爬山",
        "山桃花开的心动",
        "后来的联系",
    ]

    target = chapter_sections[2]
    moved = await client.post(
        f"/api/memoir/projects/{project_id}/section-materials/{created[1]['utterance_id']}/move",
        json={"target_section_id": target["id"], "target_sort_order": 0},
    )
    assert moved.status_code == 200
    by_id = {section["id"]: section for section in moved.json()["sections"]}
    assert [
        material["utterance_id"] for material in by_id[target["id"]]["materials"]
    ] == [created[1]["utterance_id"]]

    source_to_remove = next(
        section
        for section in moved.json()["sections"]
        if section["title"] == "第一次一起爬山"
    )
    merged = await client.post(
        f"/api/memoir/projects/{project_id}/sections/{first_section['id']}/merge",
        json={
            "source_section_ids": [target["id"], source_to_remove["id"]],
            "title": "山桃花与重新联系",
        },
    )
    assert merged.status_code == 200
    chapter_sections = [
        section
        for section in merged.json()["sections"]
        if section["chapter_id"] == chapters[0].id
    ]
    assert len(chapter_sections) == 1
    assert chapter_sections[0]["title"] == "山桃花与重新联系"
    assert {
        material["utterance_id"]
        for material in chapter_sections[0]["materials"]
    } == {created[0]["utterance_id"], created[1]["utterance_id"]}


async def test_section_api_rejects_cross_project_material(client, test_db):
    project_id, _, _, data = await _bootstrap_two_chapters(client, test_db)
    other_project = await client.post(
        "/api/mobile/memoir/projects",
        json={"title": "另一部回忆录"},
    )
    assert other_project.status_code == 201
    other_project_id = other_project.json()["id"]
    foreign_material = await create_test_voice_transcript(
        client,
        test_db,
        "这是另一部回忆录里的讲述。",
        project_id=other_project_id,
    )

    response = await client.post(
        f"/api/memoir/projects/{project_id}/sections/{data['sections'][0]['id']}/materials",
        json={"utterance_ids": [foreign_material["utterance_id"]]},
    )

    assert response.status_code == 404
    memberships = list(
        (
            await test_db.execute(
                select(MemoirSectionMaterial).where(
                    MemoirSectionMaterial.utterance_id
                    == foreign_material["utterance_id"]
                )
            )
        ).scalars().all()
    )
    assert memberships == []


async def test_section_api_excludes_and_rejects_interviewer_material(
    client,
    test_db,
):
    project_id, _, _, data = await _bootstrap_two_chapters(client, test_db)
    interviewer = await create_test_voice_transcript(
        client,
        test_db,
        "接下来可以讲讲你在工作里的变化吗？",
        project_id=project_id,
    )
    message = await test_db.get(MemoirChatMessage, interviewer["message_id"])
    assert message is not None
    message.role = MemoirChatMessageRole.INTERVIEWER.value
    test_db.add(message)
    await test_db.commit()

    listed = await client.get(f"/api/memoir/projects/{project_id}/sections")
    assert listed.status_code == 200
    assert listed.json()["unassigned_material_count"] == 0

    response = await client.post(
        f"/api/memoir/projects/{project_id}/sections/{data['sections'][0]['id']}/materials",
        json={"utterance_ids": [interviewer["utterance_id"]]},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "只有已有文字的叙述者素材可以归入小章节"


async def test_material_organization_appends_after_highest_section_order(
    client,
    test_db,
):
    project_id, chapters, _, data = await _bootstrap_two_chapters(client, test_db)
    existing = await test_db.get(MemoirSection, data["sections"][0]["id"])
    assert existing is not None
    existing.sort_order = 4
    test_db.add(existing)
    await test_db.commit()
    created = await create_test_voice_transcript(
        client,
        test_db,
        "后来我又和朋友去走了另一条山路。",
        project_id=project_id,
    )
    ai = FakeMaterialOrganizationAI(
        {
            "action": "create_section",
            "chapter_id": chapters[0].id,
            "title": "再次同行",
            "section_type": "event",
            "confidence": 0.9,
            "reason": "新的独立经历",
        }
    )

    result = await organize_material(
        test_db,
        project_id=project_id,
        utterance_id=created["utterance_id"],
        expected_text_hash=source_text_hash("后来我又和朋友去走了另一条山路。"),
        expected_transcript_version_id=created["transcript_version_id"],
        ai_client=ai,
    )

    section = await test_db.scalar(
        select(MemoirSection).where(
            MemoirSection.project_id == project_id,
            MemoirSection.title == "再次同行",
        )
    )
    assert result.result_json["section_id"] == section.id
    assert section.sort_order == 5
    assert ai.calls == 1


async def test_material_organization_skips_interviewer_material(
    client,
    test_db,
):
    project_id, _, _, _ = await _bootstrap_two_chapters(client, test_db)
    created = await create_test_voice_transcript(
        client,
        test_db,
        "你还记得当时有哪些人在场吗？",
        project_id=project_id,
    )
    message = await test_db.get(MemoirChatMessage, created["message_id"])
    assert message is not None
    message.role = MemoirChatMessageRole.INTERVIEWER.value
    test_db.add(message)
    await test_db.commit()
    ai = FakeMaterialOrganizationAI(
        {
            "action": "assign_existing",
            "section_id": "should-not-be-used",
        }
    )

    result = await organize_material(
        test_db,
        project_id=project_id,
        utterance_id=created["utterance_id"],
        expected_text_hash=source_text_hash("你还记得当时有哪些人在场吗？"),
        expected_transcript_version_id=created["transcript_version_id"],
        ai_client=ai,
    )

    assert result.result_json["reason"] == "not_narrator_material"
    assert ai.calls == 0


async def test_section_update_rejects_stale_version(client, test_db):
    project_id, _, _, data = await _bootstrap_two_chapters(client, test_db)
    section = data["sections"][0]

    updated = await client.patch(
        f"/api/memoir/projects/{project_id}/sections/{section['id']}",
        json={
            "title": "更新后的标题",
            "expected_version": section["version"],
        },
    )
    assert updated.status_code == 200

    stale = await client.patch(
        f"/api/memoir/projects/{project_id}/sections/{section['id']}",
        json={
            "title": "过期修改",
            "expected_version": section["version"],
        },
    )
    assert stale.status_code == 409
