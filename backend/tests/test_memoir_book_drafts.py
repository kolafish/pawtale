import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirAutomationEventType,
    MemoirBookAutoRevisionJobStatus,
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirCreatedBy,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookAutoRevisionJob,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlanChapter,
)
from app.services.memoir.book_auto_revision import (
    run_auto_revision_job,
    schedule_chapter_auto_revision,
)
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_chapter_draft,
    create_test_voice_transcript,
    memoir_source_ref,
)


class FakeBookDraftAI:
    provider_name = "fake-ai"
    model_name = "fake-book-draft"

    def __init__(self, content: str | list[str] = "我小时候常在外婆家的院子里晒太阳。"):
        self.responses = list(content) if isinstance(content, list) else [content]
        self.content = self.responses[-1]
        self.calls = []

    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return self.content


async def test_mobile_memoir_book_chapter_draft_routes_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/projects/project-1/book-plan/chapters/chapter-1/draft",
        json={},
    )

    assert response.status_code == 401


async def test_mobile_memoir_book_chapter_draft_requires_confirmed_plan(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(created),
        status=MemoirBookPlanStatus.DRAFT,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "请先确认章节规划"


async def test_mobile_memoir_book_chapter_draft_get_returns_404_before_draft_exists(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )

    response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "章节稿不存在"


async def test_mobile_memoir_book_chapter_draft_get_returns_existing_draft_with_source_change_counts(
    client,
    test_db,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_refs=[memoir_source_ref(first), memoir_source_ref(second)],
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.metadata_json = {
        "pending_source_utterance_ids": [second["utterance_id"]],
        "removed_source_utterance_ids": [first["utterance_id"]],
    }
    await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        content="我小时候常在外婆家的院子里晒太阳。",
    )

    response = await client.get(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapter_id"] == chapter.id
    assert data["latest_version"]["content"] == "我小时候常在外婆家的院子里晒太阳。"
    assert data["pending_source_count"] == 1
    assert data["removed_source_count"] == 1


async def test_mobile_memoir_book_chapter_draft_generate_uses_chapter_sources(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来我第一次离家去县城读书，心里很紧张。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    fake_ai = FakeBookDraftAI("我小时候常在外婆家的院子里晒太阳。")
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={"ai_model": "deepseek"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert data["latest_version"]["version"] == 1
    assert data["latest_version"]["content"] == "我小时候常在外婆家的院子里晒太阳。"
    assert data["latest_version"]["created_by"] == "ai"
    assert data["latest_version"]["model"] == "fake-book-draft"
    assert data["versions"][0]["prompt_snapshot"]
    prompt = fake_ai.calls[0]["user_prompt"]
    assert "我小时候常在外婆家的院子里晒太阳" in prompt
    assert "第一次离家去县城读书" not in prompt
    assert f"utterance_id: {first['utterance_id']}" in prompt
    assert f"utterance_id: {second['utterance_id']}" not in prompt
    assert "不新增事实、人物、时间、地点、事件、对话或情绪" in prompt

    stored_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == chapter.id)
    )
    stored_versions = (
        await test_db.execute(
            select(MemoirBookChapterDraftVersion)
            .where(MemoirBookChapterDraftVersion.draft_id == stored_draft.id)
            .order_by(MemoirBookChapterDraftVersion.version)
        )
    ).scalars().all()
    assert len(stored_versions) == 1
    assert stored_versions[0].created_by == "ai"


async def test_mobile_memoir_book_chapter_draft_regenerate_appends_version_with_feedback(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    fake_ai = FakeBookDraftAI("第一版正文")
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )
    first_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )
    fake_ai.content = "第二版正文"

    second_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={"force_regenerate": True, "feedback": "更贴近原话"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    data = second_response.json()
    assert data["latest_version"]["version"] == 2
    assert data["latest_version"]["content"] == "第二版正文"
    assert data["latest_version"]["feedback"] == "更贴近原话"
    assert len(data["versions"]) == 2


async def test_mobile_memoir_book_chapter_draft_supplements_pending_sources_without_force(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.source_refs_json = [memoir_source_ref(first), memoir_source_ref(second, "第一次离家")]
    chapter.metadata_json = {
        "pending_source_utterance_ids": [second["utterance_id"]],
        "source_assignment_updated_at": "2026-06-17T00:00:00+00:00",
    }
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
        metadata_json={"status_reason": "new_sources_assigned"},
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.AI.value,
            prompt_snapshot="old prompt",
        )
    )
    await test_db.commit()
    fake_ai = FakeBookDraftAI(
        [
            json.dumps(
                {
                    "mode": "supplement",
                    "reason": "新增来源是新的离家经历，适合补入新段落。",
                    "pending_source_ids": [second["utterance_id"]],
                    "related_existing_source_ids": [],
                    "coverage_requirements": ["补入第一次离家去县城读书的经历"],
                },
                ensure_ascii=False,
            ),
            "我小时候常在外婆家的院子里晒太阳。后来我第一次离家去县城读书。",
        ]
    )
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["latest_version"]["version"] == 2
    assert "第一次离家去县城读书" in data["latest_version"]["content"]
    assert data["metadata_json"] == {}
    assert data["pending_source_count"] == 0
    assert data["removed_source_count"] == 0
    assert len(fake_ai.calls) == 2
    plan_prompt = fake_ai.calls[0]["user_prompt"]
    assert "请判断本章新增来源应该如何并入现有章节正文" in plan_prompt
    assert '"mode": "supplement"' in plan_prompt
    assert f"utterance_id: {second['utterance_id']}" in plan_prompt
    prompt = fake_ai.calls[1]["user_prompt"]
    assert "原章节正文" in prompt
    assert "修订规划" in prompt
    assert "coverage_requirements" in prompt
    assert "本次任务：只把 source_type 为“新增来源”" in prompt
    assert f"utterance_id: {first['utterance_id']}" in prompt
    assert f"utterance_id: {second['utterance_id']}" in prompt
    assert "source_type: 既有来源" in prompt
    assert "source_type: 新增来源" in prompt

    refreshed_chapter = await test_db.scalar(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id)
    )
    assert refreshed_chapter.metadata_json == {}


