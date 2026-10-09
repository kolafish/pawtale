import json

import pytest
import pytest_asyncio
from pydantic import ValidationError
from fastapi import HTTPException
from sqlalchemy import select

from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import (
    MemoirBookChapterDraft, MemoirBookChapterDraftVersion, MemoirBookOutput, MemoirBookPlan,
    MemoirBookPlanChapter, MemoirEditingJob, MemoirSection, MemoirSectionMaterial,
    MemoirVoiceSession, MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.schemas.memoir_voice_edit import VoiceEditApplyRequest, VoiceEditOperation, VoiceEditProposal
from app.services.ai_base import AITextServiceBase
from app.services.ai_timeouts import AIProviderTimeoutError
from app.services.memoir.voice_edit import _story
from app.services.memoir.semantic_state import semantic_json_signature


class FakeAI(AITextServiceBase):
    provider_name = "fixture"
    model_name = "deepseek-v4-flash"

    def __init__(self, proposal):
        self.proposal = proposal
        self.inputs = []
        self.system = ""

    async def _call_llm(self, system, user, **kwargs):
        self.system = system
        self.inputs.append(json.loads(user))
        return json.dumps(self.proposal, ensure_ascii=False)


@pytest_asyncio.fixture
async def story(client, test_db):
    uid = (await client.get("/api/auth/me")).json()["id"]
    project = Project(title="我的回忆录", type="memoir", user_id=uid)
    test_db.add(project)
    await test_db.flush()
    plan = MemoirBookPlan(project_id=project.id, status="confirmed")
    session = MemoirVoiceSession(project_id=project.id)
    test_db.add_all([plan, session])
    await test_db.flush()
    chapters, sections, materials = [], [], []
    for i, title in enumerate(["测试录音", "童年回忆", "早晨日常"]):
        chapter = MemoirBookPlanChapter(plan_id=plan.id, project_id=project.id, title=title, sort_order=i)
        test_db.add(chapter)
        await test_db.flush()
        section = MemoirSection(chapter_id=chapter.id, project_id=project.id, title=title)
        material = MemoirVoiceUtterance(voice_session_id=session.id, transcript_raw=["这是录音保存测试。", "我又来测试了。我出生在八十年代，奶奶带着我长大。", "今天早晨吃了粽子。"][i])
        draft = MemoirBookChapterDraft(project_id=project.id, chapter_id=chapter.id)
        test_db.add_all([section, material, draft])
        await test_db.flush()
        test_db.add_all([
            MemoirSectionMaterial(project_id=project.id, section_id=section.id, utterance_id=material.id),
            MemoirBookChapterDraftVersion(draft_id=draft.id, project_id=project.id, chapter_id=chapter.id, version=1, content=material.transcript_raw),
        ])
        chapters.append(chapter.id)
        sections.append(section.id)
        materials.append(material.id)
    extra = MemoirSection(project_id=project.id, chapter_id=chapters[1], title="去外婆家的童年经历", sort_order=1)
    test_db.add(extra)
    await test_db.commit()
    return {"pid": project.id, "uid": uid, "chapters": chapters, "sections": sections + [extra.id], "materials": materials}


async def preview(client, monkeypatch, story, operations, *, questions=None, instruction="帮我改一下", supports_chapter_split=False, supports_section_merge=True, supports_composed_chapter_edits=True, supports_multiple_chapter_splits=True, reply="请看看这些修改。"):
    fake = FakeAI({"operations": operations, "questions": questions or [], "reply": reply})
    requested = []

    def factory(model):
        requested.append(model)
        return fake

    monkeypatch.setattr("app.services.memoir.voice_edit.get_ai_service", factory)
    response = await client.post(f"/api/memoir/projects/{story['pid']}/voice-edit/preview", json={"instruction": instruction, "ai_model": "deepseek-v4-flash", "supports_chapter_split": supports_chapter_split, "supports_section_merge": supports_section_merge, "supports_composed_chapter_edits": supports_composed_chapter_edits, "supports_multiple_chapter_splits": supports_multiple_chapter_splits})
    assert requested == ["deepseek-v4-flash"]
    return response, fake


async def apply(client, story, preview_data, operations=None):
    chosen = operations if operations is not None else [
        {
            **{k: op[k] for k in ("action", "target_ref", "new_title")},
            **({"utterance_ids": op["utterance_ids"]} if op.get("utterance_ids") else {}),
            **({"section_ids": op["section_ids"]} if op.get("section_ids") else {}),
        }
        for op in preview_data["operations"]
    ]
    return await client.post(f"/api/memoir/projects/{story['pid']}/voice-edit/apply", json={"signature": preview_data["signature"], "operations": chosen})


async def test_raw_speech_context_and_both_renames(client, test_db, monkeypatch, story):
    operations = [
        {"action": "rename_chapter", "target_ref": story["chapters"][1], "new_title": "八十年代的童年"},
        {"action": "rename_section", "target_ref": story["sections"][3], "new_title": "外婆家的快乐时光"},
    ]
    raw = "童年记忆这一张换一个名字，去外婆家的童年经历这个，小杰也换个名字。"
    response, fake = await preview(client, monkeypatch, story, operations, instruction=raw)
    assert response.status_code == 200, response.text
    assert fake.inputs[0]["instruction"] == raw
    schema = json.loads(fake.system.split("返回 JSON Schema：\n", 1)[1])
    assert schema["properties"]["operations"]["type"] == "array"
    assert any("我又来测试了。我出生在八十年代" in m["raw"] for m in fake.inputs[0]["book_data"]["materials"])
    assert (await test_db.get(MemoirBookPlanChapter, story["chapters"][1])).title == "童年回忆"
    assert (await apply(client, story, response.json())).status_code == 200
    assert (await test_db.get(MemoirBookPlanChapter, story["chapters"][1])).title == "八十年代的童年"
    assert (await test_db.get(MemoirSection, story["sections"][3])).title == "外婆家的快乐时光"
    assert (await test_db.get(MemoirSection, story["sections"][1])).title == "童年回忆"
    assert (await apply(client, story, response.json())).status_code == 409


async def test_regenerate_chapter_sections_preview_queues_existing_workflow(
    client,
    test_db,
    monkeypatch,
    story,
):
    operation = {
        "action": "regenerate_chapter_sections",
        "target_ref": story["chapters"][1],
    }
    response, fake = await preview(
        client,
        monkeypatch,
        story,
        [operation],
        instruction="看看是不是可以给第一章生成一些小节。",
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "regenerate_chapter_sections" in fake.system
    assert data["operations"][0]["label"] == "重新整理章节「童年回忆」的小节分组"
    assert "原始讲述和现有正文版本会保留" in data["operations"][0]["detail"]

    result = await apply(client, story, data)
    assert result.status_code == 200, result.text
    assert "已开始重新整理小节" in result.json()["message"]
    job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == story["pid"],
            MemoirEditingJob.chapter_id == story["chapters"][1],
            MemoirEditingJob.job_type == MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS.value,
        )
    )
    assert job is not None
    assert job.status == "pending"
    expected = job.payload_json["request"]["expected_sections"]
    assert {item["id"] for item in expected} == {
        story["sections"][1],
        story["sections"][3],
    }
    assert await test_db.get(MemoirSection, story["sections"][1]) is not None
    assert await test_db.get(MemoirSection, story["sections"][3]) is not None


