import json
import re
from datetime import datetime

import pytest
from sqlalchemy import select

from app.config import DEFAULT_AI_TEXT_MODEL
from app.services.ai_base import AITextServiceBase
from app.domain.memoir.types import MemoirChatMessageRole
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookChapterDraftVersion,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
    MemoirTranscriptVersion,
)
from app.services.memoir import (
    chapter_generation,
    section_regeneration,
    sections as memoir_sections,
    story_tree_growth,
)
from app.services.memoir.narrative import book_completion
from app.services.memoir.editing_job_handlers import (
    register_memoir_editing_job_handlers,
)
from app.services.memoir.editing_jobs import clear_editing_job_handlers
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_chapter_draft,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
    run_test_editing_job,
)


class FakeStoryGrowthAI(AITextServiceBase):
    provider_name = "fake"
    model_name = "fake-story-growth"

    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.calls = 0
        self.prompts: list[str] = []

    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        self.calls += 1
        self.prompts.append(user_prompt)
        return json.dumps(self.payload, ensure_ascii=False)


def test_section_structure_prompt_does_not_require_specific_titles():
    prompt = section_regeneration._build_section_structure_prompt(
        chapter_title="山桃花开的心动",
        materials=[],
    )

    assert "标题要具体" not in prompt
    assert "避免用空泛" not in prompt


class ReplayMaterialOrganizationAI(AITextServiceBase):
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
    confirmed_draft = await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapters[0].id,
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
    await test_db.refresh(confirmed_draft)
    assert confirmed_draft.status == "draft"
    assert confirmed_draft.metadata_json["status_reason"] == (
        "section_structure_changed"
    )


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


async def test_story_tree_growth_groups_session_materials_into_new_section(
    client,
    test_db,
    monkeypatch,
):
    project_id, chapters, _, _ = await _bootstrap_two_chapters(client, test_db)
    first = await create_test_voice_transcript(
        client,
        test_db,
        "后来我们又一起去爬了香山。",
        project_id=project_id,
    )
    session = await test_db.get(MemoirVoiceSession, first["session"]["id"])
    assert session is not None
    session.status = "stopped"
    test_db.add(session)
    await test_db.commit()
    fake = FakeStoryGrowthAI(
        {
            "groups": [
                {
                    "source_indices": [1],
                    "action": "create_section",
                    "section_id": None,
                    "chapter_id": "C1",
                    "new_chapter_title": None,
                    "new_section_title": "后来一起爬香山",
                    "confidence": 0.91,
                    "reason": "同属爬山经历，但形成了新的具体事件",
                }
            ]
        }
    )
    monkeypatch.setattr(story_tree_growth, "get_ai_service", lambda _model=None: fake)
    job = MemoirEditingJob(
        project_id=project_id,
        job_type="organize_material",
        status="running",
        run_after=datetime.now(),
        payload_json={"voice_session_id": session.id},
    )
    test_db.add(job)
    await test_db.flush()

    result = await story_tree_growth.run_story_tree_growth_job(test_db, job)

    created_section = await test_db.scalar(
        select(MemoirSection).where(
            MemoirSection.project_id == project_id,
            MemoirSection.title == "后来一起爬香山",
        )
    )
    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.event_type == "story_structure_updated"
        )
    )
    assert created_section is not None
    assert created_section.chapter_id == chapters[0].id
    assert result.result_json["organized_utterance_ids"] == [first["utterance_id"]]
    assert result.result_json["new_section_ids"] == [created_section.id]
    assert event is not None
    assert event.metadata_json["new_section_ids"] == [created_section.id]
    assert fake.calls == 1
    assert "create_section" in fake.prompts[0]
    assert chapters[0].id not in fake.prompts[0]

    acknowledged = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/automation-events/{event.id}/acknowledge"
    )
    assert acknowledged.status_code == 200
    later = await create_test_voice_transcript(client, test_db, "后来同一年我们又去爬了黄山。", project_id=project_id)
    utterance = await test_db.get(MemoirVoiceUtterance, later["utterance_id"])
    message = await test_db.get(MemoirChatMessage, later["message_id"])
    transcript = await test_db.get(MemoirTranscriptVersion, later["transcript_version_id"])
    utterance.voice_session_id = message.voice_session_id = transcript.voice_session_id = session.id
    message.turn_index = 2
    session.status = "listening"
    job.result_json = result.result_json
    fake.payload["groups"][0]["new_section_title"] = "后来一起爬黄山"
    await test_db.commit()
    result = await story_tree_growth.run_story_tree_growth_job(test_db, job)
    events = (await test_db.scalars(select(MemoirAutomationEvent).where(
        MemoirAutomationEvent.project_id == project_id,
        MemoirAutomationEvent.event_type == "story_structure_updated",
    ))).all()
    assert len(events) == 2
    fresh = next(item for item in events if item.id != event.id)
    assert not fresh.metadata_json.get("acknowledged_at")
    assert fresh.metadata_json["voice_session_id"] == session.id
    assert fresh.metadata_json["new_section_ids"] == result.result_json["new_section_ids"]
    assert fresh.utterance_ids_json == [later["utterance_id"]]


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
        "ai_model": DEFAULT_AI_TEXT_MODEL,
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
    assert state["requested_ai_model"] == DEFAULT_AI_TEXT_MODEL

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

    wrong_chapter = await client.delete(
        f"/api/memoir/projects/{project_id}/chapters/{chapters[1].id}/section-regeneration-jobs/{job_id}"
    )
    assert wrong_chapter.status_code == 404

    cancelled = await client.delete(f"{url}/{job_id}")

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["stage"] == "cancelled"
    assert cancelled.json()["can_cancel"] is False


