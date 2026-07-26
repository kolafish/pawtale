import json
import re

import pytest
from sqlalchemy import select

from app.domain.memoir.types import MemoirChatMessageRole
from app.models.memoir import (
    MemoirChatMessage,
    MemoirSection,
    MemoirSectionMaterial,
)
from app.services.memoir.material_organization import organize_material
from app.services.memoir import section_regeneration
from app.services.memoir.editing_job_handlers import (
    register_memoir_editing_job_handlers,
)
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from app.services.memoir.utterance_editing_pipeline import source_text_hash
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
    run_test_editing_job,
)


class FakeMaterialOrganizationAI:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.calls = 0

    async def _call_llm(self, *_args, **_kwargs):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


class ReplayMaterialOrganizationAI:
    provider_name = "fake"
    model_name = "fake-section-organizer"

    def __init__(self, *, leave_unassigned: bool = False):
        self.leave_unassigned = leave_unassigned
        self.prompts: list[str] = []

    def _should_leave_unassigned(self, _index: int) -> bool:
        return self.leave_unassigned

    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        self.prompts.append(user_prompt)
        if "这里只设计候选小节" in user_prompt:
            return json.dumps(
                {
                    "sections": [{
                        "title": "山桃花里的重逢",
                        "section_type": "event",
                    }],
                },
                ensure_ascii=False,
            )

        raw_materials = re.search(
            r"待归类讲述：\n(\[.*\])\n\n只返回 JSON",
            user_prompt,
            re.DOTALL,
        )
        section = re.search(r'"section_id": "([^"]+)"', user_prompt)
        assert raw_materials is not None
        assert section is not None
        materials = json.loads(raw_materials.group(1))
        return json.dumps(
            {
                "assignments": [
                    {
                        "utterance_id": material["utterance_id"],
                        "action": (
                            "leave_unassigned"
                            if self._should_leave_unassigned(index)
                            else "assign_existing"
                        ),
                        "section_id": (
                            None
                            if self._should_leave_unassigned(index)
                            else section.group(1)
                        ),
                        "confidence": 0.86,
                        "reason": (
                            "暂不归章"
                            if self._should_leave_unassigned(index)
                            else "延续同一次山桃花爬山经历"
                        ),
                    }
                    for index, material in enumerate(materials)
                ],
            },
            ensure_ascii=False,
        )


class FailingReplayMaterialOrganizationAI(ReplayMaterialOrganizationAI):
    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        if self.prompts:
            raise RuntimeError("provider unavailable")
        return await super()._call_llm(
            _system_prompt,
            user_prompt,
            **_kwargs,
        )


class PartiallyUnassignedReplayMaterialOrganizationAI(
    ReplayMaterialOrganizationAI
):
    def _should_leave_unassigned(self, index: int) -> bool:
        return index > 0


def test_section_regeneration_batches_by_material_text_budget():
    production_lengths = [
        8, 15, 20, 24, 32, 33, 47, 64, 70, 78, 80, 83, 101,
        108, 109, 112, 113, 114, 119, 126, 160, 164, 176, 199,
        356, 600,
    ]

    def snapshots(lengths):
        return [
            section_regeneration._MaterialSnapshot(
                utterance_id=str(index),
                text="字" * length,
                text_hash=str(length),
                transcript_version_id=None,
                include_in_book=True,
            )
            for index, length in enumerate(lengths)
        ]

    production_materials = snapshots(production_lengths)
    production_batches = section_regeneration._material_batches(
        production_materials
    )
    assert [len(batch) for batch in production_batches] == [26]

    oversized_combination = snapshots([5_000, 4_000])
    split_batches = section_regeneration._material_batches(
        oversized_combination
    )
    assert [len(batch) for batch in split_batches] == [1, 1]


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