async def test_memoir_book_auto_revision_job_supplements_pending_sources(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.source_refs_json = [memoir_source_ref(first), memoir_source_ref(second, "第一次离家")]
    chapter.metadata_json = {
        "pending_source_utterance_ids": [second["utterance_id"]],
        "source_assignment_updated_at": "2026-06-17T00:00:00+00:00",
    }
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
        metadata_json={"status_reason": "new_sources_assigned"},
    )
    test_db.add(draft)
    await test_db.flush()
    version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter.id,
        version=1,
        content="我小时候常在外婆家的院子里晒太阳。",
        created_by=MemoirCreatedBy.AI.value,
        prompt_snapshot="old prompt",
    )
    test_db.add(version)
    await test_db.commit()

    job = await schedule_chapter_auto_revision(
        test_db,
        project_id=project_id,
        chapter_id=chapter.id,
        ai_model="deepseek-v4-pro",
        run_after=datetime.now(UTC).replace(tzinfo=None),
    )
    await test_db.commit()
    assert job is not None
    assert job.metadata_json["base_version_id"] == version.id

    model_calls = []
    fake_ai = FakeBookDraftAI(
        [
            json.dumps(
                {
                    "mode": "supplement",
                    "reason": "新增来源是新的离家经历，适合补入新段落。",
                    "pending_source_ids": [second["utterance_id"]],
                    "related_existing_source_ids": [],
                    "coverage_requirements": ["补入第一次离家去县城读书的经历"],
                },
                ensure_ascii=False,
            ),
            "我小时候常在外婆家的院子里晒太阳。后来我第一次离家去县城读书。",
        ]
    )

    def fake_get_ai_service(model=None):
        model_calls.append(model)
        return fake_ai

    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        fake_get_ai_service,
    )

    assert await run_auto_revision_job(test_db, job.id) is True

    refreshed_job = await test_db.scalar(
        select(MemoirBookAutoRevisionJob).where(MemoirBookAutoRevisionJob.id == job.id)
    )
    assert refreshed_job.status == MemoirBookAutoRevisionJobStatus.SUCCEEDED.value
    assert refreshed_job.attempts == 1
    assert refreshed_job.metadata_json["result_version_id"]
    refreshed_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == draft.id)
    )
    assert refreshed_draft.metadata_json == {}
    versions = (
        await test_db.execute(
            select(MemoirBookChapterDraftVersion)
            .where(MemoirBookChapterDraftVersion.draft_id == draft.id)
            .order_by(MemoirBookChapterDraftVersion.version)
        )
    ).scalars().all()
    assert len(versions) == 2
    assert "第一次离家去县城读书" in versions[-1].content
    refreshed_chapter = await test_db.scalar(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id)
    )
    assert refreshed_chapter.metadata_json == {}
    assert model_calls == ["deepseek-v4-pro"]
    event = await test_db.scalar(
        select(MemoirAutomationEvent).where(
            MemoirAutomationEvent.project_id == project_id,
            MemoirAutomationEvent.event_type == MemoirAutomationEventType.CHAPTER_AUTO_REVISED.value,
        )
    )
    assert event is not None
    assert event.chapter_id == chapter.id
    assert event.utterance_ids_json == [second["utterance_id"]]
    assert event.summary_text == "「外婆家的童年」已自动补写 1 条新增讲述。"
    assert event.metadata_json["source"] == "book_auto_revision"
    assert event.metadata_json["result_version_id"] == versions[-1].id