async def test_running_regrouping_cannot_be_reported_as_a_new_consolidation(
    client, test_db,
):
    project_id, chapters, _created, data = await _bootstrap_two_chapters(client, test_db)
    chapter_id = chapters[0].id
    url = f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/section-regeneration-jobs"
    payload = {"expected_sections": [{
        "id": data["sections"][0]["id"], "version": data["sections"][0]["version"],
    }]}
    other = MemoirSection(project_id=project_id, chapter_id=chapter_id,
                          title="另一段回忆", sort_order=1)
    test_db.add(other)
    await test_db.commit()
    payload["expected_sections"].append({"id": other.id, "version": other.version})
    queued = await client.post(url, json=payload)
    assert queued.status_code == 202, queued.text
    job = await test_db.get(MemoirEditingJob, queued.json()["job_id"])
    assert job is not None
    job.status = "running"
    await test_db.commit()

    response = await client.post(url, json={**payload, "section_count_goal": "reduce"})
    assert response.status_code == 409, response.text
    assert "已有另一项小节整理任务" in response.json()["detail"]
    await test_db.refresh(job)
    assert job.payload_json["request"]["max_sections"] is None
    assert job.payload_json["request"].get("section_count_goal") is None


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
    confirmed_draft = await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
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
            "ai_model": DEFAULT_AI_TEXT_MODEL,
        },
    )

    assert response.status_code == 200, response.text
    await test_db.refresh(confirmed_draft)
    assert confirmed_draft.status == "draft"
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
    assert requested_models == [DEFAULT_AI_TEXT_MODEL]
    assert "先从全章视角设计" in fake.prompts[0]
    assert "朋友发来山桃花的照片" in fake.prompts[0]
    assert chapter_id not in fake.prompts[0]
    assert created[0]["utterance_id"] not in fake.prompts[0]
    assert '"source_index": 1' in fake.prompts[0]
    assert "assign_existing|leave_unassigned" in fake.prompts[1]
    assert "山桃花里的重逢" in fake.prompts[1]
    assert chapter_id not in fake.prompts[1]
    assert created[0]["utterance_id"] not in fake.prompts[1]
    assert '"section_id": "S1"' in fake.prompts[1]
    assert '"utterance_id": "M1"' in fake.prompts[1]
    untouched = [
        section
        for section in listed.json()["sections"]
        if section["chapter_id"] == chapters[1].id
    ]
    assert [section["title"] for section in untouched] == ["工作这些年"]
    assert "工作这些年" not in fake.prompts[0]


async def test_section_consolidation_keeps_original_structure_when_ai_adds_sections(
    client, test_db, monkeypatch,
):
    project_id, chapters, _created, data = await _bootstrap_two_chapters(client, test_db)
    chapter_id = chapters[0].id
    original = data["sections"][0]
    other = MemoirSection(
        project_id=project_id, chapter_id=chapter_id,
        title="另一段回忆", sort_order=1,
    )
    test_db.add(other)
    await test_db.commit()
    fake = FakeStoryGrowthAI({"sections": [
        {"title": "第一组", "section_type": "event"},
        {"title": "第二组", "section_type": "event"},
    ]})
    monkeypatch.setattr(section_regeneration, "get_ai_service", lambda _model: fake)

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/sections/regenerate",
        json={"expected_sections": [
            {"id": original["id"], "version": original["version"]},
            {"id": other.id, "version": other.version},
        ], "max_sections": 1},
    )
    assert response.status_code == 409, response.text
    assert "原小节保持不变" in response.json()["detail"]
    assert await test_db.get(MemoirSection, original["id"]) is not None
    assert await test_db.get(MemoirSection, other.id) is not None
    assert fake.calls == 1


