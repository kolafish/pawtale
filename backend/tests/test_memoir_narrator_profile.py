import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memoir import MemoirEditingJob, MemoirVoiceSession
from app.models.projects import Project
from app.services.ai_base import AITextServiceBase
from app.services.memoir import narrator_profile, projects
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


@pytest.mark.asyncio
async def test_manual_save_overlapping_profile_completion_preserves_both_updates(
    client, test_db, monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    transcript = await create_test_voice_transcript(
        client, test_db, "我叫王秀英，五二年生的，老家四川成都。", project_id=project_id,
    )
    fake = FakeProfileAI({"name": "王秀英", "gender": "female", "age": 74, "origin": "四川成都"})
    monkeypatch.setattr(narrator_profile, "get_ai_service", lambda _model=None: fake)
    manual_loaded = asyncio.Event()
    profile_completed = asyncio.Event()
    original_load = projects.load_project

    async def load_before_completion(*args, **kwargs):
        project = await original_load(*args, **kwargs)
        manual_loaded.set()
        await profile_completed.wait()
        return project

    monkeypatch.setattr(projects, "load_project", load_before_completion)
    manual_save = asyncio.create_task(client.patch(
        f"/api/mobile/memoir/projects/{project_id}", json={"elder_name": "秀英阿姨"},
    ))
    try:
        await asyncio.wait_for(manual_loaded.wait(), timeout=5)
        async with AsyncSession(test_db.bind, expire_on_commit=False) as worker:
            project = await worker.get(Project, project_id)
            voice_session = await worker.get(MemoirVoiceSession, transcript["session"]["id"])
            await narrator_profile.fill_narrator_profile(worker, project=project, voice_session=voice_session)
        profile_completed.set()
        saved = await asyncio.wait_for(manual_save, timeout=5)
        assert saved.status_code == 200
    finally:
        profile_completed.set()
        if not manual_save.done():
            manual_save.cancel()
        await asyncio.gather(manual_save, return_exceptions=True)

    project = await test_db.get(Project, project_id)
    await test_db.refresh(project)
    profile = project.metadata_json["memoir_profile"]
    assert profile["narrator_name"] == "秀英阿姨"
    assert (profile["narrator_gender"], profile["narrator_age"], profile["narrator_origin"]) == (
        "female", 74, "四川成都",
    )
    inferred = profile[projects.INFERRED_PROFILE_FIELDS_KEY]
    assert "narrator_name" not in inferred
    assert inferred["narrator_age"] == voice_session.id


@pytest.mark.asyncio
async def test_profile_job_is_queued_only_while_the_first_narration_lacks_it(client, test_db, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    first = await create_test_voice_transcript(
        client, test_db, "我叫王秀英，老家四川成都。", project_id=project_id, question_text="怎么称呼您？",
    )
    voice_session = await test_db.get(MemoirVoiceSession, first["session"]["id"])
    await narrator_profile.schedule_narrator_profile(test_db, voice_session=voice_session)
    await test_db.commit()
    job = await test_db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.job_type == "fill_narrator_profile"))
    assert job is not None

    fake = FakeProfileAI({"name": "王秀英", "gender": "female", "age": None, "origin": "四川成都", "skip": []})
    monkeypatch.setattr(narrator_profile, "get_ai_service", lambda _model=None: fake)
    await narrator_profile.run_narrator_profile_job(test_db, job)
    project = await test_db.get(Project, project_id)
    await test_db.refresh(project)
    assert project.title == "王秀英的回忆录"

    # Declining age is remembered, so the interviewer stops asking for it.
    fake.payload = {**fake.payload, "skip": ["age"]}
    await narrator_profile.run_narrator_profile_job(test_db, job)
    await test_db.refresh(project)
    assert projects.profile_items_to_ask(project.metadata_json["memoir_profile"]) == []

    # A later narration no longer extracts anything.
    later = await create_test_voice_transcript(client, test_db, "后来我去了重庆。", project_id=project_id)
    later_session = await test_db.get(MemoirVoiceSession, later["session"]["id"])
    await narrator_profile.schedule_narrator_profile(test_db, voice_session=later_session)
    await test_db.commit()
    jobs = (await test_db.scalars(
        select(MemoirEditingJob).where(MemoirEditingJob.job_type == "fill_narrator_profile")
    )).all()
    assert [item.id for item in jobs] == [job.id]
