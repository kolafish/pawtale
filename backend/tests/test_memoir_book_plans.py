import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.memoir.narrative_types import (
    MemoirChapterContractStatus,
    MemoirNarrativeActorType,
)
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirBookImagePlacementType,
    MemoirBookChapterDraftStatus,
    MemoirChapterSourceAssignedBy,
    MemoirChapterSourceLinkStatus,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirImageAssetSourceType,
    MemoirSemanticCacheStatus,
)
from app.models.memoir import (
    MemoirAutomationEvent,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookImagePlacement,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChapterCard,
    MemoirChapterSourceLink,
    MemoirEditingJob,
    MemoirImageAsset,
)
from app.models.memoir_narrative import MemoirChapterContract
from app.services.memoir.book_plan_ai import ChapterTitleSourceDigest, build_chapter_title_prompt
from app.services.memoir.book_plan_sources import (
    find_chapter_by_id,
    renumber_chapters,
    set_temporary_chapter_orders,
    sorted_chapters,
    unique_source_ref_dicts,
)
from tests.memoir_helpers import (
    create_test_chapter_draft,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
)


class FakeBookPlanAI:
    provider_name = "fake-ai"
    model_name = "fake-book-plan"

    def __init__(self):
        self.calls = []

    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        return json.dumps(
            {
                "title": "外婆回忆录章节规划",
                "chapters": [
                    {
                        "title": "外婆家的童年",
                        "source_indices": [1],
                        "evidence_quote": "外婆家的院子",
                    },
                    {
                        "title": "离家求学",
                        "source_indices": [2],
                        "evidence_quote": "第一次离家去县城读书",
                    },
                ],
            },
            ensure_ascii=False,
        )


class FakeChapterTitleAI:
    provider_name = "fake-ai"
    model_name = "fake-title"

    def __init__(self):
        self.calls = []

    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        return json.dumps({"title": "外婆院中的日子"}, ensure_ascii=False)


async def _chapter_source_links(test_db, *, project_id: str) -> list[MemoirChapterSourceLink]:
    return (
        await test_db.execute(
            select(MemoirChapterSourceLink)
            .where(MemoirChapterSourceLink.project_id == project_id)
            .order_by(MemoirChapterSourceLink.chapter_id, MemoirChapterSourceLink.utterance_id)
        )
    ).scalars().all()


async def _create_source_held_event(test_db, *, project_id: str, utterance_id: str) -> MemoirAutomationEvent:
    event = MemoirAutomationEvent(
        project_id=project_id,
        review_utterance_id=utterance_id,
        event_type=MemoirAutomationEventType.SOURCE_HELD.value,
        status=MemoirAutomationEventStatus.NEEDS_REVIEW.value,
        utterance_ids_json=[],
        summary_text="1 条讲述暂未自动收入章节，需要确认归属。",
        metadata_json={
            "route": {
                "utterance_id": utterance_id,
                "action": "suggest_new_chapter",
                "suggested_title": "待整理讲述",
            }
        },
    )
    test_db.add(event)
    await test_db.commit()
    return event


async def _assert_source_held_event_resolved(test_db, event_id: str, *, chapter_title: str) -> None:
    event = await test_db.get(MemoirAutomationEvent, event_id)
    assert event.status == MemoirAutomationEventStatus.COMPLETED.value
    assert event.metadata_json["resolution"] == "confirmed"
    assert event.metadata_json["resolution_reason"] == "source_assigned"
    assert event.metadata_json["resolved_chapter_title"] == chapter_title


def test_chapter_title_prompt_uses_all_source_digests_without_current_title():
    sources = [
        ChapterTitleSourceDigest(
            source_number=index,
            utterance_id=f"utterance-{index}",
            created_at=f"2026-06-{index:02d}T00:00:00+00:00",
            summary=f"第{index}条来源讲到过年时家里准备饭菜和亲戚团聚。",
            representative_quote=f"第{index}条原话",
        )
        for index in range(1, 10)
    ]

    prompt = build_chapter_title_prompt(sources)

    assert "当前章节名" not in prompt
    assert "旧章节标题" not in prompt
    assert "record_summary:" in prompt
    assert "representative_quote:" in prompt
    assert "第9条来源" in prompt
    assert '"title": "章节标题"' in prompt


def test_book_plan_source_helpers_cover_chapter_order_and_source_refs():
    plan = MemoirBookPlan(
        id="plan-1",
        project_id="project-1",
        title="爷爷主题",
        status="confirmed",
        created_by=MemoirCreatedBy.USER.value,
    )
    first = MemoirBookPlanChapter(
        id="chapter-1",
        plan_id=plan.id,
        project_id=plan.project_id,
        title="第一章",
        sort_order=2,
    )
    second = MemoirBookPlanChapter(
        id="chapter-2",
        plan_id=plan.id,
        project_id=plan.project_id,
        title="第二章",
        sort_order=1,
    )
    plan.chapters = [first, second]

    assert [chapter.id for chapter in sorted_chapters(plan)] == ["chapter-2", "chapter-1"]
    assert find_chapter_by_id(plan.chapters, "chapter-1") is first
    assert find_chapter_by_id(plan.chapters, None) is None
    assert unique_source_ref_dicts([
        {"utterance_id": "u1", "quote": "一"},
        {"utterance_id": "u1", "quote": "重复"},
        {"utterance_id": "u2", "quote": "二"},
        {"missing": "ignored"},
    ]) == [
        {"utterance_id": "u1", "quote": "一"},
        {"utterance_id": "u2", "quote": "二"},
    ]

    set_temporary_chapter_orders([first, second])
    assert [first.sort_order, second.sort_order] == [10000, 10001]
    renumber_chapters([second, first])
    assert [second.sort_order, first.sort_order] == [0, 1]