async def test_section_consolidation_lets_ai_choose_a_smaller_group_count(
    client, test_db, monkeypatch,
):
    project_id, chapters, created, data = await _bootstrap_two_chapters(client, test_db)
    chapter_id = chapters[0].id
    original = data["sections"][0]
    other = MemoirSection(
        project_id=project_id, chapter_id=chapter_id,
        title="另一段回忆", sort_order=1,
    )
    untouched = MemoirSection(
        project_id=project_id, chapter_id=chapter_id,
        title="独立的旧小节", sort_order=2,
    )
    test_db.add_all([other, untouched])
    await test_db.flush()
    moved = await test_db.scalar(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.utterance_id == created[1]["utterance_id"],
    ))
    assert moved is not None
    moved.section_id = other.id
    moved.include_in_book = False
    await test_db.commit()
    fake = FakeStoryGrowthAI({"groups": [{
        "target_section_id": "S2",
        "source_section_ids": ["S1"],
        "title": "山桃花里的相识",
    }]})
    monkeypatch.setattr(section_regeneration, "get_ai_service", lambda _model: fake)

    url = f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/section-regeneration-jobs"
    queued = await client.post(
        url,
        json={"expected_sections": [
            {"id": original["id"], "version": original["version"]},
            {"id": other.id, "version": other.version},
            {"id": untouched.id, "version": untouched.version},
        ], "section_count_goal": "reduce"},
    )
    assert queued.status_code == 202, queued.text
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, queued.json()["job_id"]) is True
    finally:
        clear_editing_job_handlers()
    response = await client.get(f"{url}/current")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "succeeded"
    assert response.json()["section_count"] == 2
    sections = (await client.get(f"/api/memoir/projects/{project_id}/sections")).json()["sections"]
    current = [section for section in sections if section["chapter_id"] == chapter_id]
    assert [section["id"] for section in current] == [original["id"], untouched.id]
    assert current[0]["title"] == "山桃花里的相识"
    assert {material["utterance_id"] for material in current[0]["materials"]} == {
        created[0]["utterance_id"], created[1]["utterance_id"],
    }
    moved_after = next(material for material in current[0]["materials"]
                       if material["utterance_id"] == created[1]["utterance_id"])
    assert moved_after["id"] == moved.id
    assert moved_after["include_in_book"] is False
    assert (moved_after["assigned_by"], moved_after["assignment_reason"]) == (
        "ai", "ai_section_merge",
    )
    assert len(fake.prompts) == 1
    assert original["id"] not in fake.prompts[0]


