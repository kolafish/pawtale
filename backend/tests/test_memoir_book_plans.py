import json

from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirBookAutoRevisionJobStatus,
    MemoirBookChapterDraftStatus,
    MemoirCreatedBy,
)
from app.models.memoir import (
    MemoirBookAutoRevisionJob,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.services.memoir.book_plan_ai import ChapterTitleSourceDigest, build_chapter_title_prompt
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
    fake_ai = FakeBookPlanAI()
    monkeypatch.setattr(
        "app.services.memoir.book_plans.get_ai_service",
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
    assert stored_chapters[0].source_refs_json[0]["quote"] == "外婆家的院子"

    legacy_ref = dict(stored_chapters[0].source_refs_json[0])
    legacy_ref.pop("created_at", None)
    stored_chapters[0].source_refs_json = [legacy_ref]
    test_db.add(stored_chapters[0])
    await test_db.commit()

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
        "app.services.memoir.book_plans.get_ai_service",
        lambda model=None: FakeBookPlanAI(),
    )
    generated_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/generate",
        json={},
    )
    source_ref = generated_response.json()["chapters"][0]["source_refs"][0]

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


async def test_mobile_memoir_book_source_coverage_detects_new_transcripts_after_plan(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plans.get_ai_service",
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
        "app.services.memoir.book_plans.get_ai_service",
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
    await test_db.commit()
    later = await create_test_voice_transcript(client, test_db, "后来我又讲了第一次离家去县城读书。")

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

    refreshed_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == draft.id)
    )
    assert refreshed_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_draft.metadata_json["status_reason"] == "new_sources_assigned"
    job = await test_db.scalar(
        select(MemoirBookAutoRevisionJob).where(MemoirBookAutoRevisionJob.chapter_id == chapter_id)
    )
    assert job is not None
    assert job.status == MemoirBookAutoRevisionJobStatus.PENDING.value
    assert job.attempts == 0
    assert job.metadata_json["base_version_id"] == version.id
    assert job.metadata_json["pending_source_utterance_ids"] == [later["utterance_id"]]
    assert job.metadata_json["ai_model"] == "deepseek-v4-pro"

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
        "app.services.memoir.book_plans.get_ai_service",
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
    project_id = first["session"]["project_id"]
    confirm_response = await client.put(
        f"/api/mobile/memoir/projects/{project_id}/book-plan",
        json={
            "chapters": [
                {"title": "童年院子", "source_refs": [memoir_source_ref(first)]},
                {"title": "离家求学", "source_refs": [memoir_source_ref(second)]},
            ],
        },
    )
    assert confirm_response.status_code == 200
    first_chapter, second_chapter = confirm_response.json()["chapters"]

    move_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={"type": "move_chapter", "chapter_id": second_chapter["id"], "direction": "up"},
    )

    assert move_response.status_code == 200, move_response.json()
    assert [chapter["id"] for chapter in move_response.json()["chapters"]] == [
        second_chapter["id"],
        first_chapter["id"],
    ]
    assert move_response.json()["metadata_json"]["structure_operation"] == "move_chapter"

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

    create_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={"type": "create_chapter_from_source", "utterance_id": later["utterance_id"]},
    )

    assert create_response.status_code == 200, create_response.json()
    created_chapter = create_response.json()["chapters"][1]
    assert created_chapter["title"] == "后来我又讲了一个认识很多年的朋友"
    assert created_chapter["source_refs"][0]["utterance_id"] == later["utterance_id"]

    delete_response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-plan/structure-commands",
        json={"type": "delete_chapter", "chapter_id": created_chapter["id"]},
    )

    assert delete_response.status_code == 200, delete_response.json()
    assert [chapter["title"] for chapter in delete_response.json()["chapters"]] == ["童年和外婆家"]
    coverage = await client.get(f"/api/mobile/memoir/projects/{project_id}/book-source-coverage")
    assert coverage.status_code == 200
    assert [item["utterance_id"] for item in coverage.json()["uncovered_sources"]] == [later["utterance_id"]]


async def test_mobile_memoir_book_plan_reconfirm_removes_replaced_chapter_drafts(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(client, test_db, "我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    monkeypatch.setattr(
        "app.services.memoir.book_plans.get_ai_service",
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
        "app.services.memoir.book_plans.get_ai_service",
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
        "app.services.memoir.book_plans.get_ai_service",
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
        "app.services.memoir.book_plans.get_ai_service",
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
        "app.services.memoir.book_plans.get_ai_service",
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
        "app.services.memoir.book_plans.get_ai_service",
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
        "app.services.memoir.book_plans.get_ai_service",
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