async def test_material_placement_can_create_section_and_chapter_atomically(
    client,
    test_db,
):
    project_id, chapters, created, _ = await _bootstrap_two_chapters(
        client,
        test_db,
    )

    created_section = await client.post(
        f"/api/memoir/projects/{project_id}/section-materials/"
        f"{created[1]['utterance_id']}/place",
        json={
            "target_chapter_id": chapters[0].id,
            "new_section_title": "第一次一起爬山",
        },
    )
    assert created_section.status_code == 200, created_section.text
    assert created_section.json()["chapter_id"] == chapters[0].id
    assert created_section.json()["created_chapter"] is False
    assert created_section.json()["created_section"] is True

    created_chapter = await client.post(
        f"/api/memoir/projects/{project_id}/section-materials/"
        f"{created[0]['utterance_id']}/place",
        json={
            "new_chapter_title": "山桃花开的春天",
            "new_section_title": "照片里的邀约",
        },
    )
    assert created_chapter.status_code == 200, created_chapter.text
    assert created_chapter.json()["created_chapter"] is True
    assert created_chapter.json()["created_section"] is True

    plan_response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-plan"
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    new_chapter = plan["chapters"][-1]
    assert new_chapter["title"] == "山桃花开的春天"
    assert [
        ref["utterance_id"] for ref in new_chapter["source_refs"]
    ] == [created[0]["utterance_id"]]
    assert new_chapter["sections"][0]["title"] == "照片里的邀约"

    sections_response = await client.get(
        f"/api/memoir/projects/{project_id}/sections"
    )
    assert sections_response.status_code == 200, sections_response.text
    sections = sections_response.json()["sections"]
    new_section = next(
        section
        for section in sections
        if section["id"] == created_section.json()["section_id"]
    )
    assert new_section["title"] == "第一次一起爬山"
    assert [
        material["utterance_id"] for material in new_section["materials"]
    ] == [created[1]["utterance_id"]]

    excluded_section_response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[1].id}/sections",
        json={"title": "不参与成书的小节"},
    )
    excluded_section = next(
        section
        for section in excluded_section_response.json()["sections"]
        if section["title"] == "不参与成书的小节"
    )
    excluded_section_response = await client.patch(
        f"/api/memoir/projects/{project_id}/sections/{excluded_section['id']}",
        json={
            "include_in_book": False,
            "expected_version": excluded_section["version"],
        },
    )
    assert excluded_section_response.status_code == 200
    moved_to_excluded = await client.post(
        f"/api/memoir/projects/{project_id}/section-materials/"
        f"{created[1]['utterance_id']}/move",
        json={"target_section_id": excluded_section["id"]},
    )
    assert moved_to_excluded.status_code == 200

    rejected = await client.post(
        f"/api/memoir/projects/{project_id}/section-materials/"
        f"{created[2]['utterance_id']}/place",
        json={
            "new_chapter_title": "不应留下的空章节",
            "new_section_title": "唯一素材",
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "原章节至少需要保留 1 条素材"
    unchanged_plan = (
        await client.get(
            f"/api/mobile/memoir/projects/{project_id}/book-plan"
        )
    ).json()
    assert len(unchanged_plan["chapters"]) == 3


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


async def test_section_regeneration_job_reports_progress_and_deduplicates(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _, data = await _bootstrap_two_chapters(
        client,
        test_db,
    )
    chapter_id = chapters[0].id
    fake = ReplayMaterialOrganizationAI()
    monkeypatch.setattr(
        section_regeneration,
        "get_ai_service",
        lambda _model=None: fake,
    )
    url = (
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/"
        "section-regeneration-jobs"
    )
    payload = {
        "expected_sections": [
            {
                "id": data["sections"][0]["id"],
                "version": data["sections"][0]["version"],
            }
        ],
        "ai_model": "deepseek-v4-flash",
    }

    queued = await client.post(url, json=payload)
    duplicate = await client.post(url, json=payload)

    assert queued.status_code == 202, queued.text
    assert duplicate.status_code == 202, duplicate.text
    state = queued.json()
    assert state["job_id"] == duplicate.json()["job_id"]
    assert state["status"] == "pending"
    assert state["material_count"] == 2
    assert state["batch_count"] == 1
    assert state["requested_ai_model"] == "deepseek-v4-flash"

    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, state["job_id"]) is True
    finally:
        clear_editing_job_handlers()

    completed = await client.get(f"{url}/current")
    assert completed.status_code == 200, completed.text
    result = completed.json()
    assert result["status"] == "succeeded"
    assert result["stage"] == "completed"
    assert result["processed_material_count"] == 2
    assert result["processed_char_count"] == result["total_char_count"]
    assert result["completed_batch_count"] == 1
    assert result["section_count"] == 1
    assert result["resolved_ai_model"] == "fake-section-organizer"
    assert len(fake.prompts) == 2


async def test_section_regeneration_job_can_be_cancelled_before_run(
    client,
    test_db,
):
    project_id, chapters, _, data = await _bootstrap_two_chapters(
        client,
        test_db,
    )
    chapter_id = chapters[0].id
    url = (
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/"
        "section-regeneration-jobs"
    )
    queued = await client.post(
        url,
        json={
            "expected_sections": [
                {
                    "id": data["sections"][0]["id"],
                    "version": data["sections"][0]["version"],
                }
            ],
        },
    )
    job_id = queued.json()["job_id"]

    cancelled = await client.delete(f"{url}/{job_id}")

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["stage"] == "cancelled"
    assert cancelled.json()["can_cancel"] is False


async def test_regenerate_chapter_sections_replays_current_material_organizer(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, created, data = await _bootstrap_two_chapters(
        client,
        test_db,
    )
    chapter_id = chapters[0].id
    original_section = data["sections"][0]
    excluded = await test_db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == created[0]["utterance_id"],
        )
    )
    assert excluded is not None
    excluded.include_in_book = False
    test_db.add(excluded)
    await test_db.commit()
    fake = ReplayMaterialOrganizationAI()
    requested_models: list[str | None] = []

    def factory(model=None):
        requested_models.append(model)
        return fake

    monkeypatch.setattr(
        section_regeneration,
        "get_ai_service",
        factory,
    )

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/sections/regenerate",
        json={
            "expected_sections": [
                {
                    "id": original_section["id"],
                    "version": original_section["version"],
                }
            ],
            "ai_model": "deepseek-v4-flash",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "material_count": 2,
        "assigned_count": 2,
        "unassigned_count": 0,
        "section_count": 1,
        "ai_model": "fake-section-organizer",
    }
    listed = await client.get(f"/api/memoir/projects/{project_id}/sections")
    assert listed.status_code == 200
    chapter_sections = [
        section
        for section in listed.json()["sections"]
        if section["chapter_id"] == chapter_id
    ]
    assert len(chapter_sections) == 1
    assert chapter_sections[0]["id"] != original_section["id"]
    assert chapter_sections[0]["title"] == "山桃花里的重逢"
    assert [
        material["utterance_id"]
        for material in chapter_sections[0]["materials"]
    ] == [created[0]["utterance_id"], created[1]["utterance_id"]]
    assert all(
        material["assigned_by"] == "ai"
        for material in chapter_sections[0]["materials"]
    )
    assert [
        material["include_in_book"]
        for material in chapter_sections[0]["materials"]
    ] == [False, True]
    assert len(fake.prompts) == 2
    assert requested_models == ["deepseek-v4-flash"]
    assert "先从全章视角设计" in fake.prompts[0]
    assert "朋友发来山桃花的照片" in fake.prompts[0]
    assert "assign_existing|leave_unassigned" in fake.prompts[1]
    assert "山桃花里的重逢" in fake.prompts[1]
    untouched = [
        section
        for section in listed.json()["sections"]
        if section["chapter_id"] == chapters[1].id
    ]
    assert [section["title"] for section in untouched] == ["工作这些年"]
    assert "工作这些年" not in fake.prompts[0]


async def test_regenerate_chapter_sections_keeps_pending_material_for_user_choice(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, created, data = await _bootstrap_two_chapters(
        client,
        test_db,
    )
    chapter_id = chapters[0].id
    original_section = data["sections"][0]
    fake = PartiallyUnassignedReplayMaterialOrganizationAI()
    monkeypatch.setattr(
        section_regeneration,
        "get_ai_service",
        lambda _model=None: fake,
    )

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/sections/regenerate",
        json={
            "expected_sections": [
                {
                    "id": original_section["id"],
                    "version": original_section["version"],
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "material_count": 2,
        "assigned_count": 1,
        "unassigned_count": 1,
        "section_count": 1,
        "ai_model": "fake-section-organizer",
    }
    assert len(fake.prompts) == 2
    listed = await client.get(f"/api/memoir/projects/{project_id}/sections")
    assert listed.status_code == 200, listed.text
    chapter_sections = [
        section
        for section in listed.json()["sections"]
        if section["chapter_id"] == chapter_id
    ]
    normal_section = next(
        section
        for section in chapter_sections
        if not section["requires_user_assignment"]
    )
    pending_section = next(
        section
        for section in chapter_sections
        if section["requires_user_assignment"]
    )
    assert normal_section["title"] == "山桃花里的重逢"
    assert [
        item["utterance_id"] for item in normal_section["materials"]
    ] == [created[0]["utterance_id"]]
    assert pending_section["title"] == "待归类素材"
    assert pending_section["include_in_book"] is False
    assert [
        item["utterance_id"] for item in pending_section["materials"]
    ] == [created[1]["utterance_id"]]
    assert pending_section["materials"][0]["include_in_book"] is True
    assert {
        item["utterance_id"]
        for section in chapter_sections
        for item in section["materials"]
    } == {created[0]["utterance_id"], created[1]["utterance_id"]}

    workspace = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/"
        f"{chapter_id}/workspace"
    )
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["pending_assignment_material_count"] == 1
    assert workspace.json()["can_rewrite_draft"] is False
    assert (
        workspace.json()["completion_summary"]["next_action"]["kind"]
        == "review_sections"
    )
    blocked = await client.post(
        f"/api/memoir/projects/{project_id}/book-completion/"
        f"chapters/{chapter_id}/prepare",
        json={},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "请先为 1 条待归类素材选择小节"
    direct_generate = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/"
        "layered-draft/generate",
        json={},
    )
    assert direct_generate.status_code == 409
    assert (
        direct_generate.json()["detail"]
        == "请先为 1 条待归类素材选择小节"
    )
    direct_confirm = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/"
        "layered-draft/confirm",
    )
    assert direct_confirm.status_code == 409
    assert (
        direct_confirm.json()["detail"]
        == "请先为 1 条待归类素材选择小节"
    )

    moved = await client.post(
        f"/api/memoir/projects/{project_id}/section-materials/"
        f"{created[1]['utterance_id']}/move",
        json={"target_section_id": normal_section["id"]},
    )
    assert moved.status_code == 200, moved.text
    refreshed = await client.get(
        f"/api/memoir/projects/{project_id}/chapters/"
        f"{chapter_id}/workspace"
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["pending_assignment_material_count"] == 0
    assert (
        refreshed.json()["completion_summary"]["next_action"]["kind"]
        != "review_sections"
    )


async def test_regenerate_chapter_sections_preserves_structure_when_all_pending(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, created, data = await _bootstrap_two_chapters(
        client,
        test_db,
    )
    original_section = data["sections"][0]
    fake = ReplayMaterialOrganizationAI(leave_unassigned=True)
    monkeypatch.setattr(
        section_regeneration,
        "get_ai_service",
        lambda _model=None: fake,
    )

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/"
        f"{chapters[0].id}/sections/regenerate",
        json={
            "expected_sections": [
                {
                    "id": original_section["id"],
                    "version": original_section["version"],
                }
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "AI 未能为本章生成任何小节，请重试"
    await test_db.rollback()
    persisted = await test_db.get(MemoirSection, original_section["id"])
    assert persisted is not None
    memberships = list(
        (
            await test_db.scalars(
                select(MemoirSectionMaterial).where(
                    MemoirSectionMaterial.section_id
                    == original_section["id"]
                )
            )
        ).all()
    )
    assert {
        material.utterance_id for material in memberships
    } == {created[0]["utterance_id"], created[1]["utterance_id"]}


async def test_regenerate_chapter_sections_preserves_structure_when_ai_fails(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, created, data = await _bootstrap_two_chapters(
        client,
        test_db,
    )
    original_section = data["sections"][0]
    fake = FailingReplayMaterialOrganizationAI()
    monkeypatch.setattr(
        section_regeneration,
        "get_ai_service",
        lambda _model=None: fake,
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await client.post(
            f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/sections/regenerate",
            json={
                "expected_sections": [
                    {
                        "id": original_section["id"],
                        "version": original_section["version"],
                    }
                ],
            },
        )

    await test_db.rollback()
    persisted = await test_db.get(MemoirSection, original_section["id"])
    assert persisted is not None
    memberships = list(
        (
            await test_db.execute(
                select(MemoirSectionMaterial).where(
                    MemoirSectionMaterial.section_id == original_section["id"]
                )
            )
        ).scalars().all()
    )
    assert {
        material.utterance_id
        for material in memberships
    } == {created[0]["utterance_id"], created[1]["utterance_id"]}


async def test_regenerate_chapter_sections_rejects_stale_structure_before_ai(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _, data = await _bootstrap_two_chapters(
        client,
        test_db,
    )
    original_section = data["sections"][0]
    fake = ReplayMaterialOrganizationAI()
    monkeypatch.setattr(
        section_regeneration,
        "get_ai_service",
        lambda _model=None: fake,
    )

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/sections/regenerate",
        json={
            "expected_sections": [
                {
                    "id": original_section["id"],
                    "version": original_section["version"] + 1,
                }
            ],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "本章小节已经变化，请刷新后重试"
    assert fake.prompts == []


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