async def test_reduce_sections_voice_edit_queues_natural_consolidation(
    client, test_db, monkeypatch, story,
):
    operation = {
        "action": "consolidate_chapter_sections",
        "target_ref": story["chapters"][1],
    }
    response, fake = await preview(
        client, monkeypatch, story, [operation],
        instruction="第二章的小节太多，合并得更少一些",
    )
    assert response.status_code == 200, response.text
    item = response.json()["operations"][0]
    assert "目前 2 个，整理后更少" in item["label"]
    assert "不预设合并后的数量" in item["detail"]
    assert "必须使用 consolidate_chapter_sections" in fake.system

    result = await apply(client, story, response.json())
    assert result.status_code == 200, result.text
    job = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.project_id == story["pid"],
        MemoirEditingJob.chapter_id == story["chapters"][1],
        MemoirEditingJob.job_type == MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS.value,
    ))
    assert job is not None
    assert job.payload_json["request"]["section_count_goal"] == "reduce"
    assert job.payload_json["request"]["max_sections"] is None
    assert await test_db.get(MemoirSection, story["sections"][1]) is not None
    assert await test_db.get(MemoirSection, story["sections"][3]) is not None


@pytest.mark.parametrize("action", ["regenerate_chapter_sections", "consolidate_chapter_sections"])
@pytest.mark.parametrize("selected", ["both", "rename", "structure"])
async def test_chapter_review_can_confirm_rename_and_structure_independently(
    client, test_db, monkeypatch, story, action, selected,
):
    chapter_id = story["chapters"][1]
    ops = [
        {"action": action, "target_ref": chapter_id},
        {"action": "rename_chapter", "target_ref": chapter_id, "new_title": "八十年代的童年"},
    ]
    before = await _story(test_db, story["pid"])
    response, _ = await preview(client, monkeypatch, story, ops,
        instruction="内容多了一些，请整体审阅所有章节、小节的组织结构和名字，看看可以怎么调整。")
    assert response.status_code == 200, response.text
    assert len(response.json()["operations"]) == 2
    assert semantic_json_signature(await _story(test_db, story["pid"])) == semantic_json_signature(before)
    chosen = [op for op in ops if selected == "both" or (op["action"] == "rename_chapter") == (selected == "rename")]
    result = await apply(client, story, response.json(), operations=chosen)
    assert result.status_code == 200, result.text
    chapter = await test_db.get(MemoirBookPlanChapter, chapter_id)
    assert chapter.title == ("童年回忆" if selected == "structure" else "八十年代的童年")
    job = await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.chapter_id == chapter_id,
        MemoirEditingJob.job_type == MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS.value,
    ))
    if selected == "rename":
        assert job is None
    else:
        assert job is not None and job.status == "pending"
        assert job.payload_json["request"]["section_count_goal"] == (
            "reduce" if action == "consolidate_chapter_sections" else None
        )
    after = await _story(test_db, story["pid"])
    assert after["sections"] == before["sections"]
    assert after["memberships"] == before["memberships"]
    assert after["materials"] == before["materials"]
    if selected != "structure":
        assert (await apply(client, story, response.json(), operations=chosen)).status_code == 409


@pytest.mark.parametrize("failure", ["stale", "enqueue"])
async def test_composed_chapter_edit_is_atomic(client, test_db, monkeypatch, story, failure):
    chapter_id = story["chapters"][1]
    ops = [
        {"action": "rename_chapter", "target_ref": chapter_id, "new_title": "不应保存的改名"},
        {"action": "regenerate_chapter_sections", "target_ref": chapter_id},
    ]
    response, _ = await preview(client, monkeypatch, story, ops)
    assert response.status_code == 200, response.text
    if failure == "stale":
        section = await test_db.get(MemoirSection, story["sections"][3])
        section.title = "确认前发生变化"
        section.version += 1
        await test_db.commit()
    else:
        async def reject_enqueue(*args, **kwargs):
            chapter = await test_db.get(MemoirBookPlanChapter, chapter_id)
            assert chapter.title == "不应保存的改名"
            raise HTTPException(409, "本章已有另一项小节整理任务在运行")
        monkeypatch.setattr("app.services.memoir.voice_edit.enqueue_chapter_section_regeneration", reject_enqueue)
    result = await apply(client, story, response.json())
    assert result.status_code == 409, result.text
    assert (await test_db.get(MemoirBookPlanChapter, chapter_id)).title == "童年回忆"
    assert await test_db.scalar(select(MemoirEditingJob).where(
        MemoirEditingJob.chapter_id == chapter_id,
        MemoirEditingJob.job_type == MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS.value,
    )) is None


async def test_composed_chapter_edits_require_client_support_and_reject_conflicts(
    client, test_db, monkeypatch, story,
):
    chapter_id = story["chapters"][1]
    rename = {"action": "rename_chapter", "target_ref": chapter_id, "new_title": "八十年代的童年"}
    structure = {"action": "regenerate_chapter_sections", "target_ref": chapter_id}
    legacy, _ = await preview(client, monkeypatch, story, [rename, structure], supports_composed_chapter_edits=False)
    assert legacy.status_code == 200
    assert legacy.json()["operations"] == []
    for conflicting in [rename, {**rename, "new_title": "另一个名字"},
                        {"action": "delete_chapter", "target_ref": chapter_id},
                        {"action": "consolidate_chapter_sections", "target_ref": chapter_id}]:
        first = structure if conflicting["action"] == "consolidate_chapter_sections" else rename
        response, _ = await preview(client, monkeypatch, story, [first, conflicting])
        assert response.status_code == 422, response.text
    assert (await test_db.get(MemoirBookPlanChapter, chapter_id)).title == "童年回忆"


async def test_regenerate_chapter_sections_rejects_stale_or_mixed_chapter_edits(
    client,
    test_db,
    monkeypatch,
    story,
):
    operation = {
        "action": "regenerate_chapter_sections",
        "target_ref": story["chapters"][1],
    }
    mixed, _ = await preview(
        client,
        monkeypatch,
        story,
        [
            operation,
            {
                "action": "rename_section",
                "target_ref": story["sections"][3],
                "new_title": "外婆家的快乐时光",
            },
        ],
    )
    assert mixed.status_code == 422
    assert "不能和同一章内的其他修改同时执行" in mixed.json()["detail"]

    response, _ = await preview(client, monkeypatch, story, [operation])
    assert response.status_code == 200, response.text
    section = await test_db.get(MemoirSection, story["sections"][3])
    section.title = "后来修改的小节"
    section.version += 1
    await test_db.commit()
    result = await apply(client, story, response.json())
    assert result.status_code == 409
    job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.chapter_id == story["chapters"][1],
            MemoirEditingJob.job_type == MemoirEditingJobType.REGENERATE_CHAPTER_SECTIONS.value,
        )
    )
    assert job is None