async def test_mobile_memoir_book_chapter_draft_planner_can_choose_regenerate(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来又想起外婆也常在那个院子里晒被子。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.source_refs_json = [memoir_source_ref(first), memoir_source_ref(second, "院子里晒被子")]
    chapter.metadata_json = {
        "pending_source_utterance_ids": [second["utterance_id"]],
        "source_assignment_updated_at": "2026-06-17T00:00:00+00:00",
    }
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
        metadata_json={"status_reason": "new_sources_assigned"},
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.AI.value,
            prompt_snapshot="old prompt",
        )
    )
    await test_db.commit()
    fake_ai = FakeBookDraftAI(
        [
            json.dumps(
                {
                    "mode": "regenerate",
                    "reason": "新增来源与旧正文都围绕同一个院子。",
                    "pending_source_ids": [second["utterance_id"]],
                    "related_existing_source_ids": [first["utterance_id"]],
                    "coverage_requirements": ["把院子晒太阳和晒被子写成同一段回忆"],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "clusters": [
                        {
                            "title": "院子里的日光",
                            "source_ids": [first["utterance_id"], second["utterance_id"]],
                            "summary": "两条来源都讲外婆家院子里的日光和晾晒。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            "小时候，外婆家的院子总是晒着太阳，也晒着被子。",
        ]
    )
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["latest_version"]["version"] == 2
    assert "晒着被子" in data["latest_version"]["content"]
    assert data["metadata_json"] == {}
    assert len(fake_ai.calls) == 3
    cluster_prompt = fake_ai.calls[1]["user_prompt"]
    assert "请把本章来源讲述记录中讲同一件事" in cluster_prompt
    prompt = fake_ai.calls[2]["user_prompt"]
    assert "mode: regenerate" in prompt
    assert "事件聚合规划" in prompt
    assert "院子里的日光" in prompt
    assert "原章节正文" not in prompt
    assert "本次任务：只把 source_type 为“新增来源”" not in prompt
    assert "把院子晒太阳和晒被子写成同一段回忆" in prompt


async def test_mobile_memoir_book_chapter_draft_planner_can_request_source_review(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "多年以后我在工厂学会了修机器。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.source_refs_json = [memoir_source_ref(first), memoir_source_ref(second, "工厂修机器")]
    chapter.metadata_json = {
        "pending_source_utterance_ids": [second["utterance_id"]],
        "source_assignment_updated_at": "2026-06-17T00:00:00+00:00",
    }
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
        metadata_json={"status_reason": "new_sources_assigned"},
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.AI.value,
            prompt_snapshot="old prompt",
        )
    )
    await test_db.commit()
    fake_ai = FakeBookDraftAI(
        json.dumps(
            {
                "mode": "needs_review",
                "reason": "新增来源讲工厂工作，和童年院子章节关系弱。",
                "pending_source_ids": [second["utterance_id"]],
                "related_existing_source_ids": [],
                "coverage_requirements": [],
            },
            ensure_ascii=False,
        )
    )
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "新增来源与本章关系不明确，请先调整章节归属"
    assert len(fake_ai.calls) == 1
    refreshed_chapter = await test_db.scalar(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id)
    )
    assert refreshed_chapter.metadata_json["pending_source_utterance_ids"] == [second["utterance_id"]]
    versions = (
        await test_db.execute(
            select(MemoirBookChapterDraftVersion).where(MemoirBookChapterDraftVersion.draft_id == draft.id)
        )
    ).scalars().all()
    assert len(versions) == 1


async def test_mobile_memoir_book_chapter_draft_force_regenerate_ignores_previous_pending_content(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "岁首一家人围坐吃团圆饭。")
    second = await create_test_voice_transcript(client, test_db, "初一早上挨家给长辈拜年。")
    third = await create_test_voice_transcript(client, test_db, "腊月里还会提前准备团圆饭要用的菜。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "团圆饭"),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.source_refs_json = [
        memoir_source_ref(first, "团圆饭"),
        memoir_source_ref(second, "拜年"),
        memoir_source_ref(third, "准备团圆饭要用的菜"),
    ]
    chapter.metadata_json = {
        "pending_source_utterance_ids": [second["utterance_id"]],
        "source_assignment_updated_at": "2026-06-17T00:00:00+00:00",
    }
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
        metadata_json={"status_reason": "new_sources_assigned"},
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="很早以前的一条旧记录，不应该进入重生成提示。",
            created_by=MemoirCreatedBy.AI.value,
            prompt_snapshot="old prompt",
        )
    )
    await test_db.commit()
    fake_ai = FakeBookDraftAI(
        [
            json.dumps(
                {
                    "clusters": [
                        {
                            "title": "岁首团圆饭",
                            "source_ids": [first["utterance_id"], third["utterance_id"]],
                            "summary": "一家人围坐吃团圆饭，也会提前准备饭菜。",
                        },
                        {
                            "title": "初一拜年",
                            "source_ids": [second["utterance_id"]],
                            "summary": "初一早上挨家给长辈拜年。",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            "岁首时一家人吃团圆饭，腊月里先备菜，初一早上去给长辈拜年。",
        ]
    )
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={"force_regenerate": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["latest_version"]["version"] == 2
    assert data["metadata_json"] == {}
    assert len(fake_ai.calls) == 2
    cluster_prompt = fake_ai.calls[0]["user_prompt"]
    assert "请把本章来源讲述记录中讲同一件事" in cluster_prompt
    prompt = fake_ai.calls[1]["user_prompt"]
    assert "很早以前的一条旧记录" not in prompt
    assert "原章节正文" not in prompt
    assert "本次任务：只把 source_type 为“新增来源”" not in prompt
    assert "事件聚合规划" in prompt
    assert "岁首团圆饭" in prompt
    assert "初一拜年" in prompt
    assert "岁首一家人围坐吃团圆饭" in prompt
    assert "初一早上挨家给长辈拜年" in prompt
    assert "腊月里还会提前准备团圆饭要用的菜" in prompt
    assert prompt.count("source_type: 既有来源") == 3
    assert "source_type: 新增来源" not in prompt
    assert "本章共有 3 条来源" in prompt
    assert "不能只写第一条" in prompt
    assert "以来源讲述记录为准" in prompt
    assert "章节标题参考" in prompt
    assert "章节范围参考" not in prompt
    assert "每条要求覆盖的来源至少要有一处可对应的具体内容进入正文" in prompt

    refreshed_chapter = await test_db.scalar(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id)
    )
    assert refreshed_chapter.metadata_json == {}


async def test_mobile_memoir_book_chapter_draft_update_clears_pending_source_state(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.source_refs_json = [memoir_source_ref(first), memoir_source_ref(second, "第一次离家")]
    chapter.metadata_json = {
        "pending_source_utterance_ids": [second["utterance_id"]],
        "source_assignment_updated_at": "2026-06-17T00:00:00+00:00",
    }
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
        metadata_json={"status_reason": "new_sources_assigned"},
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.AI.value,
            prompt_snapshot="old prompt",
        )
    )
    await test_db.commit()

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={"content": "我小时候常在外婆家的院子里晒太阳。后来我第一次离家去县城读书。"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["latest_version"]["version"] == 2
    assert data["latest_version"]["created_by"] == "user"
    assert data["metadata_json"] == {}
    assert data["pending_source_count"] == 0
    assert data["removed_source_count"] == 0
    refreshed_chapter = await test_db.scalar(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id)
    )
    assert refreshed_chapter.metadata_json == {}


async def test_mobile_memoir_book_chapter_draft_confirm_rejects_pending_sources(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.metadata_json = {"pending_source_utterance_ids": [created["utterance_id"]]}
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.AI.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/confirm"
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "请先补写新增来源"


async def test_mobile_memoir_book_chapter_draft_confirm_clears_removed_source_state(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    chapter.metadata_json = {
        "removed_source_utterance_ids": ["deleted-utterance"],
        "source_refs_removed_at": "2026-06-17T00:00:00+00:00",
    }
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.DRAFT.value,
        metadata_json={"status_reason": "sources_deleted", "removed_source_utterance_ids": ["deleted-utterance"]},
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/confirm"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    assert data["metadata_json"] == {}
    assert data["pending_source_count"] == 0
    assert data["removed_source_count"] == 0
    refreshed_chapter = await test_db.scalar(
        select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.id == chapter.id)
    )
    assert refreshed_chapter.metadata_json == {}


async def test_mobile_memoir_book_chapter_draft_update_and_confirm(client, test_db, monkeypatch):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: FakeBookDraftAI("AI 初稿"),
    )
    await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )

    update_response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={"content": "用户确认前改过的章节正文"},
    )
    confirm_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft/confirm"
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["status"] == "draft"
    assert updated["latest_version"]["version"] == 2
    assert updated["latest_version"]["content"] == "用户确认前改过的章节正文"
    assert updated["latest_version"]["created_by"] == "user"
    assert confirm_response.status_code == 200
    confirmed = confirm_response.json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["latest_version"]["content"] == "用户确认前改过的章节正文"


async def test_mobile_memoir_book_chapter_draft_post_returns_existing_without_force(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(created),
        status=MemoirBookPlanStatus.CONFIRMED,
    )
    fake_ai = FakeBookDraftAI("第一版正文")
    monkeypatch.setattr(
        "app.services.memoir.book_drafts.get_ai_service",
        lambda model=None: fake_ai,
    )

    first_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )
    second_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter.id}/draft",
        json={},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["latest_version"]["version"] == 1
    assert len(fake_ai.calls) == 1