async def test_mobile_memoir_book_plan_routes_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/projects/project-1/book-plan/generate",
        json={},
    )

    assert response.status_code == 401


async def test_mobile_memoir_book_source_coverage_requires_login(anonymous_client):
    response = await anonymous_client.get(
        "/api/mobile/memoir/projects/project-1/book-source-coverage",
    )

    assert response.status_code == 401


async def test_mobile_memoir_book_source_assignments_require_login(anonymous_client):
    response = await anonymous_client.post(
        "/api/mobile/memoir/projects/project-1/book-source-assignments",
        json={"assignments": [{"chapter_id": "chapter-1", "utterance_ids": ["utterance-1"]}]},
    )

    assert response.status_code == 401


async def test_mobile_memoir_book_plan_requires_transcripts(client):
    project_id = await ensure_mobile_memoir_project(client)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "暂无讲述内容，无法规划章节"


async def test_mobile_memoir_book_plan_get_returns_404_before_plan_exists(client):
    project_id = await ensure_mobile_memoir_project(client)

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-plan")

    assert response.status_code == 404
    assert response.json()["detail"] == "章节规划不存在"


async def test_mobile_memoir_book_source_coverage_lists_all_sources_before_plan(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")

    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == project_id
    assert data["plan_id"] is None
    assert data["total_source_count"] == 1
    assert data["covered_source_count"] == 0
    assert data["uncovered_source_count"] == 1
    assert data["sources"][0]["utterance_id"] == created["utterance_id"]
    assert data["sources"][0]["is_included"] is False
    assert data["sources"][0]["included_in_chapters"] == []
    assert data["uncovered_sources"][0]["utterance_id"] == created["utterance_id"]
    assert data["uncovered_sources"][0]["text"] == "我小时候常在外婆家的院子里晒太阳。"


async def test_mobile_memoir_book_plan_generate_saves_source_bound_chapters(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来我第一次离家去县城读书，心里很紧张。")
    project_id = first["session"]["project_id"]
    held_event = await _create_source_held_event(
        test_db,
        project_id=project_id,
        utterance_id=first["utterance_id"],
    )
    fake_ai = FakeBookPlanAI()
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={"ai_model": "deepseek", "max_chapters": 6},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert data["created_by"] == "ai"
    assert data["model"] == "fake-book-plan"
    assert data["metadata_json"]["source_utterance_count"] == 2
    assert [chapter["title"] for chapter in data["chapters"]] == [
        "外婆家的童年",
        "离家求学",
    ]
    assert data["chapters"][0]["source_refs"][0]["utterance_id"] == first["utterance_id"]
    assert data["chapters"][0]["source_refs"][0]["message_id"] == first["message_id"]
    assert data["chapters"][0]["source_refs"][0]["created_at"]
    assert "summary" not in data["chapters"][0]
    assert data["chapters"][1]["source_refs"][0]["utterance_id"] == second["utterance_id"]
    assert "不新增人物、时间、地点、事件或情绪" in fake_ai.calls[0]["user_prompt"]
    assert '"summary"' not in fake_ai.calls[0]["user_prompt"]
    assert f"utterance_id: {first['utterance_id']}" in fake_ai.calls[0]["user_prompt"]
    refreshed_held_event = await test_db.get(MemoirAutomationEvent, held_event.id)
    assert refreshed_held_event.status == MemoirAutomationEventStatus.NEEDS_REVIEW.value

    stored_plan = await test_db.scalar(select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id))
    stored_chapters = (
        await test_db.execute(
            select(MemoirBookPlanChapter)
            .where(MemoirBookPlanChapter.plan_id == stored_plan.id)
            .order_by(MemoirBookPlanChapter.sort_order)
        )
    ).scalars().all()
    assert stored_plan.status == "draft"
    assert len(stored_chapters) == 2
    links_by_utterance = {
        link.utterance_id: link
        for link in await _chapter_source_links(test_db, project_id=project_id)
    }
    assert links_by_utterance[first["utterance_id"]].chapter_id == stored_chapters[0].id
    assert links_by_utterance[first["utterance_id"]].quote == "外婆家的院子"
    assert links_by_utterance[second["utterance_id"]].chapter_id == stored_chapters[1].id

    get_response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-plan")
    assert get_response.status_code == 200
    assert get_response.json()["chapters"][0]["source_refs"][0]["created_at"]


async def test_mobile_memoir_book_plan_confirm_replaces_plan_with_user_chapters(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]
    held_event = await _create_source_held_event(
        test_db,
        project_id=project_id,
        utterance_id=created["utterance_id"],
    )

    response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "source_refs": [source_ref],
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "用户确认后的章节"
    assert data["status"] == "confirmed"
    assert data["created_by"] == "user"
    assert data["model"] is None
    assert len(data["chapters"]) == 1
    assert data["chapters"][0]["title"] == "童年和外婆家"
    assert data["chapters"][0]["source_refs"][0]["utterance_id"] == created["utterance_id"]
    await _assert_source_held_event_resolved(test_db, held_event.id, chapter_title="童年和外婆家")

    stored_chapters = (
        await test_db.execute(
            select(MemoirBookPlanChapter).where(MemoirBookPlanChapter.project_id == project_id)
        )
    ).scalars().all()
    assert len(stored_chapters) == 1

    legacy_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "旧版摘要请求",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "summary": "旧字段不再接受",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    assert legacy_response.status_code == 422


async def test_mobile_memoir_book_plan_confirmation_preserves_hidden_chapters(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    await create_test_voice_transcript(client, test_db, "后来我第一次离家去县城读书，心里很紧张。")
    project_id = first["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    assert generated.status_code == 200
    generated_chapters = generated.json()["chapters"]

    hidden = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "set_chapter_book_inclusion",
            "chapter_id": generated_chapters[1]["id"],
            "include_in_book": False,
        },
    )
    assert hidden.status_code == 200

    confirmed = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": hidden.json()["title"],
            "chapters": [
                {
                    "title": chapter["title"],
                    "include_in_book": chapter["include_in_book"],
                    "source_refs": chapter["source_refs"],
                }
                for chapter in hidden.json()["chapters"]
            ],
        },
    )

    assert confirmed.status_code == 200, confirmed.text
    assert [chapter["include_in_book"] for chapter in confirmed.json()["chapters"]] == [True, False]