async def test_split_chapter_preview_moves_only_confirmed_whole_utterances(
    client, test_db, monkeypatch, story,
):
    source_chapter = story["chapters"][2]
    original_material = story["materials"][2]
    session = await test_db.scalar(select(MemoirVoiceSession).where(MemoirVoiceSession.project_id == story["pid"]))
    moved = MemoirVoiceUtterance(
        voice_session_id=session.id,
        transcript_raw="辞职以后，我每天下午去运动，晚上学着做饭。",
    )
    test_db.add(moved)
    await test_db.flush()
    test_db.add(MemoirSectionMaterial(
        project_id=story["pid"], section_id=story["sections"][2], utterance_id=moved.id,
        sort_order=1,
    ))
    await test_db.commit()

    operation = {
        "action": "split_chapter", "target_ref": source_chapter,
        "new_title": "辞职后的日常", "utterance_ids": [moved.id],
    }
    response, fake = await preview(
        client, monkeypatch, story, [operation],
        instruction="把第三章里辞职后的运动和做饭放到新章节。",
        supports_chapter_split=True,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "split_chapter" in fake.system
    assert next(chapter for chapter in fake.inputs[0]["book_data"]["chapters"] if chapter["ref"] == source_chapter)["number"] == 3
    assert next(material for material in fake.inputs[0]["book_data"]["materials"] if material["ref"] == moved.id)["created_at"]
    assert "第3章" in data["operations"][0]["label"]
    assert "辞职后的日常" in data["operations"][0]["label"]
    assert "每天下午去运动" in data["operations"][0]["detail"]
    assert data["operations"][0]["utterance_ids"] == [moved.id]

    result = await apply(client, story, data, operations=[operation])
    assert result.status_code == 200, result.text
    after = await _story(test_db, story["pid"])
    new_chapter = next(chapter for chapter in after["chapters"] if chapter["title"] == "辞职后的日常")
    assert new_chapter["number"] == 4
    assert moved.id in {
        material["ref"] for material in after["materials"]
        if any(section["ref"] == material["section_ref"] and section["chapter_ref"] == new_chapter["ref"] for section in after["sections"])
    }
    assert original_material in {
        material["ref"] for material in after["materials"]
        if any(section["ref"] == material["section_ref"] and section["chapter_ref"] == source_chapter for section in after["sections"])
    }
    assert (await apply(client, story, data, operations=[operation])).status_code == 409


async def test_split_chapter_moves_last_two_sections_without_collapsing_them(
    client, test_db, monkeypatch, story,
):
    source_chapter = story["chapters"][2]
    session = await test_db.scalar(select(MemoirVoiceSession).where(MemoirVoiceSession.project_id == story["pid"]))
    moved_sections = []
    expected_links = {}
    included_ids = []
    for order in range(1, 11):
        section = MemoirSection(
            id=f"00000000-0000-0000-0000-{1000 - order:012d}",
            project_id=story["pid"], chapter_id=source_chapter,
            title={9: "辞职后的日常：运动与做饭", 10: "重拾羽毛球：找搭子"}.get(order, f"工作经历{order}"),
            sort_order=order,
        )
        test_db.add(section)
        await test_db.flush()
        if order < 9:
            continue
        moved_sections.append(section)
        for material_order in range(3):
            utterance = MemoirVoiceUtterance(
                voice_session_id=session.id,
                transcript_raw=f"小节{order}的第{material_order + 1}条讲述。",
            )
            test_db.add(utterance)
            await test_db.flush()
            included = material_order < 2
            link = MemoirSectionMaterial(
                project_id=story["pid"], section_id=section.id,
                utterance_id=utterance.id, sort_order=material_order,
                include_in_book=included,
            )
            test_db.add(link)
            await test_db.flush()
            expected_links[link.id] = (section.id, utterance.id, included)
            if included:
                included_ids.append(utterance.id)
    source_draft = await test_db.scalar(select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == source_chapter))
    source_draft.status = "confirmed"
    plan_row = await test_db.scalar(select(MemoirBookPlan).where(MemoirBookPlan.project_id == story["pid"]))
    output = MemoirBookOutput(
        project_id=story["pid"], plan_id=plan_row.id,
        title="我的回忆录", status="accepted",
    )
    test_db.add(output)
    await test_db.commit()

    before = await _story(test_db, story["pid"])
    assert [section["ref"] for section in before["sections"] if section["chapter_ref"] == source_chapter][-2:] == [
        section.id for section in moved_sections
    ]
    operation = {
        "action": "split_chapter", "target_ref": source_chapter,
        "section_ids": [section.id for section in moved_sections],
    }
    response, _ = await preview(
        client, monkeypatch, story, [operation],
        instruction="把第三章最后两个小节里辞职后运动、做饭和羽毛球这些放到新章节。",
        supports_chapter_split=True,
        supports_multiple_chapter_splits=False,
        reply="第三章有 11 个小节，请您逐条阅读这些讲述，再告诉我是否要移动；还请您想一个新章节名称。" * 5,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["questions"] == []
    assert len(data["operations"]) == 1
    item = data["operations"][0]
    assert item["new_title"] == "辞职后的日常与重拾羽毛球"
    assert item["section_ids"] == [section.id for section in moved_sections]
    assert item["utterance_ids"] == []
    assert "第3章「早晨日常」" in item["label"]
    assert "辞职后的日常" in item["label"] and "重拾羽毛球" in item["label"]
    assert "讲述全文" not in item["detail"]
    assert "小节9的第1条讲述" not in item["detail"]
    assert "新章节名" in data["reply"]
    assert len(data["reply"]) < 30

    result = await apply(client, story, data)
    assert result.status_code == 200, result.text
    plan = (await client.get(f"/api/mobile/memoir/projects/{story['pid']}/book-plan")).json()
    new_chapter = next(chapter for chapter in plan["chapters"] if chapter["title"] == item["new_title"])
    assert new_chapter["sort_order"] == 3
    assert {ref["utterance_id"] for ref in new_chapter["source_refs"]} == set(included_ids)
    assert {ref["utterance_id"] for ref in plan["chapters"][2]["source_refs"]} == {story["materials"][2]}
    for index, section in enumerate(moved_sections):
        await test_db.refresh(section)
        assert section.chapter_id == new_chapter["id"]
        assert section.sort_order == index
        assert section.version > 1
    links = list((await test_db.scalars(select(MemoirSectionMaterial).where(
        MemoirSectionMaterial.id.in_(expected_links)
    ))).all())
    assert {link.id: (link.section_id, link.utterance_id, link.include_in_book) for link in links} == expected_links
    await test_db.refresh(source_draft)
    await test_db.refresh(output)
    assert source_draft.status == "draft"
    assert output.status == "draft"
    assert (await apply(client, story, data)).status_code == 409


@pytest_asyncio.fixture
async def multiple_split_story(story, test_db):
    session = await test_db.scalar(select(MemoirVoiceSession).where(MemoirVoiceSession.project_id == story["pid"]))
    operations = []
    for group, title in enumerate(["婚后经历", "执教成就"]):
        section_ids = []
        for index in range(2):
            section = MemoirSection(
                project_id=story["pid"], chapter_id=story["chapters"][0],
                title=f"{title}{index + 1}", sort_order=group * 2 + index + 1,
            )
            utterance = MemoirVoiceUtterance(
                voice_session_id=session.id, transcript_raw=f"{title}的第{index + 1}条讲述。",
            )
            test_db.add_all([section, utterance])
            await test_db.flush()
            test_db.add(MemoirSectionMaterial(
                project_id=story["pid"], section_id=section.id, utterance_id=utterance.id,
            ))
            section_ids.append(section.id)
        operations.append({
            "action": "split_chapter", "target_ref": story["chapters"][0],
            "new_title": title, "section_ids": section_ids,
        })
    await test_db.commit()
    return story, operations


@pytest.mark.parametrize("selection", [[0, 1], [0], [1]])
async def test_split_one_chapter_into_three_preserves_groups_and_independent_confirmation(
    client, test_db, monkeypatch, multiple_split_story, selection,
):
    story, operations = multiple_split_story
    before = await _story(test_db, story["pid"])
    response, _ = await preview(
        client, monkeypatch, story, operations, supports_chapter_split=True,
        instruction="第一章内容太多了，拆一下章节。\n拆成三张吧。",
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(json.loads(data["signature"])) == 2
    assert len((await _story(test_db, story["pid"]))["chapters"]) == len(before["chapters"])
    chosen = [operations[index] for index in selection]
    result = await apply(client, story, data, operations=chosen)
    assert result.status_code == 200, result.text
    after = await _story(test_db, story["pid"])
    assert [chapter["title"] for chapter in after["chapters"]] == [
        "测试录音", *[op["new_title"] for op in chosen], "童年回忆", "早晨日常",
    ]
    for op in chosen:
        chapter = next(c for c in after["chapters"] if c["title"] == op["new_title"])
        assert [s["ref"] for s in after["sections"] if s["chapter_ref"] == chapter["ref"]] == op["section_ids"]
        item = next(item for item in data["operations"] if item["new_title"] == op["new_title"])
        assert op["new_title"] in item["detail"]
    assert after["memberships"] == before["memberships"]
    assert after["materials"] == before["materials"]
    assert (await apply(client, story, data, operations=chosen)).status_code == 409


@pytest.mark.parametrize("conflict", ["overlap", "empty_source", "mixed_selection", "rename", "delete"])
async def test_multiple_splits_reject_conflicting_batches(client, test_db, monkeypatch, multiple_split_story, conflict):
    story, operations = multiple_split_story
    if conflict == "overlap":
        operations[1]["section_ids"].append(operations[0]["section_ids"][0])
    elif conflict == "empty_source":
        operations[1]["section_ids"].append(story["sections"][0])
    elif conflict == "mixed_selection":
        snapshot = await _story(test_db, story["pid"])
        selected = set(operations[1].pop("section_ids"))
        operations[1]["utterance_ids"] = [
            link["material_ref"] for link in snapshot["memberships"] if link["section_ref"] in selected
        ]
    else:
        operations.append({
            "action": f"{conflict}_chapter", "target_ref": story["chapters"][0],
            **({"new_title": "新的名字"} if conflict == "rename" else {}),
        })
    response, _ = await preview(client, monkeypatch, story, operations, supports_chapter_split=True)
    assert response.status_code == 422, response.text
    assert len((await _story(test_db, story["pid"]))["chapters"]) == 3


async def test_multiple_splits_require_a_client_that_can_confirm_each_group(client, monkeypatch, multiple_split_story):
    story, operations = multiple_split_story
    response, fake = await preview(
        client, monkeypatch, story, operations,
        supports_chapter_split=True, supports_multiple_chapter_splits=False,
    )
    assert response.status_code == 200, response.text
    assert response.json()["operations"] == []
    assert response.json()["questions"] == []
    assert "更新小程序" in response.json()["reply"]
    assert "不能分别确认同一章拆出的多个新章" in fake.system


async def test_multiple_splits_roll_back_the_whole_batch_after_a_later_failure(
    client, test_db, monkeypatch, multiple_split_story,
):
    from app.services.memoir.voice_edit import apply_book_plan_structure_command

    story, operations = multiple_split_story
    response, _ = await preview(client, monkeypatch, story, operations, supports_chapter_split=True)
    assert response.status_code == 200, response.text
    before = await _story(test_db, story["pid"])
    calls = 0

    async def fail_second_split(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HTTPException(422, "第二组拆分失败")
        return await apply_book_plan_structure_command(*args, **kwargs)

    monkeypatch.setattr("app.services.memoir.voice_edit.apply_book_plan_structure_command", fail_second_split)
    result = await apply(client, story, response.json())
    assert result.status_code == 422, result.text
    assert calls == 2
    assert await _story(test_db, story["pid"]) == before


async def test_multiple_material_splits_keep_source_text_and_chapter_order(
    client, test_db, monkeypatch, multiple_split_story,
):
    story, operations = multiple_split_story
    before = await _story(test_db, story["pid"])
    for op in operations:
        selected = set(op.pop("section_ids"))
        op["utterance_ids"] = [link["material_ref"] for link in before["memberships"] if link["section_ref"] in selected]
    response, _ = await preview(client, monkeypatch, story, operations, supports_chapter_split=True)
    assert response.status_code == 200, response.text
    result = await apply(client, story, response.json())
    assert result.status_code == 200, result.text
    after = await _story(test_db, story["pid"])
    assert [c["title"] for c in after["chapters"]] == ["测试录音", "婚后经历", "执教成就", "童年回忆", "早晨日常"]
    assert [(m["ref"], m["raw"], m["text"]) for m in after["materials"]] == [
        (m["ref"], m["raw"], m["text"]) for m in before["materials"]
    ]
    for op in operations:
        chapter_id = next(c["ref"] for c in after["chapters"] if c["title"] == op["new_title"])
        section_ids = {s["ref"] for s in after["sections"] if s["chapter_ref"] == chapter_id}
        assert {link["material_ref"] for link in after["memberships"] if link["section_ref"] in section_ids} == set(op["utterance_ids"])


async def test_split_section_preview_rejects_changed_section(client, test_db, monkeypatch, story):
    chapter = story["chapters"][1]
    section = await test_db.get(MemoirSection, story["sections"][1])
    extra = await test_db.get(MemoirSection, story["sections"][3])
    wrong, _ = await preview(client, monkeypatch, story, [{
        "action": "split_chapter", "target_ref": chapter,
        "section_ids": [story["sections"][2]], "new_title": "童年往事",
    }], supports_chapter_split=True)
    assert wrong.status_code == 422
    response, _ = await preview(client, monkeypatch, story, [{
        "action": "split_chapter", "target_ref": chapter,
        "section_ids": [section.id, extra.id], "new_title": "童年往事",
    }], supports_chapter_split=True)
    assert response.status_code == 422  # the original chapter would have no included source left

    session = await test_db.scalar(select(MemoirVoiceSession).where(MemoirVoiceSession.project_id == story["pid"]))
    utterance = MemoirVoiceUtterance(voice_session_id=session.id, transcript_raw="在外婆家过暑假。")
    test_db.add(utterance)
    await test_db.flush()
    test_db.add(MemoirSectionMaterial(project_id=story["pid"], section_id=extra.id, utterance_id=utterance.id))
    await test_db.commit()
    extra.include_in_book = False
    await test_db.commit()
    hidden, _ = await preview(client, monkeypatch, story, [{
        "action": "split_chapter", "target_ref": chapter,
        "section_ids": [extra.id], "new_title": "外婆家的夏天",
    }], supports_chapter_split=True)
    assert hidden.status_code == 422
    extra.include_in_book = True
    await test_db.commit()
    response, _ = await preview(client, monkeypatch, story, [{
        "action": "split_chapter", "target_ref": chapter,
        "section_ids": [extra.id], "new_title": "外婆家的夏天",
    }], supports_chapter_split=True)
    assert response.status_code == 200, response.text
    extra.title = "修改后的小节"
    extra.version += 1
    await test_db.commit()
    result = await apply(client, story, response.json())
    assert result.status_code == 409
    assert len((await _story(test_db, story["pid"]))["chapters"]) == 3


async def test_split_title_fallback_rejects_unknown_chapter(client, monkeypatch, story):
    response, _ = await preview(client, monkeypatch, story, [{
        "action": "split_chapter", "target_ref": "not-a-chapter",
        "utterance_ids": [story["materials"][2]],
    }], supports_chapter_split=True)
    assert response.status_code == 422


async def test_split_chapter_rejects_wrong_or_complete_source_selection(
    client, monkeypatch, story,
):
    chapter = story["chapters"][2]
    for selected in ([story["materials"][1]], [story["materials"][2]]):
        response, _ = await preview(client, monkeypatch, story, [{
            "action": "split_chapter", "target_ref": chapter,
            "new_title": "日常生活", "utterance_ids": selected,
        }], supports_chapter_split=True)
        assert response.status_code == 422
    uncertain, _ = await preview(client, monkeypatch, story, [{
        "action": "split_chapter", "target_ref": chapter,
        "new_title": "日常生活", "utterance_ids": [story["materials"][2]],
    }], questions=[{
        "target_refs": [chapter],
        "question": "第三章里，您具体要移走哪几条讲述？",
    }], supports_chapter_split=True)
    assert uncertain.status_code == 200
    assert uncertain.json()["operations"] == []
    assert uncertain.json()["questions"] == ["第三章里，您具体要移走哪几条讲述？"]


async def test_old_client_cannot_receive_unexecutable_chapter_split(client, monkeypatch, story):
    response, fake = await preview(client, monkeypatch, story, [{
        "action": "split_chapter", "target_ref": story["chapters"][2],
        "new_title": "辞职后的日常", "utterance_ids": [story["materials"][2]],
    }], instruction="把第三章的日常放进新章节")
    assert response.status_code == 200
    assert response.json()["operations"] == []
    assert "当前小程序版本" in response.json()["reply"]
    assert "不能执行 split_chapter" in fake.system


async def test_selected_deletion_combines_material_and_structure_atomically(client, test_db, monkeypatch, story):
    ops = [
        {"action": "delete_chapter", "target_ref": story["chapters"][0]},
        {"action": "delete_section", "target_ref": story["sections"][0]},
        {"action": "delete_material", "target_ref": story["materials"][0]},
    ]
    response, _ = await preview(client, monkeypatch, story, ops, questions=[{"target_refs": [story["materials"][1]], "question": "带有测试开场白的童年讲述是否保留？"}])
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["operations"]) == 2  # child removal is covered by the parent
    assert "录音保存测试" in data["operations"][0]["detail"]
    assert (await apply(client, story, data)).status_code == 200
    assert await test_db.get(MemoirBookPlanChapter, story["chapters"][0]) is None
    assert await test_db.get(MemoirSection, story["sections"][0]) is None
    assert await test_db.get(MemoirVoiceUtterance, story["materials"][0]) is None
    assert await test_db.get(MemoirVoiceUtterance, story["materials"][1]) is not None
    assert not (await test_db.scalars(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.chapter_id == story["chapters"][0]))).all()


async def test_standalone_section_delete_preserves_material_and_other_sections(client, test_db, monkeypatch, story):
    response, _ = await preview(client, monkeypatch, story, [{"action": "delete_section", "target_ref": story["sections"][1]}])
    assert (await apply(client, story, response.json())).status_code == 200
    assert await test_db.get(MemoirSection, story["sections"][1]) is None
    assert await test_db.get(MemoirSection, story["sections"][3]) is not None
    assert await test_db.get(MemoirVoiceUtterance, story["materials"][1]) is not None
    assert await test_db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.utterance_id == story["materials"][1])) is None


