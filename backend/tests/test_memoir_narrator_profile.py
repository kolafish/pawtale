import json

import pytest

from app.models.memoir import MemoirVoiceSession
from app.models.projects import Project
from app.services.ai_base import AITextServiceBase
from app.services.memoir import narrator_profile
from tests.memoir_helpers import create_test_voice_transcript, ensure_mobile_memoir_project


class FakeProfileAI(AITextServiceBase):
    provider_name = "fake"
    model_name = "fake-profile"

    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    async def _call_llm(self, _system_prompt, _user_prompt, **_kwargs):
        return json.dumps(self.payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_profile_fills_empty_fields_from_narration_and_names_the_first_book(
    client,
    test_db,
    monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    updated = await client.patch(f"/api/mobile/memoir/projects/{project_id}", json={"narrator_origin": "重庆"})
    assert updated.status_code == 200
    transcript = await create_test_voice_transcript(
        client,
        test_db,
        "我叫王秀音，五二年生的，老家四川成都。",
        project_id=project_id,
        question_text="怎么称呼您？",
    )
    voice_session = await test_db.get(MemoirVoiceSession, transcript["session"]["id"])
    fake = FakeProfileAI({"name": "王秀音", "gender": "female", "age": 74, "origin": "四川成都"})
    monkeypatch.setattr(narrator_profile, "get_ai_service", lambda _model=None: fake)

    project = await test_db.get(Project, project_id)
    await narrator_profile.fill_narrator_profile(test_db, project=project, voice_session=voice_session)

    profile = project.metadata_json["memoir_profile"]
    assert project.title == "王秀音的回忆录"
    assert (profile["narrator_name"], profile["narrator_gender"], profile["narrator_age"]) == ("王秀音", "female", 74)
    # The hometown the user saved stays.
    assert profile["narrator_origin"] == "重庆"

    # A spoken correction later in the same session replaces what was heard.
    fake.payload = {"name": "王秀英", "gender": "female", "age": 74, "origin": "四川成都"}
    await narrator_profile.fill_narrator_profile(test_db, project=project, voice_session=voice_session)
    assert project.title == "王秀英的回忆录"
    assert project.metadata_json["memoir_profile"]["narrator_name"] == "王秀英"

    # Once the user saves the name, narration no longer changes it.
    saved = await client.patch(f"/api/mobile/memoir/projects/{project_id}", json={"elder_name": "王秀英"})
    assert saved.status_code == 200
    await test_db.refresh(project)
    fake.payload = {"name": "老王", "gender": None, "age": None, "origin": None}
    await narrator_profile.fill_narrator_profile(test_db, project=project, voice_session=voice_session)
    assert project.metadata_json["memoir_profile"]["narrator_name"] == "王秀英"