async def test_mobile_memoir_book_source_coverage_detects_new_transcripts_after_plan(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    assert confirm_response.status_code == 200
    later = await create_test_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")

    response = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")

    assert response.status_code == 200
    data = response.json()
    assert data["plan_id"] == confirm_response.json()["id"]
    assert data["total_source_count"] == 2
    assert data["covered_source_count"] == 1
    assert data["uncovered_source_count"] == 1
    source_status_by_utterance = {item["utterance_id"]: item for item in data["sources"]}
    assert source_status_by_utterance[created["utterance_id"]]["is_included"] is True
    assert source_status_by_utterance[created["utterance_id"]]["included_in_chapters"][0]["title"] == "童年和外婆家"
    assert source_status_by_utterance[later["utterance_id"]]["is_included"] is False
    assert [item["utterance_id"] for item in data["uncovered_sources"]] == [later["utterance_id"]]
    assert data["uncovered_sources"][0]["text"] == "后来我又讲了第一次离家去县城读书。"


async def test_mobile_memoir_book_source_assignment_appends_source_and_marks_draft_pending(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    assert confirm_response.status_code == 200
    chapter_id = confirm_response.json()["chapters"][0]["id"]
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add(draft)
    await test_db.flush()
    version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter_id,
        version=1,
        content="我小时候常在外婆家的院子里晒太阳。",
        created_by=MemoirCreatedBy.USER.value,
        prompt_snapshot="",
    )
    test_db.add(version)
    await test_db.flush()
    links_by_utterance = {
        link.utterance_id: link
        for link in await _chapter_source_links(test_db, project_id=project_id)
    }
    links_by_utterance[created["utterance_id"]].merged_draft_version_id = version.id
    test_db.add(links_by_utterance[created["utterance_id"]])
    await test_db.commit()
    later = await create_test_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")
    held_event = await _create_source_held_event(
        test_db,
        project_id=project_id,
        utterance_id=later["utterance_id"],
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-assignments",
        json={
            "assignments": [
                {
                    "chapter_id": chapter_id,
                    "utterance_ids": [later["utterance_id"]],
                }
            ],
            "ai_model": "deepseek-v4-pro",
        },
    )

    assert response.status_code == 200
    data = response.json()
    refs = data["chapters"][0]["source_refs"]
    assert [ref["utterance_id"] for ref in refs] == [created["utterance_id"], later["utterance_id"]]
    assert later["utterance_id"] in data["chapters"][0]["metadata_json"]["pending_source_utterance_ids"]
    assert data["chapters"][0]["pending_source_count"] == 1
    assert data["chapters"][0]["removed_source_count"] == 0
    links = await _chapter_source_links(test_db, project_id=project_id)
    links_by_utterance = {link.utterance_id: link for link in links}
    assert set(links_by_utterance) == {created["utterance_id"], later["utterance_id"]}
    assert links_by_utterance[later["utterance_id"]].chapter_id == chapter_id
    assert links_by_utterance[later["utterance_id"]].status == MemoirChapterSourceLinkStatus.ACTIVE.value
    assert links_by_utterance[later["utterance_id"]].assigned_by == MemoirChapterSourceAssignedBy.USER.value
    assert links_by_utterance[later["utterance_id"]].assignment_reason == "manual_source_assignment"

    refreshed_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == draft.id)
    )
    assert refreshed_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_draft.metadata_json["status_reason"] == "new_sources_assigned"
    await _assert_source_held_event_resolved(test_db, held_event.id, chapter_title="童年和外婆家")

    coverage = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage.status_code == 200
    assert coverage.json()["uncovered_source_count"] == 0