async def test_standalone_chapter_delete_preserves_original_material(client, test_db, monkeypatch, story):
    response, _ = await preview(client, monkeypatch, story, [{
        "action": "delete_chapter", "target_ref": story["chapters"][0],
    }])
    assert response.status_code == 200, response.text
    result = await apply(client, story, response.json())
    assert result.status_code == 200, result.text
    assert await test_db.get(MemoirBookPlanChapter, story["chapters"][0]) is None
    assert await test_db.get(MemoirSection, story["sections"][0]) is None
    assert await test_db.get(MemoirVoiceUtterance, story["materials"][0]) is not None
    assert await test_db.get(MemoirBookPlanChapter, story["chapters"][1]) is not None


@pytest.mark.parametrize("singleton", [False, True])
async def test_voice_merge_moves_section_into_existing_chapter_and_preserves_other_groups(
    client, test_db, monkeypatch, story, singleton,
):
    source = story["sections"][0 if singleton else 1]
    source_chapter = story["chapters"][0 if singleton else 1]
    target = story["sections"][2]
    session = await test_db.scalar(select(MemoirVoiceSession).where(MemoirVoiceSession.project_id == story["pid"]))
    excluded = MemoirVoiceUtterance(voice_session_id=session.id, transcript_raw="暂不收录但仍属于这一小节的讲述。")
    unrelated = MemoirVoiceUtterance(voice_session_id=session.id, transcript_raw="其他小节的讲述保持原位置。")
    test_db.add_all([excluded, unrelated])
    await test_db.flush()
    test_db.add_all([
        MemoirSectionMaterial(project_id=story["pid"], section_id=source, utterance_id=excluded.id, sort_order=1, include_in_book=False),
        MemoirSectionMaterial(project_id=story["pid"], section_id=story["sections"][3], utterance_id=unrelated.id),
    ])
    plan = await test_db.scalar(select(MemoirBookPlan).where(MemoirBookPlan.project_id == story["pid"]))
    output = MemoirBookOutput(project_id=story["pid"], plan_id=plan.id, title="我的回忆录", status="accepted")
    drafts = list((await test_db.scalars(select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.project_id == story["pid"]))).all())
    for draft in drafts:
        draft.status = "confirmed"
    test_db.add(output)
    await test_db.commit()
    before = await _story(test_db, story["pid"])
    links_before = {m["ref"]: m for m in before["memberships"]}
    op = {"action": "merge_sections", "target_ref": target, "section_ids": [source]}
    response, fake = await preview(
        client, monkeypatch, story, [op],
        instruction="把这个章节里的小节挪到早晨日常章节，和那里的小节合并。",
    )
    assert response.status_code == 200, response.text
    assert "必须使用 merge_sections" in fake.system
    item = response.json()["operations"][0]
    assert "合并到「早晨日常」中的「早晨日常」" in item["label"]
    assert ("将移除" in item["detail"]) == singleton
    result = await apply(client, story, response.json())
    assert result.status_code == 200, result.text
    after = await _story(test_db, story["pid"])
    assert len(after["chapters"]) == len(before["chapters"]) - int(singleton)
    assert {s["ref"] for s in after["sections"]} == {s["ref"] for s in before["sections"]} - {source}
    assert {m["ref"]: (m["raw"], m["text"]) for m in after["materials"]} == {
        m["ref"]: (m["raw"], m["text"]) for m in before["materials"]
    }
    for membership in after["memberships"]:
        old = links_before[membership["ref"]]
        assert membership["section_ref"] == (target if old["section_ref"] == source else old["section_ref"])
        assert membership["included"] == old["included"]
        assert membership["version"] == old["version"]
    assert (await test_db.get(MemoirBookPlanChapter, source_chapter) is None) == singleton
    await test_db.refresh(output)
    assert output.status == "draft"
    for draft in drafts:
        if draft.chapter_id in {source_chapter, story["chapters"][2]} and not (singleton and draft.chapter_id == source_chapter):
            await test_db.refresh(draft)
            assert draft.status == "draft"
    assert (await apply(client, story, response.json())).status_code == 409