async def test_section_consolidation_preserves_original_when_no_merge_group(
    client, test_db, monkeypatch,
):
    project_id, chapters, created, data = await _bootstrap_two_chapters(client, test_db)
    chapter_id = chapters[0].id
    original = data["sections"][0]
    other = MemoirSection(
        project_id=project_id, chapter_id=chapter_id,
        title="另一段回忆", sort_order=1,
    )
    test_db.add(other)
    await test_db.flush()
    moved = await test_db.scalar(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.utterance_id == created[1]["utterance_id"],
    ))
    assert moved is not None
    moved.section_id = other.id
    await test_db.commit()
    fake = FakeStoryGrowthAI({"groups": []})
    monkeypatch.setattr(section_regeneration, "get_ai_service", lambda _model: fake)

    response = await client.post(
        f"/api/memoir/projects/{project_id}/chapters/{chapter_id}/sections/regenerate",
        json={"expected_sections": [
            {"id": original["id"], "version": original["version"]},
            {"id": other.id, "version": other.version},
        ], "section_count_goal": "reduce"},
    )
    assert response.status_code == 409, response.text
    assert "没有找到适合合并的现有小节" in response.json()["detail"]
    assert await test_db.get(MemoirSection, original["id"]) is not None
    assert await test_db.get(MemoirSection, other.id) is not None
    assert len(fake.prompts) == 1


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
        != "review_sections"
    )
    await book_completion.ensure_chapter_sections(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
        user_id="test-user",
    )
    generation_context = await chapter_generation.load_chapter_generation_context(
        test_db,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    assert [item.material_id for item in generation_context.materials] == [
        created[0]["utterance_id"]
    ]

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


@pytest.mark.db_independent
@pytest.mark.parametrize("change", ["placement", "text", "section", "chapter", "chapter_existing", "delete_section"])
async def test_story_growth_discards_decisions_changed_during_model_wait(
    client, test_db, monkeypatch, change,
):
    from sqlalchemy import delete, update
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.memoir import MemoirBookPlanChapter, MemoirVoiceUtterance
    from app.schemas.memoir_sections import MemoirSectionMaterialPlacementRequest
    from app.services.memoir.sections import place_material

    project_id, chapters, _, data = await _bootstrap_two_chapters(client, test_db)
    source = await create_test_voice_transcript(client, test_db, "后来我们再去爬香山。", project_id=project_id)
    session = await test_db.get(MemoirVoiceSession, source["session"]["id"])
    session.status = "stopped"
    user_id = (await client.get("/api/auth/me")).json()["id"]
    target_id = data["sections"][0]["id"]
    job = MemoirEditingJob(project_id=project_id, job_type="organize_material", status="running",
                           run_after=datetime.now(), payload_json={"voice_session_id": session.id})
    test_db.add(job)
    await test_db.commit()
    manual_section = None

    async def decide(**kwargs):
        nonlocal manual_section
        async with AsyncSession(test_db.bind, expire_on_commit=False) as other:
            if change == "placement":
                placed = await place_material(other, user_id=user_id, project_id=project_id,
                    utterance_id=source["utterance_id"],
                    req=MemoirSectionMaterialPlacementRequest(
                        target_chapter_id=chapters[0].id, new_section_title="用户指定的小节"))
                manual_section = placed.section_id
            elif change == "text":
                await other.execute(update(MemoirVoiceUtterance).where(
                    MemoirVoiceUtterance.id == source["utterance_id"]
                ).values(transcript_edited="更正：那次其实是出差。"))
            elif change == "section":
                await other.execute(update(MemoirSection).where(MemoirSection.id == target_id)
                                    .values(title="用户改过的小节", version=MemoirSection.version + 1))
            elif change in {"chapter", "chapter_existing"}:
                await other.execute(update(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapters[0].id)
                                    .values(title="用户改过的章节"))
            else:
                await other.execute(delete(MemoirSection).where(MemoirSection.id == target_id))
            await other.commit()
        return [story_tree_growth.GrowthGroup(
            utterance_ids=[source["utterance_id"]],
            action="create_section" if change == "chapter" else "assign_existing",
            chapter_id=chapters[0].id if change == "chapter" else None,
            new_section_title="过时的新小节" if change == "chapter" else None,
            section_id=None if change == "chapter" else target_id,
        )]

    monkeypatch.setattr(story_tree_growth, "decide_story_growth", decide)
    result = await story_tree_growth.run_story_tree_growth_job(test_db, job)
    material = await test_db.scalar(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.utterance_id == source["utterance_id"]))
    assert result.result_json["organized_utterance_ids"] == []
    if change == "placement":
        assert material.section_id == manual_section
        assert material.assigned_by == "user"
    else:
        assert material is None
    assert await test_db.scalar(select(MemoirSection.id).where(MemoirSection.title == "过时的新小节")) is None


async def _stopped_growth_job(client, test_db, project_id, text):
    source = await create_test_voice_transcript(client, test_db, text, project_id=project_id)
    session = await test_db.get(MemoirVoiceSession, source["session"]["id"])
    session.status = "stopped"
    job = MemoirEditingJob(project_id=project_id, job_type="organize_material", status="running",
                           run_after=datetime.now(), payload_json={"voice_session_id": session.id},
                           dedupe_key=f"organize_session:{session.id}")
    test_db.add(job)
    await test_db.commit()
    return source, job


async def test_story_growth_repairs_section_order_gap_before_creating_section(
    client, test_db, monkeypatch,
):
    project_id, chapters, _, _ = await _bootstrap_two_chapters(client, test_db)
    test_db.add(MemoirSection(
        project_id=project_id,
        chapter_id=chapters[0].id,
        title="保留的旧小节",
        sort_order=2,
    ))
    await test_db.commit()
    source, job = await _stopped_growth_job(
        client, test_db, project_id, "照着教程做茄子饼失败的经历。",
    )

    async def decide(**_kwargs):
        return [story_tree_growth.GrowthGroup(
            utterance_ids=[source["utterance_id"]],
            action="create_section",
            chapter_id=chapters[0].id,
            new_section_title="茄子饼失败的经历",
        )]

    monkeypatch.setattr(story_tree_growth, "decide_story_growth", decide)
    result = await story_tree_growth.run_story_tree_growth_job(test_db, job)

    assert result.status.value == "succeeded"
    chapter_sections = list((await test_db.scalars(
        select(MemoirSection)
        .where(MemoirSection.chapter_id == chapters[0].id)
        .order_by(MemoirSection.sort_order)
    )).all())
    assert [section.sort_order for section in chapter_sections] == [0, 1, 2]
    created = next(
        section for section in chapter_sections
        if section.title == "茄子饼失败的经历"
    )
    assert await test_db.scalar(select(MemoirSectionMaterial.id).where(
        MemoirSectionMaterial.section_id == created.id,
        MemoirSectionMaterial.utterance_id == source["utterance_id"],
    ))