async def test_mobile_memoir_book_source_assignment_rejects_covered_source(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    chapter_id = confirm_response.json()["chapters"][0]["id"]

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-assignments",
        json={
            "assignments": [
                {
                    "chapter_id": chapter_id,
                    "utterance_ids": [created["utterance_id"]],
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "讲述记录已收录到章节"


async def test_mobile_memoir_book_source_move_updates_only_source_and_target_chapters(
    client,
    test_db,
):
    first = await create_test_voice_transcript(client, test_db, "第一条年节记忆。")
    second = await create_test_voice_transcript(client, test_db, "第二条年节记忆。")
    third = await create_test_voice_transcript(client, test_db, "童年友情里的伙伴。")
    project_id = first["session"]["project_id"]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "岁首欢聚记",
                    "source_refs": [memoir_source_ref(first), memoir_source_ref(second)],
                },
                {
                    "title": "童年友情",
                    "source_refs": [memoir_source_ref(third)],
                },
            ],
        },
    )
    assert confirm_response.status_code == 200
    chapters = confirm_response.json()["chapters"]
    source_chapter_id = chapters[0]["id"]
    target_chapter_id = chapters[1]["id"]
    source_draft = await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=source_chapter_id,
    )
    target_draft = await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=target_chapter_id,
    )
    held_event = await _create_source_held_event(
        test_db,
        project_id=project_id,
        utterance_id=second["utterance_id"],
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-moves",
        json={
            "source_chapter_id": source_chapter_id,
            "target_chapter_id": target_chapter_id,
            "utterance_id": second["utterance_id"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    chapters_by_id = {chapter["id"]: chapter for chapter in data["chapters"]}
    assert [
        ref["utterance_id"]
        for ref in chapters_by_id[source_chapter_id]["source_refs"]
    ] == [first["utterance_id"]]
    assert [
        ref["utterance_id"]
        for ref in chapters_by_id[target_chapter_id]["source_refs"]
    ] == [third["utterance_id"], second["utterance_id"]]
    assert chapters_by_id[source_chapter_id]["metadata_json"]["removed_source_utterance_ids"] == [
        second["utterance_id"]
    ]
    assert chapters_by_id[source_chapter_id]["pending_source_count"] == 0
    assert chapters_by_id[source_chapter_id]["removed_source_count"] == 1
    assert chapters_by_id[target_chapter_id]["metadata_json"]["pending_source_utterance_ids"] == [
        second["utterance_id"]
    ]
    assert chapters_by_id[target_chapter_id]["pending_source_count"] == 1
    assert chapters_by_id[target_chapter_id]["removed_source_count"] == 0
    moved_links = [
        link
        for link in await _chapter_source_links(test_db, project_id=project_id)
        if link.utterance_id == second["utterance_id"]
    ]
    links_by_chapter = {link.chapter_id: link for link in moved_links}
    assert links_by_chapter[source_chapter_id].status == MemoirChapterSourceLinkStatus.REMOVED.value
    assert links_by_chapter[source_chapter_id].removed_at is not None
    assert links_by_chapter[target_chapter_id].status == MemoirChapterSourceLinkStatus.ACTIVE.value
    assert links_by_chapter[target_chapter_id].assigned_by == MemoirChapterSourceAssignedBy.USER.value
    assert links_by_chapter[target_chapter_id].assignment_reason == "manual_source_move"

    refreshed_source_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == source_draft.id)
    )
    refreshed_target_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == target_draft.id)
    )
    assert refreshed_source_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_source_draft.metadata_json["status_reason"] == "sources_changed"
    assert refreshed_target_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_target_draft.metadata_json["status_reason"] == "sources_changed"
    await _assert_source_held_event_resolved(test_db, held_event.id, chapter_title="童年友情")