async def test_voice_merge_keeps_source_chapter_with_hidden_section(client, test_db, monkeypatch, story):
    source = story["sections"][0]
    hidden = MemoirSection(project_id=story["pid"], chapter_id=story["chapters"][0], title="尚未收录的小节", include_in_book=False, sort_order=1)
    test_db.add(hidden)
    await test_db.commit()
    response, _ = await preview(client, monkeypatch, story, [{
        "action": "merge_sections", "target_ref": story["sections"][2], "section_ids": [source],
    }])
    assert "将移除" not in response.json()["operations"][0]["detail"]
    result = await apply(client, story, response.json())
    assert result.status_code == 200, result.text
    assert await test_db.get(MemoirBookPlanChapter, story["chapters"][0]) is not None
    assert await test_db.get(MemoirSection, hidden.id) is not None


@pytest.mark.parametrize("change", ["destination", "destination_deleted", "source", "new_sibling", "source_draft"])
async def test_voice_merge_rejects_changed_source_or_destination(client, test_db, monkeypatch, story, change):
    op = {"action": "merge_sections", "target_ref": story["sections"][2], "section_ids": [story["sections"][0]]}
    response, _ = await preview(client, monkeypatch, story, [op])
    if change == "destination_deleted":
        await test_db.delete(await test_db.get(MemoirSection, op["target_ref"]))
    elif change == "new_sibling":
        test_db.add(MemoirSection(project_id=story["pid"], chapter_id=story["chapters"][0], title="新补的小节", sort_order=1))
    elif change == "source_draft":
        draft = await test_db.scalar(select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == story["chapters"][0]))
        test_db.add(MemoirBookChapterDraftVersion(draft_id=draft.id, project_id=story["pid"], chapter_id=draft.chapter_id, version=2, content="后来修改的章节正文。"))
    else:
        section = await test_db.get(MemoirSection, op["target_ref"] if change == "destination" else op["section_ids"][0])
        section.title = "后来修改的标题"
        section.version += 1
    await test_db.commit()
    before = await _story(test_db, story["pid"])
    result = await apply(client, story, response.json())
    assert result.status_code == 409, result.text
    assert await _story(test_db, story["pid"]) == before