async def test_delete_section_compacts_remaining_order(client, test_db):
    project_id, chapters, _, _ = await _bootstrap_two_chapters(client, test_db)
    for title in ("中间小节", "末尾小节"):
        response = await client.post(
            f"/api/memoir/projects/{project_id}/chapters/{chapters[0].id}/sections",
            json={"title": title},
        )
        assert response.status_code == 200, response.text

    middle = await test_db.scalar(select(MemoirSection).where(
        MemoirSection.project_id == project_id,
        MemoirSection.title == "中间小节",
    ))
    user_id = (await client.get("/api/auth/me")).json()["id"]
    await memoir_sections.delete_section(
        test_db,
        user_id=user_id,
        project_id=project_id,
        section_id=middle.id,
    )

    remaining_orders = list((await test_db.scalars(
        select(MemoirSection.sort_order)
        .where(MemoirSection.chapter_id == chapters[0].id)
        .order_by(MemoirSection.sort_order)
    )).all())
    assert remaining_orders == [0, 1]


@pytest.mark.db_independent
async def test_concurrent_section_creation_serializes_chapter_order(
    client, test_db,
):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.schemas.memoir_sections import MemoirSectionMaterialPlacementRequest
    from app.services.memoir.sections import place_material

    project_id, chapters, _, _ = await _bootstrap_two_chapters(client, test_db)
    sources = [
        await create_test_voice_transcript(
            client, test_db, text, project_id=project_id,
        )
        for text in ("第一次新讲述。", "第二次新讲述。")
    ]
    user_id = (await client.get("/api/auth/me")).json()["id"]
    session_factory = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def create(source, title):
        async with session_factory() as db:
            return await place_material(
                db,
                user_id=user_id,
                project_id=project_id,
                utterance_id=source["utterance_id"],
                req=MemoirSectionMaterialPlacementRequest(
                    target_chapter_id=chapters[0].id,
                    new_section_title=title,
                ),
            )

    await asyncio.gather(
        create(sources[0], "并发新小节一"),
        create(sources[1], "并发新小节二"),
    )

    async with session_factory() as db:
        created_sections = list((await db.scalars(
            select(MemoirSection)
            .where(MemoirSection.chapter_id == chapters[0].id)
            .order_by(MemoirSection.sort_order)
        )).all())
    assert [section.sort_order for section in created_sections] == [0, 1, 2]
    assert {section.title for section in created_sections} >= {
        "并发新小节一", "并发新小节二",
    }


async def test_initial_story_resumes_only_unfinished_chapters(client, test_db, monkeypatch):
    from app.services.memoir import book_plan_write_workflow as plans
    from app.services.memoir.book_plan_ai import GeneratedBookPlan, chapters_from_payload

    project_id = await ensure_mobile_memoir_project(client)
    await _stopped_growth_job(client, test_db, project_id, "小时候我住在成都。")
    _, job = await _stopped_growth_job(client, test_db, project_id, "后来我去北京工作。")
    plan_calls = 0
    section_calls = []

    async def plan(**kwargs):
        nonlocal plan_calls
        plan_calls += 1
        title, chapters = chapters_from_payload({"title": "我的故事", "chapters": [
            {"title": "小时候", "source_indices": [1]}, {"title": "工作后", "source_indices": [2]},
        ]}, sources=kwargs["sources"])
        return GeneratedBookPlan(title=title, chapters=chapters, model="test", prompt="test")

    async def sections(_db, **kwargs):
        section_calls.append(kwargs["chapter_id"])
        if len(section_calls) == 2:
            raise RuntimeError("second chapter provider failure")

    monkeypatch.setattr(plans, "_generate_book_plan_from_sources", plan)
    monkeypatch.setattr(story_tree_growth, "regenerate_chapter_sections", sections)
    with pytest.raises(RuntimeError, match="second chapter"):
        await story_tree_growth.run_story_tree_growth_job(test_db, job)
    await test_db.rollback()
    await test_db.refresh(job)
    progress = story_tree_growth._progress(job)
    assert len(progress.initial_sections) == 2
    assert progress.completed_initial_chapters == [section_calls[0]]
    job.status = "failed"
    await test_db.commit()
    retried = await story_tree_growth.schedule_story_tree_growth(
        test_db, project_id=project_id, voice_session_id=job.payload_json["voice_session_id"], source="retry",
    )
    assert retried.id == job.id
    assert story_tree_growth._progress(retried).completed_initial_chapters == [section_calls[0]]
    job.status = "running"
    await test_db.commit()
    result = await story_tree_growth.run_story_tree_growth_job(test_db, job)
    assert result.status.value == "succeeded"
    assert plan_calls == 1
    assert section_calls == [section_calls[0], section_calls[1], section_calls[1]]
    assert len(result.result_json["growth"]["completed_initial_chapters"]) == 2