async def test_mobile_memoir_book_source_move_rejects_last_source_in_chapter(
    client,
    test_db,
):
    first = await create_test_voice_transcript(client, test_db, "第一条年节记忆。")
    second = await create_test_voice_transcript(client, test_db, "童年友情里的伙伴。")
    project_id = first["session"]["project_id"]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "岁首欢聚记",
                    "source_refs": [memoir_source_ref(first)],
                },
                {
                    "title": "童年友情",
                    "source_refs": [memoir_source_ref(second)],
                },
            ],
        },
    )
    assert confirm_response.status_code == 200
    chapters = confirm_response.json()["chapters"]

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-source-moves",
        json={
            "source_chapter_id": chapters[0]["id"],
            "target_chapter_id": chapters[1]["id"],
            "utterance_id": first["utterance_id"],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "原章节至少需要保留一条来源"


async def test_mobile_memoir_book_plan_structure_command_moves_and_renames_chapter(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "小时候在院子里玩。")
    second = await create_test_voice_transcript(client, test_db, "后来第一次离家求学。")
    third = await create_test_voice_transcript(client, test_db, "工作以后认识了很多朋友。")
    project_id = first["session"]["project_id"]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {"title": "童年院子", "source_refs": [memoir_source_ref(first)]},
                {"title": "离家求学", "source_refs": [memoir_source_ref(second)]},
                {"title": "一生的朋友", "source_refs": [memoir_source_ref(third)]},
            ],
        },
    )
    assert confirm_response.status_code == 200
    first_chapter, second_chapter, third_chapter = confirm_response.json()["chapters"]

    move_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={"type": "move_chapter", "chapter_id": second_chapter["id"], "direction": "up"},
    )

    assert move_response.status_code == 200, move_response.json()
    assert [chapter["id"] for chapter in move_response.json()["chapters"]] == [
        second_chapter["id"],
        first_chapter["id"],
        third_chapter["id"],
    ]
    assert move_response.json()["metadata_json"]["structure_operation"] == "move_chapter"

    move_to_last_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={"type": "move_chapter", "chapter_id": second_chapter["id"], "target_position": "last"},
    )

    assert move_to_last_response.status_code == 200, move_to_last_response.json()
    assert [chapter["id"] for chapter in move_to_last_response.json()["chapters"]] == [
        first_chapter["id"],
        third_chapter["id"],
        second_chapter["id"],
    ]

    rename_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={"type": "rename_chapter", "chapter_id": first_chapter["id"], "title": "院子里的童年"},
    )

    assert rename_response.status_code == 200, rename_response.json()
    chapters_by_id = {chapter["id"]: chapter for chapter in rename_response.json()["chapters"]}
    assert chapters_by_id[first_chapter["id"]]["title"] == "院子里的童年"


async def test_mobile_memoir_book_plan_structure_command_merges_chapters_and_marks_target_draft(
    client,
    test_db,
):
    first = await create_test_voice_transcript(client, test_db, "第一条年节记忆。")
    second = await create_test_voice_transcript(client, test_db, "第二条年节记忆。")
    project_id = first["session"]["project_id"]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {"title": "岁首欢聚记", "source_refs": [memoir_source_ref(first)]},
                {"title": "童年友情", "source_refs": [memoir_source_ref(second)]},
            ],
        },
    )
    assert confirm_response.status_code == 200
    target_chapter, source_chapter = confirm_response.json()["chapters"]
    target_draft = await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=target_chapter["id"],
    )
    source_draft = await create_test_chapter_draft(
        test_db,
        project_id=project_id,
        chapter_id=source_chapter["id"],
    )
    hidden_target = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "set_chapter_book_inclusion",
            "chapter_id": target_chapter["id"],
            "include_in_book": False,
        },
    )
    assert hidden_target.status_code == 200, hidden_target.text

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "merge_chapters",
            "chapter_id": source_chapter["id"],
            "target_chapter_id": target_chapter["id"],
        },
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert [chapter["id"] for chapter in data["chapters"]] == [target_chapter["id"]]
    assert data["chapters"][0]["include_in_book"] is True
    assert [ref["utterance_id"] for ref in data["chapters"][0]["source_refs"]] == [
        first["utterance_id"],
        second["utterance_id"],
    ]
    assert data["chapters"][0]["metadata_json"]["pending_source_utterance_ids"] == [second["utterance_id"]]
    assert data["chapters"][0]["pending_source_count"] == 1
    assert data["chapters"][0]["removed_source_count"] == 0
    refreshed_target_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == target_draft.id)
    )
    assert refreshed_target_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_target_draft.metadata_json["status_reason"] == "sources_changed"
    assert await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == source_draft.id)
    ) is None


async def test_mobile_memoir_book_plan_structure_command_splits_chapter_sources(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "第一条年节记忆。")
    second = await create_test_voice_transcript(client, test_db, "第二条年节记忆。")
    project_id = first["session"]["project_id"]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {
                    "title": "岁首欢聚记",
                    "source_refs": [memoir_source_ref(first), memoir_source_ref(second)],
                },
            ],
        },
    )
    assert confirm_response.status_code == 200
    chapter = confirm_response.json()["chapters"][0]
    draft = await create_test_chapter_draft(test_db, project_id=project_id, chapter_id=chapter["id"])

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "split_chapter",
            "chapter_id": chapter["id"],
            "title": "年节旧事（拆出）",
            "utterance_ids": [second["utterance_id"]],
        },
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert [chapter["title"] for chapter in data["chapters"]] == ["岁首欢聚记", "年节旧事（拆出）"]
    assert [ref["utterance_id"] for ref in data["chapters"][0]["source_refs"]] == [first["utterance_id"]]
    assert [ref["utterance_id"] for ref in data["chapters"][1]["source_refs"]] == [second["utterance_id"]]
    refreshed_draft = await test_db.scalar(select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == draft.id))
    assert refreshed_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_draft.metadata_json["status_reason"] == "sources_changed"