async def test_voice_merge_failure_rolls_back_material_move(client, test_db, monkeypatch, story):
    response, _ = await preview(client, monkeypatch, story, [{
        "action": "merge_sections", "target_ref": story["sections"][2], "section_ids": [story["sections"][0]],
    }])
    before = await _story(test_db, story["pid"])

    async def fail(*args, **kwargs):
        raise HTTPException(409, "模拟移除空章时发生冲突")

    monkeypatch.setattr("app.services.memoir.voice_edit.apply_book_plan_structure_command", fail)
    result = await apply(client, story, response.json())
    assert result.status_code == 409
    assert await _story(test_db, story["pid"]) == before


async def test_voice_merge_requires_current_sections_and_unambiguous_batch(client, test_db, monkeypatch, story):
    op = {"action": "merge_sections", "target_ref": story["sections"][2], "section_ids": [story["sections"][0]]}
    response, _ = await preview(client, monkeypatch, story, [op, {
        "action": "delete_chapter", "target_ref": story["chapters"][2],
    }])
    assert response.status_code == 422
    hidden = await test_db.get(MemoirSection, story["sections"][0])
    hidden.include_in_book = False
    await test_db.commit()
    response, _ = await preview(client, monkeypatch, story, [op])
    assert response.status_code == 422
    hidden.include_in_book = True
    await test_db.commit()
    uncertain, _ = await preview(client, monkeypatch, story, [op], questions=[{
        "target_refs": [op["target_ref"]], "question": "要放入哪一个小节？",
    }])
    assert uncertain.status_code == 200
    assert uncertain.json()["operations"] == []
    legacy, _ = await preview(client, monkeypatch, story, [op], supports_section_merge=False)
    assert legacy.status_code == 200
    assert legacy.json()["operations"] == []
    assert "更新小程序" in legacy.json()["reply"]


