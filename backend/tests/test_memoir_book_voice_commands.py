import json

from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirBookPlanStatus,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    MemoirEditingJob,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from tests.memoir_helpers import create_test_voice_transcript, ensure_mobile_memoir_project, memoir_source_ref


class FakeBookVoiceCommandAI:
    provider_name = "fake-ai"
    model_name = "fake-book-command"

    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload or {}
        self.error = error
        self.calls = []

    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        if self.error:
            raise self.error
        return json.dumps(self.payload, ensure_ascii=False)


async def _create_two_chapter_plan(
    test_db,
    *,
    project_id: str,
    draft_contents: list[str] | None = None,
    titles: list[str] | None = None,
):
    plan = MemoirBookPlan(
        project_id=project_id,
        title="外婆回忆录章节规划",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapters = []
    for index, title in enumerate(titles or ["外婆家的童年", "第一次离家"]):
        chapter = MemoirBookPlanChapter(
            plan_id=plan.id,
            project_id=project_id,
            title=title,
            sort_order=index,
            source_refs_json=[],
        )
        test_db.add(chapter)
        chapters.append(chapter)
    await test_db.flush()
    if draft_contents:
        for chapter, content in zip(chapters, draft_contents, strict=False):
            if not content:
                continue
            draft = MemoirBookChapterDraft(
                project_id=project_id,
                chapter_id=chapter.id,
            )
            test_db.add(draft)
            await test_db.flush()
            test_db.add(
                MemoirBookChapterDraftVersion(
                    draft_id=draft.id,
                    project_id=project_id,
                    chapter_id=chapter.id,
                    version=1,
                    content=content,
                    created_by=MemoirCreatedBy.AI.value,
                )
            )
    await test_db.commit()
    return plan, chapters


async def test_mobile_memoir_book_voice_command_parse_uses_ai_payload(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(test_db, project_id=project_id)
    fake_ai = FakeBookVoiceCommandAI(
        {
            "intent": "rename_chapter",
            "chapter_ref": "第一章",
            "new_title": "童年往事",
            "summary": "准备把第一章改名为童年往事",
        }
    )
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "把第一章改名为童年往事", "ai_model": "deepseek-test"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "rename_chapter"
    assert data["intent_label"] == "修改章名"
    assert data["requires_confirmation"] is True
    assert data["used_ai"] is True
    assert data["provider"] == "fake-ai"
    assert data["model"] == "fake-book-command"
    assert data["operations"] == [
        {
            "type": "rename_chapter",
            "chapter_id": chapters[0].id,
            "chapter_title": "外婆家的童年",
            "target_chapter_id": None,
            "target_chapter_title": None,
            "new_title": "童年往事",
            "direction": None,
            "target_position": None,
            "target_index": None,
            "relative_chapter_id": None,
            "relative_chapter_title": None,
            "relative_position": None,
            "source_position": None,
            "source_index": None,
            "utterance_id": None,
        }
    ]
    assert "1. 外婆家的童年" in fake_ai.calls[0]["user_prompt"]
    assert "2. 第一次离家" in fake_ai.calls[0]["user_prompt"]


async def test_mobile_memoir_book_voice_command_parse_falls_back_when_ai_fails(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(test_db, project_id=project_id)
    fake_ai = FakeBookVoiceCommandAI(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "把第二章上移"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "move_chapter"
    assert data["intent_label"] == "移动章节"
    assert data["summary"] == "准备把「第一次离家」上移。"
    assert data["used_ai"] is False
    assert data["operations"][0]["chapter_id"] == chapters[1].id
    assert data["operations"][0]["direction"] == "up"


async def test_mobile_memoir_book_voice_command_execute_renames_chapter(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(test_db, project_id=project_id)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/execute",
        json={
            "intent": "rename_chapter",
            "operation": {
                "type": "rename_chapter",
                "chapter_id": chapters[0].id,
                "chapter_title": "外婆家的童年",
                "new_title": "童年往事",
            },
        },
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["intent"] == "rename_chapter"
    assert data["summary"] == "已把「外婆家的童年」改名为「童年往事」。"
    assert data["plan"]["chapters"][0]["title"] == "童年往事"
    assert data["source_coverage"]["project_id"] == project_id


async def test_mobile_memoir_book_voice_command_execute_moves_chapter(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(test_db, project_id=project_id)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/execute",
        json={
            "intent": "move_chapter",
            "operation": {
                "type": "move_chapter",
                "chapter_id": chapters[1].id,
                "chapter_title": "第一次离家",
                "direction": "up",
            },
        },
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["intent"] == "move_chapter"
    assert data["summary"] == "已把「第一次离家」上移。"
    assert [chapter["id"] for chapter in data["plan"]["chapters"]] == [chapters[1].id, chapters[0].id]


async def test_mobile_memoir_book_voice_command_execute_moves_chapter_to_last(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["小时候的年", "一生的朋友", "职场浮沉与升职加薪"],
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/execute",
        json={
            "intent": "move_chapter",
            "operation": {
                "type": "move_chapter",
                "chapter_id": chapters[1].id,
                "chapter_title": "一生的朋友",
                "target_position": "last",
            },
        },
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["intent"] == "move_chapter"
    assert data["summary"] == "已把「一生的朋友」移到最后一章。"
    assert [chapter["id"] for chapter in data["plan"]["chapters"]] == [
        chapters[0].id,
        chapters[2].id,
        chapters[1].id,
    ]


async def test_mobile_memoir_book_voice_command_execute_merges_chapters(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["童年友情", "童年年节欢趣"],
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/execute",
        json={
            "intent": "merge_chapters",
            "operation": {
                "type": "merge_chapters",
                "chapter_id": chapters[0].id,
                "chapter_title": "童年友情",
                "target_chapter_id": chapters[1].id,
                "target_chapter_title": "童年年节欢趣",
            },
        },
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["intent"] == "merge_chapters"
    assert data["summary"] == "已把「童年友情」合并到「童年年节欢趣」。"
    assert [chapter["id"] for chapter in data["plan"]["chapters"]] == [chapters[1].id]


async def test_mobile_memoir_book_voice_command_merge_schedules_target_chapter_revision(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "小时候放学后，我常和同学去河边玩。")
    second = await create_test_voice_transcript(
        client,
        test_db,
        "春节那几天，家里总会做很多年菜。",
        project_id=first["session"]["project_id"],
    )
    project_id = first["session"]["project_id"]
    _, chapters = await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["童年友情", "童年年节欢趣"],
    )
    chapters[0].source_refs_json = [memoir_source_ref(first)]
    chapters[1].source_refs_json = [memoir_source_ref(second)]
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapters[1].id,
    )
    test_db.add_all([chapters[0], chapters[1], draft])
    await test_db.flush()
    version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapters[1].id,
        version=1,
        content="春节那几天，家里总会做很多年菜。",
        created_by=MemoirCreatedBy.AI.value,
    )
    test_db.add(version)
    await test_db.commit()

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/execute",
        json={
            "intent": "merge_chapters",
            "operation": {
                "type": "merge_chapters",
                "chapter_id": chapters[0].id,
                "chapter_title": "童年友情",
                "target_chapter_id": chapters[1].id,
                "target_chapter_title": "童年年节欢趣",
            },
        },
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    target_chapter = data["plan"]["chapters"][0]
    assert target_chapter["id"] == chapters[1].id
    assert [ref["utterance_id"] for ref in target_chapter["source_refs"]] == [
        second["utterance_id"],
        first["utterance_id"],
    ]
    assert target_chapter["pending_source_count"] == 1

    revision_job = await test_db.scalar(
        select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f"revise_chapter:{chapters[1].id}")
    )
    assert revision_job is not None
    assert revision_job.job_type == MemoirEditingJobType.REVISE_CHAPTER.value
    assert revision_job.status == MemoirEditingJobStatus.PENDING.value
    assert revision_job.chapter_id == chapters[1].id
    assert revision_job.payload_json["base_version_id"] == version.id
    assert revision_job.payload_json["pending_source_utterance_ids"] == [first["utterance_id"]]


async def test_mobile_memoir_book_voice_command_execute_assigns_uncovered_source(client, test_db):
    created = await create_test_voice_transcript(client, test_db, "我第一次进厂的时候，师傅带我熟悉车间。")
    project_id = created["session"]["project_id"]
    _, chapters = await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["小时候的年", "职场浮沉与升职加薪"],
    )

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/execute",
        json={
            "intent": "assign_source",
            "operation": {
                "type": "assign_source",
                "chapter_id": chapters[1].id,
                "chapter_title": "职场浮沉与升职加薪",
                "source_position": 1,
                "source_index": 1,
                "utterance_id": created["utterance_id"],
            },
        },
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["intent"] == "assign_source"
    assert data["summary"] == "已把待收录第 1 条归入「职场浮沉与升职加薪」。"
    target_chapter = next(chapter for chapter in data["plan"]["chapters"] if chapter["id"] == chapters[1].id)
    assert [ref["utterance_id"] for ref in target_chapter["source_refs"]] == [created["utterance_id"]]
    assert target_chapter["pending_source_count"] == 1
    assert data["source_coverage"]["covered_source_count"] == 1
    assert data["source_coverage"]["uncovered_source_count"] == 0


async def test_mobile_memoir_book_voice_command_parses_direct_uncovered_source_assignment(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["小时候的年", "职场浮沉与升职加薪"],
    )
    fake_ai = FakeBookVoiceCommandAI(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)
    book_context = {
        "source_coverage": {
            "total_source_count": 32,
            "covered_source_count": 22,
            "uncovered_source_count": 10,
        },
        "uncovered_sources": [
            {
                "source_index": 23,
                "utterance_id": "utterance-new-23",
                "text": "我第一次进厂的时候，师傅带着我熟悉车间。",
            },
            {
                "source_index": 24,
                "utterance_id": "utterance-new-24",
                "text": "后来有一次加班到很晚。",
            },
        ],
    }

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={
            "text": "把第一条没收入的记录归到职场那章。",
            "book_context": book_context,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "assign_source"
    assert data["used_ai"] is False
    assert data["summary"] == "准备把待收录第 1 条归入「职场浮沉与升职加薪」。"
    assert data["operations"][0]["chapter_id"] == chapters[1].id
    assert data["operations"][0]["utterance_id"] == "utterance-new-23"
    assert data["operations"][0]["source_index"] == 23


async def test_mobile_memoir_book_voice_command_execute_rejects_preview_only_intents(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(test_db, project_id=project_id)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/execute",
        json={
            "intent": "read_chapter",
            "operation": {
                "type": "read_chapter",
                "chapter_id": chapters[0].id,
                "chapter_title": "外婆家的童年",
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "这个语音操作暂时只能预览"


async def test_mobile_memoir_book_voice_command_swaps_adjacent_chapters_from_asr_zhang(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["小时候的年", "儿时庭院与家人聚散", "一生的朋友"],
    )
    fake_ai = FakeBookVoiceCommandAI(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "我想把第二张和第三张的顺序换一下。"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["original_text"] == "我想把第二章和第三章的顺序换一下。"
    assert data["intent"] == "move_chapter"
    assert data["intent_label"] == "移动章节"
    assert data["summary"] == "准备把「儿时庭院与家人聚散」下移。"
    assert data["requires_confirmation"] is True
    assert data["operations"][0]["chapter_id"] == chapters[1].id
    assert data["operations"][0]["direction"] == "down"


async def test_mobile_memoir_book_voice_command_swaps_adjacent_chapters_by_fuzzy_titles(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["小时候的年", "儿时庭院与家人聚散", "一生的朋友", "职场浮沉与升职加薪"],
    )
    fake_ai = FakeBookVoiceCommandAI(
        {
            "intent": "move_chapter",
            "chapter_ref": "小时候的年",
            "direction": "down",
            "summary": "准备把小时候的年下移",
        }
    )
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "不是啊，我说医生的朋友和职场浮沉这两章的顺序换一下。"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "move_chapter"
    assert data["summary"] == "准备把「一生的朋友」下移。"
    assert data["used_ai"] is False
    assert data["operations"][0]["chapter_id"] == chapters[2].id
    assert data["operations"][0]["direction"] == "down"


async def test_mobile_memoir_book_voice_command_swaps_named_chapters_before_ai_payload(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["小时候的年", "医生的朋友", "职场浮沉与升职加薪"],
    )
    fake_ai = FakeBookVoiceCommandAI(
        {
            "intent": "move_chapter",
            "chapter_ref": "小时候的年",
            "direction": "down",
            "summary": "准备把小时候的年下移",
        }
    )
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "帮我把医生的朋友和职场这两章的顺序换一下。"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "move_chapter"
    assert data["summary"] == "准备把「医生的朋友」下移。"
    assert data["used_ai"] is False
    assert data["operations"][0]["chapter_id"] == chapters[1].id
    assert data["operations"][0]["direction"] == "down"


async def test_mobile_memoir_book_voice_command_moves_chapter_to_named_position(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["小时候的年", "一生的朋友", "儿时庭院与家人聚散", "职场浮沉与升职加薪"],
    )
    fake_ai = FakeBookVoiceCommandAI(
        {
            "intent": "move_chapter",
            "chapter_ref": "小时候的年",
            "direction": "down",
            "summary": "准备把小时候的年下移",
        }
    )
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "我想把一生的朋友这一章移到最后一章去。"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "move_chapter"
    assert data["summary"] == "准备把「一生的朋友」移到最后一章。"
    assert data["used_ai"] is False
    assert data["operations"][0]["chapter_id"] == chapters[1].id
    assert data["operations"][0]["target_position"] == "last"
    assert data["operations"][0]["direction"] is None

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "把职场浮沉与升职加薪这一章移到第 2 章。"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "move_chapter"
    assert data["summary"] == "准备把「职场浮沉与升职加薪」移到第 2 章。"
    assert data["operations"][0]["chapter_id"] == chapters[3].id
    assert data["operations"][0]["target_index"] == 2
    assert data["operations"][0]["direction"] is None


async def test_mobile_memoir_book_voice_command_reads_and_assigns_uncovered_sources(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    _, chapters = await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["小时候的年", "职场浮沉与升职加薪"],
    )
    fake_ai = FakeBookVoiceCommandAI(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)
    book_context = {
        "source_coverage": {
            "total_source_count": 32,
            "covered_source_count": 22,
            "uncovered_source_count": 10,
        },
        "uncovered_sources": [
            {
                "source_index": 23,
                "utterance_id": "utterance-new-23",
                "text": "我第一次进厂的时候，师傅带着我熟悉车间。",
            },
            {
                "source_index": 24,
                "utterance_id": "utterance-new-24",
                "text": "后来有一次加班到很晚。",
            },
        ],
    }

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={
            "text": "你给我念一下第一条没收入的记录吧。",
            "book_context": book_context,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "read_uncovered_source"
    assert data["intent_label"] == "朗读记录"
    assert data["requires_confirmation"] is False
    assert "待收录第 1 条" in data["reply_text"]
    assert "我第一次进厂" in data["reply_text"]

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={
            "text": "再读下一条。",
            "book_context": book_context,
            "conversation_context": [
                {"role": "assistant", "text": data["reply_text"]},
            ],
        },
    )

    assert response.status_code == 200
    next_data = response.json()
    assert next_data["intent"] == "read_uncovered_source"
    assert "待收录第 2 条" in next_data["reply_text"]
    assert "后来有一次加班" in next_data["reply_text"]

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={
            "text": "把刚才这条归到职场那章。",
            "book_context": book_context,
            "conversation_context": [
                {"role": "assistant", "text": data["reply_text"]},
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "assign_source"
    assert data["intent_label"] == "归入记录"
    assert data["requires_confirmation"] is True
    assert data["summary"] == "准备把待收录第 1 条归入「职场浮沉与升职加薪」。"
    assert data["operations"][0]["chapter_id"] == chapters[1].id
    assert data["operations"][0]["utterance_id"] == "utterance-new-23"
    assert data["operations"][0]["source_position"] == 1
    assert data["operations"][0]["source_index"] == 23


async def test_mobile_memoir_book_voice_command_parse_returns_chapter_title_advice(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        draft_contents=["外婆家的厨房里总有柴火味，她会在傍晚给孩子们讲老家的故事。", "第一次离家那天，火车站很吵。"],
    )
    fake_ai = FakeBookVoiceCommandAI(
        {
            "intent": "review_chapter_title",
            "chapter_ref": "第一章",
            "summary": "正在判断第一章章名是否合适",
            "reply_text": "这个章名方向是对的，但还可以更具体。若重点是厨房和傍晚的故事，可以考虑突出那个场景。",
        }
    )
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "第一章是不是需要换个名字", "ai_model": "deepseek-test"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "review_chapter_title"
    assert data["intent_label"] == "章名建议"
    assert data["requires_confirmation"] is False
    assert data["operations"] == []
    assert data["used_ai"] is True
    assert data["reply_text"] == "这个章名方向是对的，但还可以更具体。若重点是厨房和傍晚的故事，可以考虑突出那个场景。"
    assert "正文" in fake_ai.calls[0]["user_prompt"]
    assert "摘录：外婆家的厨房" in fake_ai.calls[0]["user_prompt"]


async def test_mobile_memoir_book_voice_command_parse_falls_back_to_length_advice(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    fake_ai = FakeBookVoiceCommandAI(error=RuntimeError("provider unavailable"))
    await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        draft_contents=[
            "外婆笑了。",
            "第一次离家那天，我记得天还没亮，母亲把包袱塞到我手里。" * 24,
        ],
    )
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "第一章是不是太短了"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "review_chapter_length"
    assert data["intent_label"] == "篇幅建议"
    assert data["summary"] == "正在判断「外婆家的童年」的篇幅是否合适。"
    assert data["requires_confirmation"] is False
    assert data["operations"] == []
    assert data["used_ai"] is False
    assert "外婆家的童年" in data["reply_text"]
    assert "短于其他章节" in data["reply_text"]


async def test_mobile_memoir_book_voice_command_answers_current_chapter_count(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_two_chapter_plan(test_db, project_id=project_id)
    fake_ai = FakeBookVoiceCommandAI(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "现在有几个章节啊"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "answer_book_question"
    assert data["intent_label"] == "成书问答"
    assert data["response_kind"] == "answer"
    assert data["requires_confirmation"] is False
    assert data["operations"] == []
    assert "2 章" in data["reply_text"]
    assert "外婆家的童年" in data["reply_text"]
    assert "第一次离家" in data["reply_text"]

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "章节一共有几个啊"},
    )

    assert response.status_code == 200
    assert response.json()["intent"] == "answer_book_question"

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "现在有几个张杰啊"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["original_text"] == "现在有几个章节啊"
    assert data["intent"] == "answer_book_question"
    assert "2 章" in data["reply_text"]


async def test_mobile_memoir_book_voice_command_answers_uncovered_source_count_from_context(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_two_chapter_plan(
        test_db,
        project_id=project_id,
        titles=["小时候的年", "儿时庭院与家人聚散", "一生的朋友", "职场浮沉与升职加薪"],
    )
    fake_ai = FakeBookVoiceCommandAI(
        {
            "intent": "answer_book_question",
            "summary": "回答素材收入状态",
            "reply_text": "目前所有素材都已归入 4 个章节，没有未归类的记录。",
        }
    )
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={
            "text": "我现在是不是有一些记录还没归到章节呀",
            "book_context": {
                "source_coverage": {
                    "total_source_count": 18,
                    "covered_source_count": 11,
                    "uncovered_source_count": 7,
                }
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "answer_book_question"
    assert data["response_kind"] == "answer"
    assert data["requires_confirmation"] is False
    assert data["operations"] == []
    assert "共有 18 条讲述记录" in data["reply_text"]
    assert "已收入章节 11 条" in data["reply_text"]
    assert "还有 7 条未收入章节" in data["reply_text"]
    assert "所有素材都已归入" not in data["reply_text"]
    assert "讲述记录共 18 条" in fake_ai.calls[0]["user_prompt"]

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={
            "text": "有一张记录还没归到章节吗",
            "book_context": {
                "source_coverage": {
                    "total_source_count": 18,
                    "covered_source_count": 17,
                    "uncovered_source_count": 1,
                }
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["original_text"] == "有一张记录还没归到章节吗"
    assert "还有 1 条未收入章节" in response.json()["reply_text"]


async def test_mobile_memoir_book_voice_command_explains_editing_capabilities(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_two_chapter_plan(test_db, project_id=project_id)
    fake_ai = FakeBookVoiceCommandAI(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "我可以修改些啥"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "capability_help"
    assert data["response_kind"] == "answer"
    assert data["requires_confirmation"] is False
    assert data["operations"] == []
    assert "改章名" in data["reply_text"]
    assert "合并章节" in data["reply_text"]
    assert "等你确认" in data["reply_text"]

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={"text": "我还可以改点啥呀"},
    )

    assert response.status_code == 200
    assert response.json()["intent"] == "capability_help"


async def test_mobile_memoir_book_voice_command_passes_recent_conversation_to_ai(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_two_chapter_plan(test_db, project_id=project_id)
    fake_ai = FakeBookVoiceCommandAI(
        {
            "intent": "answer_book_question",
            "response_kind": "answer",
            "summary": "回答当前目录章节数",
            "reply_text": "现在有两章，分别是外婆家的童年和第一次离家。",
        }
    )
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={
            "text": "现在呢",
            "conversation_context": [
                {"role": "user", "text": "现在都有几张呀"},
                {"role": "assistant", "text": "现在目录有 2 章。"},
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "answer_book_question"
    assert data["response_kind"] == "answer"
    assert data["reply_text"] == "现在有两章，分别是外婆家的童年和第一次离家。"
    assert "最近语音对话" in fake_ai.calls[0]["user_prompt"]
    assert "用户：现在都有几张呀" in fake_ai.calls[0]["user_prompt"]
    assert "AI：现在目录有 2 章。" in fake_ai.calls[0]["user_prompt"]


async def test_mobile_memoir_book_voice_command_clarifies_ai_follow_up_without_reply_text(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_two_chapter_plan(test_db, project_id=project_id)
    fake_ai = FakeBookVoiceCommandAI(
        {
            "intent": "clarify_previous",
            "summary": "需要确认用户指代的修改对象",
        }
    )
    monkeypatch.setattr("app.services.memoir.book_voice_command_parser.get_ai_service", lambda _model: fake_ai)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-command/parse",
        json={
            "text": "那个",
            "conversation_context": [
                {"role": "assistant", "text": "可以改章名、调顺序、合并章节。"},
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "clarify_previous"
    assert data["response_kind"] == "clarification"
    assert data["requires_confirmation"] is False
    assert data["operations"] == []
    assert "想改章名、调整顺序" in data["reply_text"]