async def test_mobile_memoir_book_plan_structure_command_creates_and_deletes_source_chapter(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    later = await create_test_voice_transcript(client, test_db, "后来我又讲了一个认识很多年的朋友。")
    project_id = first["session"]["project_id"]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={"chapters": [{"title": "童年和外婆家", "source_refs": [memoir_source_ref(first)]}]},
    )
    assert confirm_response.status_code == 200
    held_event = await _create_source_held_event(
        test_db,
        project_id=project_id,
        utterance_id=later["utterance_id"],
    )

    create_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={"type": "create_chapter_from_source", "utterance_id": later["utterance_id"]},
    )

    assert create_response.status_code == 200, create_response.json()
    created_chapter = create_response.json()["chapters"][1]
    assert created_chapter["title"] == "后来我又讲了一个认识很多年的朋友"
    assert created_chapter["source_refs"][0]["utterance_id"] == later["utterance_id"]
    plan = await test_db.scalar(
        select(MemoirBookPlan).where(MemoirBookPlan.project_id == project_id)
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    editing_job = MemoirEditingJob(
        project_id=project_id,
        chapter_id=created_chapter["id"],
        job_type=MemoirEditingJobType.GENERATE_DRAFT_BLOCKS.value,
        status=MemoirEditingJobStatus.RUNNING.value,
        run_after=now,
        locked_at=now,
        lease_token="lease-before-chapter-delete",
        lease_expires_at=now + timedelta(minutes=5),
    )
    chapter_card = MemoirChapterCard(
        project_id=project_id,
        plan_id=plan.id,
        chapter_id=created_chapter["id"],
        input_signature="deleted-chapter-card",
        engine="rules_v1",
        status=MemoirSemanticCacheStatus.READY.value,
    )
    image_asset = MemoirImageAsset(
        project_id=project_id,
        user_id=first["user_id"],
        source_type=MemoirImageAssetSourceType.CHAPTER_ILLUSTRATION.value,
        source_signature="deleted-chapter-image",
        source_chapter_id=created_chapter["id"],
    )
    test_db.add_all([editing_job, chapter_card, image_asset])
    await test_db.flush()
    image_placement = MemoirBookImagePlacement(
        project_id=project_id,
        image_asset_id=image_asset.id,
        placement_type=MemoirBookImagePlacementType.CHAPTER_OPENING.value,
        chapter_id=created_chapter["id"],
    )
    test_db.add(image_placement)
    await test_db.commit()
    await _assert_source_held_event_resolved(
        test_db,
        held_event.id,
        chapter_title="后来我又讲了一个认识很多年的朋友",
    )

    delete_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={"type": "delete_chapter", "chapter_id": created_chapter["id"]},
    )

    assert delete_response.status_code == 200, delete_response.json()
    assert [chapter["title"] for chapter in delete_response.json()["chapters"]] == ["童年和外婆家"]
    await test_db.refresh(held_event)
    assert held_event.chapter_id is None
    deleted_chapter = held_event.metadata_json["deleted_chapter"]
    assert deleted_chapter["id"] == created_chapter["id"]
    assert deleted_chapter["title"] == "后来我又讲了一个认识很多年的朋友"
    assert deleted_chapter["detached_at"]
    await test_db.refresh(editing_job)
    assert editing_job.chapter_id is None
    assert editing_job.status == MemoirEditingJobStatus.SKIPPED.value
    assert editing_job.locked_at is None
    assert editing_job.lease_token is None
    assert editing_job.lease_expires_at is None
    assert editing_job.error_message == "章节已删除，任务已跳过"
    assert await test_db.get(MemoirChapterCard, chapter_card.id) is None
    assert await test_db.get(MemoirBookImagePlacement, image_placement.id) is None
    await test_db.refresh(image_asset)
    assert image_asset.source_chapter_id is None
    coverage = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage.status_code == 200
    assert [item["utterance_id"] for item in coverage.json()["uncovered_sources"]] == [later["utterance_id"]]


async def test_mobile_memoir_book_plan_can_hide_and_restore_structured_chapter(
    client,
    test_db,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来我第一次离家去县城读书。")
    project_id = first["session"]["project_id"]
    confirmed = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {"title": "外婆家的童年", "source_refs": [memoir_source_ref(first)]},
                {"title": "离家求学", "source_refs": [memoir_source_ref(second)]},
            ]
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    first_chapter, second_chapter = confirmed.json()["chapters"]
    assert first_chapter["include_in_book"] is True
    assert second_chapter["include_in_book"] is True

    contract = MemoirChapterContract(
        project_id=project_id,
        chapter_id=second_chapter["id"],
        version=1,
        objective="讲清第一次离家的经过。",
        reader_takeaway="理解这次经历带来的变化。",
        status=MemoirChapterContractStatus.DRAFT.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(contract)
    await test_db.commit()

    hidden = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "set_chapter_book_inclusion",
            "chapter_id": second_chapter["id"],
            "include_in_book": False,
        },
    )
    assert hidden.status_code == 200, hidden.text
    hidden_chapters = hidden.json()["chapters"]
    assert len(hidden_chapters) == 2
    assert hidden_chapters[1]["include_in_book"] is False
    assert hidden_chapters[1]["source_refs"][0]["utterance_id"] == second["utterance_id"]
    assert await test_db.get(MemoirChapterContract, contract.id) is not None

    delete_last_included = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={"type": "delete_chapter", "chapter_id": first_chapter["id"]},
    )
    assert delete_last_included.status_code == 400
    assert delete_last_included.json()["detail"] == "成书时至少需要保留一个章节"

    replace_last_included = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {
                    "id": second_chapter["id"],
                    "title": second_chapter["title"],
                    "include_in_book": False,
                    "source_refs": [memoir_source_ref(second)],
                }
            ]
        },
    )
    assert replace_last_included.status_code == 400
    assert replace_last_included.json()["detail"] == "成书时至少需要保留一个章节"

    last_included = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "set_chapter_book_inclusion",
            "chapter_id": first_chapter["id"],
            "include_in_book": False,
        },
    )
    assert last_included.status_code == 400
    assert last_included.json()["detail"] == "成书时至少需要保留一个章节"

    restored = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={
            "type": "set_chapter_book_inclusion",
            "chapter_id": second_chapter["id"],
            "include_in_book": True,
        },
    )
    assert restored.status_code == 200, restored.text
    assert all(item["include_in_book"] for item in restored.json()["chapters"])