async def test_background_draft_completion_does_not_block_section_delete(client, test_db, monkeypatch, story):
    target = story["sections"][3]
    response, fake = await preview(client, monkeypatch, story, [{"action": "delete_section", "target_ref": target}])
    assert response.status_code == 200, response.text
    other_chapter = story["chapters"][2]
    original = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.chapter_id == other_chapter))
    assert any(d["ref"] == original.id for d in fake.inputs[0]["book_data"]["drafts"])
    # Reproduce production: another chapter finishes generating after the edit
    # snapshot was read, while the section selected for deletion stays unchanged.
    generated = MemoirBookChapterDraftVersion(
        draft_id=original.draft_id, project_id=story["pid"], chapter_id=other_chapter,
        version=2, content="后台刚整理好的日常饮食正文。",
    )
    test_db.add(generated)
    await test_db.commit()
    result = await apply(client, story, response.json())
    assert result.status_code == 200, result.text
    assert await test_db.get(MemoirSection, target) is None
    assert await test_db.get(MemoirSection, story["sections"][1]) is not None
    assert await test_db.get(MemoirBookChapterDraftVersion, generated.id) is not None
    assert await test_db.get(MemoirVoiceUtterance, story["materials"][2]) is not None


@pytest.mark.parametrize("other_action", ["rename_chapter", "rename_section", "delete_material", "delete_section", "delete_chapter"])
async def test_edits_in_different_chapters_can_both_apply(client, test_db, monkeypatch, story, other_action):
    target = story["sections"][3]
    response, _ = await preview(client, monkeypatch, story, [{"action": "delete_section", "target_ref": target}])
    kind = other_action.split("_", 1)[1]
    other_op = {"action": other_action, "target_ref": story[kind + "s"][0]}
    if other_action.startswith("rename"):
        other_op["new_title"] = "日常饮食"
    other, _ = await preview(client, monkeypatch, story, [other_op])
    assert (await apply(client, story, other.json())).status_code == 200
    result = await apply(client, story, response.json())
    assert result.status_code == 200, result.text
    assert await test_db.get(MemoirSection, target) is None


async def test_unrelated_material_correction_does_not_block_delete(client, test_db, monkeypatch, story):
    target = story["sections"][3]
    response, _ = await preview(client, monkeypatch, story, [{"action": "delete_section", "target_ref": target}])
    material = await test_db.get(MemoirVoiceUtterance, story["materials"][2])
    material.transcript_edited = "另一个章节的讲述后来校正了。"
    await test_db.commit()
    result = await apply(client, story, response.json())
    assert result.status_code == 200, result.text
    assert await test_db.get(MemoirSection, target) is None
    assert (await test_db.get(MemoirVoiceUtterance, material.id)).transcript_edited == "另一个章节的讲述后来校正了。"


async def test_only_selected_operations_are_checked(client, test_db, monkeypatch, story):
    ops = [
        {"action": "delete_section", "target_ref": story["sections"][3]},
        {"action": "rename_chapter", "target_ref": story["chapters"][2], "new_title": "日常饮食"},
    ]
    response, _ = await preview(client, monkeypatch, story, ops)
    other = await test_db.get(MemoirBookPlanChapter, story["chapters"][2])
    other.title = "未选中的章节后来改了名"
    await test_db.commit()
    result = await apply(client, story, response.json(), operations=ops[:1])
    assert result.status_code == 200, result.text
    assert await test_db.get(MemoirSection, story["sections"][3]) is None
    assert (await test_db.get(MemoirBookPlanChapter, other.id)).title == "未选中的章节后来改了名"


@pytest.mark.parametrize("change", ["title", "membership", "chapter"])
async def test_changed_section_still_blocks_delete(client, test_db, monkeypatch, story, change):
    target = story["sections"][3]
    response, _ = await preview(client, monkeypatch, story, [{"action": "delete_section", "target_ref": target}])
    section = await test_db.get(MemoirSection, target)
    if change == "title":
        section.title = "后来修改的小节标题"
        section.version += 1
    elif change == "membership":
        membership = await test_db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.utterance_id == story["materials"][1]))
        membership.section_id = target
    else:
        section.chapter_id = story["chapters"][2]
    await test_db.commit()
    result = await apply(client, story, response.json())
    assert result.status_code == 409, result.text
    assert await test_db.get(MemoirSection, target) is not None


async def test_conflict_in_selected_batch_prevents_all_edits(client, test_db, monkeypatch, story):
    ops = [
        {"action": "rename_chapter", "target_ref": story["chapters"][2], "new_title": "不应保存的改名"},
        {"action": "delete_section", "target_ref": story["sections"][3]},
    ]
    response, _ = await preview(client, monkeypatch, story, ops)
    section = await test_db.get(MemoirSection, story["sections"][3])
    section.title = "同时被别人改了名的小节"
    section.version += 1
    await test_db.commit()
    result = await apply(client, story, response.json())
    assert result.status_code == 409
    assert (await test_db.get(MemoirBookPlanChapter, story["chapters"][2])).title == "早晨日常"
    assert await test_db.get(MemoirSection, story["sections"][3]) is not None


@pytest.mark.parametrize("change", ["section", "draft"])
async def test_changed_chapter_contents_block_chapter_delete(client, test_db, monkeypatch, story, change):
    target = story["chapters"][0]
    response, _ = await preview(client, monkeypatch, story, [{"action": "delete_chapter", "target_ref": target}])
    if change == "section":
        test_db.add(MemoirSection(project_id=story["pid"], chapter_id=target, title="后来新增的小节", sort_order=1))
    else:
        original = await test_db.scalar(select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.chapter_id == target))
        test_db.add(MemoirBookChapterDraftVersion(draft_id=original.draft_id, project_id=story["pid"], chapter_id=target, version=2, content="这章后来修改过的正文。"))
    await test_db.commit()
    result = await apply(client, story, response.json())
    assert result.status_code == 409
    assert await test_db.get(MemoirBookPlanChapter, target) is not None


async def test_legacy_preview_requires_refresh_without_writing(client, test_db, story):
    old_signature = semantic_json_signature(await _story(test_db, story["pid"]))
    result = await apply(client, story, {"signature": old_signature}, operations=[{"action": "delete_section", "target_ref": story["sections"][3]}])
    assert result.status_code == 409
    assert "重新查看" in result.json()["detail"]
    assert await test_db.get(MemoirSection, story["sections"][3]) is not None


