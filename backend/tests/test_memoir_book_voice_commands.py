import json

from app.domain.memoir.types import MemoirBookPlanStatus, MemoirCreatedBy
from app.models.memoir import MemoirBookPlan, MemoirBookPlanChapter
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


async def _create_two_chapter_plan(test_db, *, project_id: str):
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