async def test_book_plan_destructive_changes_reject_chapters_with_narrative_contract(
    client,
    test_db,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来我第一次离家去县城读书。")
    project_id = first["session"]["project_id"]
    confirmed = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {"title": "外婆家的童年", "source_refs": [memoir_source_ref(first)]},
                {"title": "离家求学", "source_refs": [memoir_source_ref(second)]},
            ]
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    first_chapter, protected_chapter = confirmed.json()["chapters"]
    contract = MemoirChapterContract(
        project_id=project_id,
        chapter_id=protected_chapter["id"],
        version=1,
        objective="讲清第一次离家求学的经过。",
        reader_takeaway="理解这次离家如何改变了讲述者。",
        status=MemoirChapterContractStatus.DRAFT.value,
        created_by=MemoirNarrativeActorType.SYSTEM.value,
    )
    test_db.add(contract)
    await test_db.commit()

    responses = [
        await client.post(
            f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
            json={"type": "delete_chapter", "chapter_id": protected_chapter["id"]},
        ),
        await client.post(
            f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
            json={
                "type": "merge_chapters",
                "chapter_id": protected_chapter["id"],
                "target_chapter_id": first_chapter["id"],
            },
        ),
        await client.patch(
            f"/api/mobile/memoir/projects/{project_id}/book-plan",
            json={
                "chapters": [
                    {
                        "id": first_chapter["id"],
                        "title": first_chapter["title"],
                        "source_refs": [memoir_source_ref(first)],
                    }
                ]
            },
        ),
        await client.put(
            f"/api/mobile/memoir/projects/{project_id}/book-plan",
            json={
                "chapters": [
                    {"title": "重新规划", "source_refs": [memoir_source_ref(first)]}
                ]
            },
        ),
    ]

    for response in responses:
        assert response.status_code == 409, response.text
        assert "已进入结构化写作" in response.json()["detail"]
    assert await test_db.get(MemoirChapterContract, contract.id) is not None
    assert await test_db.get(MemoirBookPlanChapter, protected_chapter["id"]) is not None


async def test_mobile_memoir_book_plan_reconfirm_removes_replaced_chapter_drafts(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]
    first_confirm = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "第一版章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    assert first_confirm.status_code == 200
    replaced_chapter_id = first_confirm.json()["chapters"][0]["id"]
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=replaced_chapter_id,
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=replaced_chapter_id,
            version=1,
            content="旧章节正文",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()

    second_confirm = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "第二版章节",
            "chapters": [
                {
                    "title": "重新整理童年",
                    "source_refs": [source_ref],
                }
            ],
        },
    )

    assert second_confirm.status_code == 200
    assert second_confirm.json()["chapters"][0]["id"] != replaced_chapter_id
    assert await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.chapter_id == replaced_chapter_id)
    ) is None
    assert await test_db.scalar(
        select(MemoirBookChapterDraftVersion).where(
            MemoirBookChapterDraftVersion.chapter_id == replaced_chapter_id
        )
    ) is None


async def test_mobile_memoir_book_plan_patch_deletes_chapter_without_replacing_remaining_draft(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来我第一次离家去县城读书，心里很紧张。")
    project_id = first["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    generated = generated_response.json()
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "source_refs": [generated["chapters"][0]["source_refs"][0]],
                },
                {
                    "title": "离家求学",
                    "source_refs": [generated["chapters"][1]["source_refs"][0]],
                },
            ],
        },
    )
    assert confirm_response.status_code == 200
    first_chapter, second_chapter = confirm_response.json()["chapters"]
    first_draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=first_chapter["id"],
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    second_draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=second_chapter["id"],
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add_all([first_draft, second_draft])
    await test_db.flush()
    test_db.add_all([
        MemoirBookChapterDraftVersion(
            draft_id=first_draft.id,
            project_id=project_id,
            chapter_id=first_chapter["id"],
            version=1,
            content="童年正文",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        ),
        MemoirBookChapterDraftVersion(
            draft_id=second_draft.id,
            project_id=project_id,
            chapter_id=second_chapter["id"],
            version=1,
            content="离家正文",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        ),
    ])
    await test_db.commit()

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "调整后的章节",
            "chapters": [
                {
                    "id": first_chapter["id"],
                    "title": first_chapter["title"],
                    "source_refs": first_chapter["source_refs"],
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "调整后的章节"
    assert [chapter["id"] for chapter in data["chapters"]] == [first_chapter["id"]]
    assert await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == first_draft.id)
    ) is not None
    assert await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == second_draft.id)
    ) is None

    coverage = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage.status_code == 200
    assert [item["utterance_id"] for item in coverage.json()["uncovered_sources"]] == [second["utterance_id"]]