@pytest.mark.parametrize("action,kind", [("delete_chapter", "chapters"), ("delete_material", "materials")])
async def test_changed_source_blocks_delete(client, test_db, monkeypatch, story, action, kind):
    response, _ = await preview(client, monkeypatch, story, [{"action": action, "target_ref": story[kind][0]}])
    material = await test_db.get(MemoirVoiceUtterance, story["materials"][0])
    material.transcript_edited = "这是后来补充的重要回忆。"
    await test_db.commit()
    result = await apply(client, story, response.json())
    assert result.status_code == 409
    assert await test_db.get(MemoirBookPlanChapter, story["chapters"][0]) is not None
    assert await test_db.get(MemoirVoiceUtterance, story["materials"][0]) is not None


async def test_failure_rolls_back_prior_renames_and_deletions(client, test_db, monkeypatch, story):
    remaining = await test_db.get(MemoirBookPlanChapter, story["chapters"][1])
    remaining.include_in_book = False
    await test_db.commit()
    before = semantic_json_signature(await _story(test_db, story["pid"]))
    ops = [{"action": "rename_section", "target_ref": story["sections"][3], "new_title": "不能留下的改名"}]
    ops += [{"action": "delete_chapter", "target_ref": story["chapters"][i]} for i in (0, 2)]
    response, _ = await preview(client, monkeypatch, story, ops)
    result = await apply(client, story, response.json())
    assert result.status_code == 400, result.text
    assert semantic_json_signature(await _story(test_db, story["pid"])) == before


@pytest.mark.parametrize("action,target_kind", [
    ("delete_chapter", "sections"),
    ("delete_material", "chapters"),
    ("rename_section", "materials"),
    ("regenerate_chapter_sections", "sections"),
])
async def test_wrong_entity_kind_is_rejected(client, monkeypatch, story, action, target_kind):
    op = {"action": action, "target_ref": story[target_kind][0]}
    if action.startswith("rename"):
        op["new_title"] = "新名字"
    response, _ = await preview(client, monkeypatch, story, [op])
    assert response.status_code == 422


async def test_foreign_project_and_uncertain_mutation_are_not_applied(client, test_db, monkeypatch, story):
    foreign = Project(title="别人的回忆", type="memoir")
    test_db.add(foreign)
    await test_db.commit()
    response = await client.post(f"/api/memoir/projects/{foreign.id}/voice-edit/preview", json={"instruction": "删掉"})
    assert response.status_code == 404
    response, _ = await preview(client, monkeypatch, story, [{"action": "delete_material", "target_ref": "foreign-material"}])
    assert response.status_code == 422
    response, _ = await preview(client, monkeypatch, story, [{"action": "delete_material", "target_ref": story["materials"][1]}], questions=[{"target_refs": [story["materials"][1]], "question": "是否整条删除？"}])
    assert response.status_code == 200
    assert response.json()["operations"] == []
    assert response.json()["questions"] == ["是否整条删除？"]
    assert await test_db.get(MemoirVoiceUtterance, story["materials"][1]) is not None
    response, _ = await preview(client, monkeypatch, story, [{"action": "delete_material", "target_ref": story["materials"][1]}], questions=[{"target_refs": [], "question": "请确定要删除哪一条讲述？"}])
    assert response.status_code == 200
    assert response.json()["operations"] == []
    assert response.json()["questions"] == ["请确定要删除哪一条讲述？"]
    response, _ = await preview(client, monkeypatch, story, [{"action": "delete_material", "target_ref": story["materials"][1]}], questions=[{"target_refs": [story["chapters"][1]], "question": "童年回忆这一章，具体要删除哪条讲述？"}])
    assert response.status_code == 200
    assert response.json()["operations"] == []
    assert response.json()["questions"] == ["童年回忆这一章，具体要删除哪条讲述？"]


async def test_no_edit_and_invalid_provider_array_do_not_write(client, test_db, monkeypatch, story):
    response, _ = await preview(client, monkeypatch, story, [], questions=[{"target_refs": [], "question": "您想修改哪部分？"}], instruction="那时候我常去外婆家。")
    assert response.status_code == 200
    assert response.json()["operations"] == []
    bad = FakeAI({"operations": '[{"action":"delete_chapter"}]', "questions": "[]", "reply": "已处理"})
    monkeypatch.setattr("app.services.memoir.voice_edit.get_ai_service", lambda _: bad)
    response = await client.post(f"/api/memoir/projects/{story['pid']}/voice-edit/preview", json={"instruction": "删除测试录音"})
    assert response.status_code == 502
    assert await test_db.get(MemoirBookPlanChapter, story["chapters"][0]) is not None


async def test_provider_timeout_is_visible_and_does_not_retry_or_write(client, test_db, monkeypatch, story):
    class SlowAI(FakeAI):
        calls = 0

        async def _call_llm(self, system, user, **kwargs):
            self.calls += 1
            raise AIProviderTimeoutError(provider=self.provider_name, model=self.model_name, timeout_seconds=60)

    fake = SlowAI(None)
    monkeypatch.setattr("app.services.memoir.voice_edit.get_ai_service", lambda _: fake)
    response = await client.post(f"/api/memoir/projects/{story['pid']}/voice-edit/preview", json={"instruction": "删除测试录音"})
    assert response.status_code == 502
    assert "模型处理超过" in response.json()["detail"]
    assert fake.calls == 1
    assert await test_db.get(MemoirBookPlanChapter, story["chapters"][0]) is not None


@pytest.mark.parametrize("data", [
    {"action": "rename_chapter", "target_ref": "c", "new_title": "  "},
    {"action": "delete_chapter", "target_ref": "c", "new_title": "新名"},
    {"action": "regenerate_chapter_sections", "target_ref": "c", "new_title": "新小节"},
    {"action": "split_chapter", "target_ref": "c", "new_title": "新章节"},
    {"action": "split_chapter", "target_ref": "c", "new_title": "新章节", "utterance_ids": ["u", "u"]},
    {"action": "split_chapter", "target_ref": "c", "new_title": "新章节", "section_ids": ["s", "s"]},
    {"action": "split_chapter", "target_ref": "c", "new_title": "新章节", "section_ids": ["s"], "utterance_ids": ["u"]},
    {"action": "delete_chapter", "target_ref": "c", "section_ids": ["s"]},
    {"action": "execute_sql", "target_ref": "c"},
])
def test_operation_contract_rejects_unexecutable_actions(data):
    with pytest.raises(ValidationError):
        VoiceEditOperation.model_validate(data)


def test_apply_contract_requires_concrete_split_title():
    with pytest.raises(ValidationError):
        VoiceEditApplyRequest.model_validate({
            "signature": "{}",
            "operations": [{"action": "split_chapter", "target_ref": "c", "section_ids": ["s"]}],
        })


def test_provider_string_arrays_are_not_interpreted_as_actions():
    with pytest.raises(ValidationError):
        VoiceEditProposal.model_validate({"operations": "[]", "questions": [], "reply": ""})