async def test_initial_story_deduplicates_chapter_sources_before_persisting(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    source, job = await _stopped_growth_job(
        client, test_db, project_id, "河边晨步、雨衣和一碗热汤面。",
    )
    plan_ai = FakeStoryGrowthAI({"title": "我的故事", "chapters": [
        {"title": "晨步", "source_indices": [1]},
        {"title": "雨衣", "source_indices": [1]},
        {"title": "热汤面", "source_indices": [1]},
    ]})
    sections_ai = ReplayMaterialOrganizationAI()
    monkeypatch.setattr("app.services.memoir.book_plan_ai.get_ai_service", lambda _model=None: plan_ai)
    monkeypatch.setattr(section_regeneration, "get_ai_service", lambda _model=None: sections_ai)

    result = await story_tree_growth.run_story_tree_growth_job(test_db, job)

    assert result.status.value == "succeeded"
    chapters = list((await test_db.scalars(select(MemoirBookPlanChapter).where(
        MemoirBookPlanChapter.project_id == project_id,
    ))).all())
    sections = list((await test_db.scalars(select(MemoirSection).where(
        MemoirSection.project_id == project_id,
    ))).all())
    materials = list((await test_db.scalars(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.project_id == project_id,
    ))).all())
    assert len(chapters) == len(sections) == len(materials) == 1
    assert materials[0].utterance_id == source["utterance_id"]
    assert materials[0].section_id == sections[0].id
    assert result.result_json["growth"]["completed_initial_chapters"] == [chapters[0].id]
    assert result.result_json["growth"]["needs_material_chapters"] == []


async def test_initial_story_waits_when_narration_only_answers_the_interviewer(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client)
    _, job = await _stopped_growth_job(client, test_db, project_id, "你叫我余大姐吧，年年有余的余。")
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda _model=None: FakeStoryGrowthAI({"title": "", "chapters": []}),
    )

    result = await story_tree_growth.run_story_tree_growth_job(test_db, job)

    assert result.status.value == "skipped"
    job.status = result.status.value
    await test_db.commit()
    again = await story_tree_growth.schedule_story_tree_growth(
        test_db, project_id=project_id, voice_session_id=job.payload_json["voice_session_id"], source="narration",
    )
    assert again.id == job.id and again.status == "pending"


async def test_retry_initial_story_with_empty_chapters_preserves_existing_user_draft(client, test_db):
    from app.schemas.memoir_sections import MemoirSectionVersionReference

    project_id = await ensure_mobile_memoir_project(client)
    source, job = await _stopped_growth_job(
        client, test_db, project_id, "河边晨步、雨衣和一碗热汤面。",
    )
    _, chapters = await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["晨步", "用户修改过的章节", "热汤面"],
        source_refs_by_chapter=[[], [memoir_source_ref(source)], []],
        draft_contents=["", "用户确认后的正文必须保留。", ""],
    )
    sections = list((await test_db.scalars(select(MemoirSection).where(
        MemoirSection.project_id == project_id,
    ))).all())
    section_ids = {section.id for section in sections}
    saved_version = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.chapter_id == chapters[1].id,
    ))
    saved_version_id = saved_version.id
    job.result_json = {"growth": story_tree_growth.StoryGrowthProgress(
        initial_sections={
            chapter.id: [MemoirSectionVersionReference(id=section.id, version=section.version)
                         for section in sections if section.chapter_id == chapter.id]
            for chapter in chapters
        },
        placed_ids=[source["utterance_id"]],
    ).model_dump(mode="json")}
    job.status = "failed"
    job.error_message = "本章没有可用于生成小节的讲述"
    await test_db.commit()

    retry = await client.post(f"/api/mobile/memoir/projects/{project_id}/draft-progress/retry")
    assert retry.status_code == 202, retry.text
    await test_db.refresh(job)
    assert job.status == "pending"
    clear_editing_job_handlers()
    register_memoir_editing_job_handlers()
    try:
        assert await run_test_editing_job(test_db, job.id) is True
    finally:
        clear_editing_job_handlers()
    await test_db.refresh(job)

    assert job.status == "succeeded"
    assert not job.error_message
    progress = story_tree_growth._progress(job)
    assert progress.completed_initial_chapters == [chapter.id for chapter in chapters]
    assert progress.needs_material_chapters == [chapters[0].id, chapters[2].id]
    assert set((await test_db.scalars(select(MemoirSection.id).where(
        MemoirSection.project_id == project_id,
    ))).all()) == section_ids
    versions = list((await test_db.scalars(select(MemoirBookChapterDraftVersion).where(
        MemoirBookChapterDraftVersion.project_id == project_id,
    ))).all())
    assert [(version.id, version.content) for version in versions] == [
        (saved_version_id, "用户确认后的正文必须保留。"),
    ]
    materials = list((await test_db.scalars(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.project_id == project_id,
    ))).all())
    assert len(materials) == 1
    assert materials[0].utterance_id == source["utterance_id"]
    assert next(section.chapter_id for section in sections if section.id == materials[0].section_id) == chapters[1].id