async def test_mobile_memoir_book_plan_patch_adds_chapter_from_uncovered_source(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = first["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "用户确认后的章节",
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    assert confirm_response.status_code == 200
    existing_chapter = confirm_response.json()["chapters"][0]
    later = await create_test_voice_transcript(client, test_db, "后来我又讲了一个认识很多年的朋友。")
    held_event = await _create_source_held_event(
        test_db,
        project_id=project_id,
        utterance_id=later["utterance_id"],
    )

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {
                    "id": existing_chapter["id"],
                    "title": existing_chapter["title"],
                    "source_refs": existing_chapter["source_refs"],
                },
                {
                    "title": "朋友往事",
                    "source_refs": [memoir_source_ref(later, "认识很多年的朋友")],
                },
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert [chapter["title"] for chapter in data["chapters"]] == ["童年和外婆家", "朋友往事"]
    assert data["chapters"][0]["id"] == existing_chapter["id"]
    assert data["chapters"][1]["id"] != existing_chapter["id"]
    assert data["chapters"][1]["source_refs"][0]["utterance_id"] == later["utterance_id"]
    await _assert_source_held_event_resolved(test_db, held_event.id, chapter_title="朋友往事")

    coverage = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage.status_code == 200
    assert coverage.json()["uncovered_source_count"] == 0


async def test_mobile_memoir_book_plan_patch_updates_draft_plan_structure(
    client,
    test_db,
    monkeypatch,
):
    first = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, "后来我第一次离家去县城读书，心里很紧张。")
    project_id = first["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    assert generated_response.status_code == 200
    generated = generated_response.json()
    assert generated["status"] == "draft"
    first_chapter, second_chapter = generated["chapters"]

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "title": "草稿目录调整",
            "chapters": [
                {
                    "id": first_chapter["id"],
                    "title": "童年和离家",
                    "source_refs": [
                        first_chapter["source_refs"][0],
                        second_chapter["source_refs"][0],
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == "draft"
    assert data["title"] == "草稿目录调整"
    assert [chapter["title"] for chapter in data["chapters"]] == ["童年和离家"]
    assert [ref["utterance_id"] for ref in data["chapters"][0]["source_refs"]] == [
        first["utterance_id"],
        second["utterance_id"],
    ]

    coverage = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage.status_code == 200
    assert coverage.json()["uncovered_source_count"] == 0


async def test_mobile_memoir_book_chapter_title_update_renames_confirmed_chapter(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {
                    "title": "童年和外婆家",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    chapter_id = confirm_response.json()["chapters"][0]["id"]

    response = await client.patch(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter_id}/title",
        json={"title": "院子里的童年"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapters"][0]["title"] == "院子里的童年"
    assert data["chapters"][0]["source_refs"][0]["utterance_id"] == created["utterance_id"]
    assert data["chapters"][0]["metadata_json"]["title_updated_by"] == "user"


async def test_mobile_memoir_book_chapter_title_generate_uses_chapter_sources(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {
                    "title": "新收录转写",
                    "source_refs": [source_ref],
                }
            ],
        },
    )
    chapter_id = confirm_response.json()["chapters"][0]["id"]
    fake_ai = FakeChapterTitleAI()
    monkeypatch.setattr(
        "app.services.memoir.book_plan_ai.get_ai_service",
        lambda model=None: fake_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter_id}/title/generate",
        json={"ai_model": "deepseek"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapters"][0]["title"] == "外婆院中的日子"
    assert data["chapters"][0]["metadata_json"]["title_updated_by"] == "ai"
    assert data["chapters"][0]["metadata_json"]["title_model"] == "fake-title"
    assert "当前章节名" not in fake_ai.calls[0]["user_prompt"]
    assert "新收录转写" not in fake_ai.calls[0]["user_prompt"]
    assert "record_summary:" in fake_ai.calls[0]["user_prompt"]
    assert '"title": "章节标题"' in fake_ai.calls[0]["user_prompt"]
    assert "不要使用“转写”" in fake_ai.calls[0]["user_prompt"]
    assert "外婆家的院子" in fake_ai.calls[0]["user_prompt"]


async def test_mobile_memoir_book_plan_confirm_rejects_foreign_source(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")

    response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {
                    "title": "伪造来源",
                    "source_refs": [
                        {
                            "voice_session_id": "voice-x",
                            "message_id": "message-x",
                            "utterance_id": "utterance-x",
                            "quote": "不存在的来源",
                        }
                    ],
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "章节来源不属于当前回忆录"
