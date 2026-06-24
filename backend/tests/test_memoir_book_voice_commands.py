import json

from app.domain.memoir.types import MemoirBookPlanStatus, MemoirCreatedBy
from app.models.memoir import (
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
)
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_tts import TTSResult
from tests.memoir_helpers import ensure_mobile_memoir_project


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


class FakeBookVoiceAssistantTTS:
    provider_name = "fake-tts"
    model_name = "fake-tts-model"
    default_voice = ""

    def __init__(self):
        self.calls = []

    async def synthesize(self, text, *, voice=None, speed_ratio=None):
        self.calls.append({"text": text, "voice": voice, "speed_ratio": speed_ratio})
        duration_seconds = max(0.2, min(8, len(text)) * 0.08)
        return TTSResult(
            audio=b"voice-assistant-audio",
            mime_type="audio/mpeg",
            extension=".mp3",
            provider=self.provider_name,
            model=self.model_name,
            voice=voice or self.default_voice,
            speed_ratio=speed_ratio or 1.0,
            alignment=[
                TTSAlignmentSegment(
                    text=text[:4],
                    start_ms=0,
                    end_ms=int(duration_seconds * 1000),
                    text_start=0,
                    text_end=min(4, len(text)),
                )
            ],
            duration_seconds=duration_seconds,
        )


async def _create_two_chapter_plan(test_db, *, project_id: str, draft_contents: list[str] | None = None):
    plan = MemoirBookPlan(
        project_id=project_id,
        title="外婆回忆录章节规划",
        status=MemoirBookPlanStatus.CONFIRMED.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapters = []
    for index, title in enumerate(["外婆家的童年", "第一次离家"]):
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
    monkeypatch.setattr("app.services.memoir.book_voice_commands.get_ai_service", lambda _model: fake_ai)

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
    monkeypatch.setattr("app.services.memoir.book_voice_commands.get_ai_service", lambda _model: fake_ai)

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
    monkeypatch.setattr("app.services.memoir.book_voice_commands.get_ai_service", lambda _model: fake_ai)

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
    monkeypatch.setattr("app.services.memoir.book_voice_commands.get_ai_service", lambda _model: fake_ai)

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
    monkeypatch.setattr("app.services.memoir.book_voice_commands.get_ai_service", lambda _model: fake_ai)

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


async def test_mobile_memoir_book_voice_command_explains_editing_capabilities(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client)
    await _create_two_chapter_plan(test_db, project_id=project_id)
    fake_ai = FakeBookVoiceCommandAI(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr("app.services.memoir.book_voice_commands.get_ai_service", lambda _model: fake_ai)

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
    monkeypatch.setattr("app.services.memoir.book_voice_commands.get_ai_service", lambda _model: fake_ai)

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
    monkeypatch.setattr("app.services.memoir.book_voice_commands.get_ai_service", lambda _model: fake_ai)

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


async def test_mobile_memoir_book_voice_assistant_tts_generates_short_reply(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    project_id = await ensure_mobile_memoir_project(client)
    await _create_two_chapter_plan(test_db, project_id=project_id)
    fake_tts = FakeBookVoiceAssistantTTS()
    monkeypatch.setattr("app.services.memoir.book_audio.get_tts_client", lambda: fake_tts)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-assistant/tts",
        json={"text": "我理解了，请确认后我再改。", "voice": "voice-a", "speed_ratio": 1.2},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["audio_file_ids"]
    assert data["provider"] == "fake-tts"
    assert data["model"] == "fake-tts-model"
    assert data["alignment_provider"] == "fake-tts"
    assert fake_tts.calls == [
        {"text": "我理解了，请确认后我再改。", "voice": "voice-a", "speed_ratio": 1.2}
    ]


async def test_mobile_memoir_book_voice_assistant_tts_requires_plan(client):
    project_id = await ensure_mobile_memoir_project(client)

    response = await client.post(
        f"/api/mobile/memoir/projects/{project_id}/book-voice-assistant/tts",
        json={"text": "我先等你生成章节。"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "章节规划不存在"