@pytest.mark.parametrize("reason", ["", "需要确认讲的是哪一段经历"])
async def test_set_aside_story_is_retried_before_waiting_for_user(
    client, test_db, monkeypatch, reason,
):
    project_id, _, _, _ = await _bootstrap_two_chapters(client, test_db)
    seen = []
    async def set_aside(**kwargs):
        ids = [item.id for item in kwargs["utterances"]]
        seen.append(ids)
        return [story_tree_growth.GrowthGroup(utterance_ids=ids, action="leave_unassigned", reason=reason)]
    monkeypatch.setattr(story_tree_growth, "decide_story_growth", set_aside)
    sources, results = [], []
    for text in ["早年一次旅行。", "最近去香山的经历。", "后来又去了颐和园。"]:
        source, job = await _stopped_growth_job(client, test_db, project_id, text)
        sources.append(source["utterance_id"])
        result = await story_tree_growth.run_story_tree_growth_job(test_db, job)
        results.append(result.result_json["growth"]["deferred"])
        job.result_json = result.result_json
        job.status = "succeeded"
        await test_db.commit()
    old, first, second = sources
    if reason:
        assert seen == [[old], [old, first], [old, first, second]]
        assert results[1][old]["status"] == "pending"
        assert results[1][old]["attempts"] == 1
    else:
        assert seen == [[old], [old, first], [old, first, second]]


async def test_new_chapters_with_the_same_title_are_merged(client, test_db, monkeypatch):
    from sqlalchemy import func

    project_id, _, _, _ = await _bootstrap_two_chapters(client, test_db)
    first, job = await _stopped_growth_job(client, test_db, project_id, "第一次去香山。")
    second, _ = await _stopped_growth_job(client, test_db, project_id, "后来在颐和园划船。")
    async def decide(**kwargs):
        return [
            story_tree_growth.GrowthGroup(utterance_ids=[first["utterance_id"]], action="create_chapter",
                                          new_chapter_title="北京游玩", new_section_title="香山"),
            story_tree_growth.GrowthGroup(utterance_ids=[second["utterance_id"]], action="create_chapter",
                                          new_chapter_title="北京游玩", new_section_title="颐和园"),
        ]
    monkeypatch.setattr(story_tree_growth, "decide_story_growth", decide)
    result = await story_tree_growth.run_story_tree_growth_job(test_db, job)
    assert len(result.result_json["new_chapter_ids"]) == 1
    assert await test_db.scalar(select(func.count(MemoirBookPlanChapter.id)).where(
        MemoirBookPlanChapter.title == "北京游玩",
    )) == 1
    sections = (await test_db.scalars(select(MemoirSection).where(
        MemoirSection.chapter_id == result.result_json["new_chapter_ids"][0],
    ))).all()
    assert {section.title for section in sections} == {"香山", "颐和园"}


async def test_story_assignment_resumes_saved_group_without_repeating_model_or_structure(
    client, test_db, monkeypatch,
):
    from sqlalchemy import func

    project_id, chapters, _, _ = await _bootstrap_two_chapters(client, test_db)
    first, job = await _stopped_growth_job(client, test_db, project_id, "第一次去香山。")
    second, _ = await _stopped_growth_job(client, test_db, project_id, "后来又去了香山。")
    model_calls = 0
    async def decide(**kwargs):
        nonlocal model_calls
        model_calls += 1
        return [story_tree_growth.GrowthGroup(utterance_ids=[first["utterance_id"], second["utterance_id"]],
            action="create_section", chapter_id=chapters[0].id, new_section_title="香山往事")]
    monkeypatch.setattr(story_tree_growth, "decide_story_growth", decide)
    save = story_tree_growth._save_progress
    async def interrupted(db, current_job, progress):
        await save(db, current_job, progress)
        if progress.placed_ids == [first["utterance_id"]]:
            raise RuntimeError("worker stopped after persisted placement")
    monkeypatch.setattr(story_tree_growth, "_save_progress", interrupted)
    with pytest.raises(RuntimeError, match="worker stopped"):
        await story_tree_growth.run_story_tree_growth_job(test_db, job)
    await test_db.rollback()
    await test_db.refresh(job)
    monkeypatch.setattr(story_tree_growth, "_save_progress", save)
    result = await story_tree_growth.run_story_tree_growth_job(test_db, job)
    assert model_calls == 1
    assert set(result.result_json["organized_utterance_ids"]) == {first["utterance_id"], second["utterance_id"]}
    assert await test_db.scalar(select(func.count(MemoirSection.id)).where(MemoirSection.title == "香山往事")) == 1


@pytest.mark.parametrize("status_code", [409, 422])
async def test_initial_story_does_not_treat_model_rejection_as_completed_structure(client, test_db, monkeypatch, status_code):
    from fastapi import HTTPException
    from app.schemas.memoir_sections import MemoirSectionVersionReference

    project_id, chapters, _, data = await _bootstrap_two_chapters(client, test_db)
    _, job = await _stopped_growth_job(client, test_db, project_id, "后来去香山。")
    section = data["sections"][0]
    job.result_json = {"growth": story_tree_growth.StoryGrowthProgress(initial_sections={
        chapters[0].id: [MemoirSectionVersionReference(id=section["id"], version=section["version"])],
    }).model_dump(mode="json")}
    await test_db.commit()
    async def rejected(*args, **kwargs):
        raise HTTPException(status_code=status_code, detail="AI 未能为本章生成任何小节，请重试")
    monkeypatch.setattr(story_tree_growth, "regenerate_chapter_sections", rejected)
    with pytest.raises(HTTPException):
        await story_tree_growth.run_story_tree_growth_job(test_db, job)
    assert story_tree_growth._progress(job).completed_initial_chapters == []


async def test_active_session_only_schedules_sealed_narration(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    created = await create_test_voice_transcript(client, test_db, "还没说完的素材。", project_id=project_id)
    message = await test_db.get(MemoirChatMessage, created["message_id"])
    session = await test_db.get(MemoirVoiceSession, created["session"]["id"])
    session.status = "listening"
    message.content_status = "partial"
    await test_db.commit()
    request = dict(project_id=project_id, voice_session_id=created["session"]["id"], source="transcript_ready")
    assert await story_tree_growth.schedule_story_tree_growth(test_db, **request) is None
    message.content_status = "final"
    await test_db.commit()
    job = await story_tree_growth.schedule_story_tree_growth(test_db, **request)
    assert job is not None
    session = await test_db.get(MemoirVoiceSession, created["session"]["id"])
    assert session.status == "listening"
    job.status = "failed"
    job.error_message = "模型响应超时，结果未知"
    await test_db.commit()
    same = await story_tree_growth.schedule_story_tree_growth(test_db, **request)
    assert same.status == "failed"
    retry = await story_tree_growth.schedule_story_tree_growth(test_db, **{**request, "source": "retry"})
    assert retry.status == "pending"


async def test_waiting_for_correction_preserves_story_checkpoint(client, test_db):
    from app.domain.memoir.types import MemoirEditingJobType
    from app.services.memoir.editing_jobs import enqueue_editing_job

    project_id = await ensure_mobile_memoir_project(client)
    _, job = await _stopped_growth_job(client, test_db, project_id, "童年在成都生活。")
    progress = {"completed_initial_chapters": ["already-written"]}
    job.result_json = {"growth": progress}
    await enqueue_editing_job(test_db, project_id=project_id, job_type=MemoirEditingJobType.CORRECT_TRANSCRIPT)
    await test_db.commit()
    waiting = await story_tree_growth.run_story_tree_growth_job(test_db, job)
    assert waiting.status.value == "pending"
    assert waiting.result_json["growth"] == progress
