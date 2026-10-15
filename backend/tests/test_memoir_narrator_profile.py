import asyncio
from datetime import date
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirTranscriptVersionType,
)
from app.models.memoir import MemoirChatMessage, MemoirTranscriptVersion, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.memoir_jobs import MemoirEditingJob
from app.models.projects import Project
from app.services.ai_base import AITextServiceBase
from app.services.asr.streaming import StreamingASREvent
from app.services.memoir import narration_people, narrator_profile, projects, voice_transcript_correction
from app.services.memoir.interview_book_context import InterviewBookContext, load_interview_book_context
from app.services.memoir.voice_chat_context import project_has_previous_narration
from app.services.memoir.voice_final_persistence import VoiceFinalPersistenceService
from app.services.memoir.voice_followup_ai import InterviewerPersona, ProgressPromptContext
from app.services.memoir.voice_interviewer_prompt import build_stream_turn_prompt
from app.services.memoir.voice_transcripts import CleanedTranscriptText
from tests.memoir_helpers import (
    ensure_mobile_memoir_project,
    post_mobile_memoir_voice_session,
    run_test_editing_job,
)

REAL_SCHEDULE = narrator_profile.schedule_narrator_profile
TODAY = date.today()


@pytest.fixture(autouse=True)
def real_profile_scheduling(monkeypatch):
    monkeypatch.setattr(narrator_profile, "schedule_narrator_profile", REAL_SCHEDULE)


class FakeProfileAI(AITextServiceBase):
    provider_name = "fake"
    model_name = "fake-profile"

    def __init__(self, payload: dict[str, object] | None = None):
        self.payload = payload or {}
        self.prompts: list[str] = []

    async def _call_llm(self, _system_prompt, user_prompt, **_kwargs):
        self.prompts.append(user_prompt)
        return json.dumps(self.payload, ensure_ascii=False)


@pytest.fixture
def fake_ai(monkeypatch):
    fake = FakeProfileAI()
    monkeypatch.setattr(narrator_profile, "get_ai_service", lambda _model=None: fake)
    monkeypatch.setattr(narration_people, "get_ai_service", lambda _model=None: fake)
    return fake


async def _session(client, project_id: str, **extra) -> str:
    response = await post_mobile_memoir_voice_session(
        client, project_id=project_id, json={"guidance_mode": "free_narration", "provider": "volc_streaming_asr", **extra},
    )
    assert response.status_code == 201
    return response.json()["session"]["id"]


async def _narrate(client, test_db, session_id: str, text: str):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    service = VoiceFinalPersistenceService(db=test_db, voice_session_id=session_id, user_id=user_id)
    return await service.persist_final(StreamingASREvent(type="final", text=text, raw={}))


async def _ask(
    test_db, session_id: str, text: str, *, offered: str | None = None, asked: bool = True,
) -> MemoirChatMessage:
    session = await test_db.get(MemoirVoiceSession, session_id)
    turn = await test_db.scalar(
        select(func.coalesce(func.max(MemoirChatMessage.turn_index), -1)).where(
            MemoirChatMessage.voice_session_id == session_id
        )
    )
    message = MemoirChatMessage(
        project_id=session.project_id,
        voice_session_id=session_id,
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text=text,
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=turn + 1,
        metadata_json={
            "profile_question": offered,
            "profile_asked": offered if asked else None,
        },
    )
    test_db.add(message)
    await test_db.commit()
    return message


async def _profile_job(test_db, utterance_id: str) -> MemoirEditingJob | None:
    return await test_db.scalar(
        select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f"narrator_profile:{utterance_id}")
    )


async def _run(test_db, utterance_id: str) -> None:
    job = await _profile_job(test_db, utterance_id)
    assert job is not None
    await narrator_profile.run_narrator_profile_job(test_db, job)


async def _profile(test_db, project_id: str) -> tuple[Project, dict]:
    project = await test_db.get(Project, project_id)
    await test_db.refresh(project)
    return project, project.metadata_json["memoir_profile"]


@pytest.mark.asyncio
async def test_each_segment_of_the_first_narration_is_read_with_the_line_before_it(client, test_db, fake_ai):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    opening = await _ask(test_db, session_id, "您好，我是小余。您怎么称呼？", offered="narrator_name")

    first = await _narrate(client, test_db, session_id, "我叫王秀音。")
    project, profile = await _profile(test_db, project_id)
    assert profile[projects.PROFILE_SESSION_KEY] == session_id
    job = await _profile_job(test_db, first.utterance_id)
    assert job.payload_json == {
        "utterance_id": first.utterance_id,
        "transcript_version_id": first.transcript_version_id,
        "previous_message_id": opening.id,
    }
    fake_ai.payload = {"name": "王秀音"}
    await _run(test_db, first.utterance_id)
    assert "AI：您好，我是小余。您怎么称呼？\n叙述者：我叫王秀音。" in fake_ai.prompts[-1]
    project, profile = await _profile(test_db, project_id)
    assert project.title == "王秀音的回忆录"

    # 小余 says the name aloud; a spoken correction in the next segment replaces it.
    asked = await _ask(test_db, session_id, "王秀音阿姨，您是哪年生的？", offered="narrator_age")
    second = await _narrate(client, test_db, session_id, "是秀英，英雄的英。我五二年生的，老家四川成都。")
    assert (await _profile_job(test_db, second.utterance_id)).payload_json["previous_message_id"] == asked.id
    fake_ai.payload = {"name": "王秀英", "gender": "female", "age": TODAY.year - 1952, "origin": "四川成都"}
    await _run(test_db, second.utterance_id)
    assert "已经记下的资料：称呼：王秀音" in fake_ai.prompts[-1]
    project, profile = await _profile(test_db, project_id)
    assert project.title == "王秀英的回忆录"
    assert (profile["narrator_name"], profile["narrator_age"], profile["narrator_origin"]) == (
        "王秀英", TODAY.year - 1952, "四川成都",
    )
    source = profile[projects.INFERRED_PROFILE_FIELDS_KEY]["narrator_name"]
    assert source["utterance_id"] == second.utterance_id
    assert source["transcript_version_id"] == second.transcript_version_id


@pytest.mark.asyncio
async def test_the_last_segment_before_stopping_is_still_read(client, test_db, fake_ai):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    await _ask(test_db, session_id, "您老家是哪里的？", offered="narrator_origin")
    last = await _narrate(client, test_db, session_id, "山东烟台。好了，今天先讲到这儿。")
    stopped = await client.post(f"/api/mobile/memoir/voice-sessions/{session_id}/stop", json={})
    assert stopped.status_code == 200

    fake_ai.payload = {"origin": "山东烟台"}
    await _run(test_db, last.utterance_id)
    _project, profile = await _profile(test_db, project_id)
    assert profile["narrator_origin"] == "山东烟台"


async def _new_version(test_db, utterance_id: str, text: str) -> str:
    utterance = await test_db.get(MemoirVoiceUtterance, utterance_id)
    message = await test_db.get(MemoirChatMessage, utterance.chat_message_id)
    version = MemoirTranscriptVersion(
        voice_session_id=utterance.voice_session_id,
        source_artifact_id=utterance.id,
        version_type=MemoirTranscriptVersionType.DIALECT_CORRECTED.value,
        content=text,
        created_by=MemoirCreatedBy.AI.value,
    )
    test_db.add(version)
    await test_db.flush()
    utterance.transcript_edited = text
    message.content_text = text
    message.transcript_version_id = version.id
    await test_db.commit()
    return version.id


@pytest.mark.asyncio
async def test_a_corrected_transcript_is_read_again_and_a_stale_version_writes_nothing(
    client, test_db, fake_ai, monkeypatch,
):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    first = await _narrate(client, test_db, session_id, "我叫王秀音。")

    # The correction job reads the new version even when the segment already sits in a chapter.
    async def layered(*_args, **_kwargs):
        return True

    async def cleaned(_text, **_kwargs):
        return CleanedTranscriptText(content="我叫王秀英。", provider="test-ai", created_by=MemoirCreatedBy.AI)

    monkeypatch.setattr(voice_transcript_correction, "_refresh_layered_material", layered)
    monkeypatch.setattr(voice_transcript_correction, "generate_cleaned_transcript_text", cleaned)
    await voice_transcript_correction.run_transcript_correction_job(test_db, SimpleNamespace(
        project_id=project_id,
        payload_json={"utterance_id": first.utterance_id, "raw_transcript_version_id": first.transcript_version_id},
    ))
    await test_db.commit()
    message = await test_db.get(MemoirChatMessage, first.message_id)
    await test_db.refresh(message)
    assert message.transcript_version_id != first.transcript_version_id
    job = await _profile_job(test_db, first.utterance_id)
    await test_db.refresh(job)
    assert job.payload_json["transcript_version_id"] == message.transcript_version_id

    # A job for the old version finds the transcript changed and writes nothing.
    fake_ai.payload = {"name": "王秀音"}
    assert not await narrator_profile.fill_narrator_profile(
        test_db,
        project=await test_db.get(Project, project_id),
        utterance=await narrator_profile._load_utterance(test_db, first.utterance_id),
        transcript_version_id=first.transcript_version_id,
        previous_message_id=None,
    )
    _project, profile = await _profile(test_db, project_id)
    assert not profile.get("narrator_name")

    fake_ai.payload = {"name": "王秀英"}
    await _run(test_db, first.utterance_id)
    # The corrected version is also read for people, after the profile.
    assert "叙述者：我叫王秀英。" in fake_ai.prompts[-2]
    assert "讲述者：我叫王秀英。" in fake_ai.prompts[-1]
    _project, profile = await _profile(test_db, project_id)
    assert profile["narrator_name"] == "王秀英"


@pytest.mark.asyncio
@pytest.mark.parametrize("later_session", [False, True])
async def test_rereading_an_earlier_segment_does_not_undo_a_later_correction(client, test_db, fake_ai, later_session):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    await _ask(test_db, session_id, "您怎么称呼？", offered="narrator_name")
    first = await _narrate(client, test_db, session_id, "我叫王秀音。")
    fake_ai.payload = {"name": "王秀音"}
    await _run(test_db, first.utterance_id)
    if later_session:
        session_id = await _session(client, project_id)
    later = await _narrate(client, test_db, session_id, "大家都叫我老王。")
    fake_ai.payload = {"name": "老王"}
    await _run(test_db, later.utterance_id)
    _project, profile = await _profile(test_db, project_id)
    assert profile["narrator_name"] == "老王"

    await _new_version(test_db, first.utterance_id, "我叫王秀英。")
    await narrator_profile.schedule_narrator_profile(test_db, utterance=await test_db.get(
        MemoirVoiceUtterance, first.utterance_id,
    ))
    await test_db.commit()
    fake_ai.payload = {"name": "王秀英"}
    await _run(test_db, first.utterance_id)
    project, profile = await _profile(test_db, project_id)
    assert profile["narrator_name"] == "老王"
    assert project.title == "老王的回忆录"


@pytest.mark.asyncio
@pytest.mark.parametrize("old,old_output,corrected,new_output,age,declined", [
    ("我七十岁。", {"age": 70}, "我母亲七十岁。", {}, None, False),
    ("我七十岁。", {"age": 70}, "我今年七十岁。", {"age": 70}, 70, False),
    ("我的年纪就不说了。", {"skip": ["age"]}, "母亲的年纪就不说了。", {}, None, False),
    ("我的年纪就不说了。", {"skip": ["age"]}, "我的年纪不想说。", {"skip": ["age"]}, None, True),
])
async def test_corrected_segment_replaces_its_own_profile_evidence(
    client, test_db, fake_ai, old, old_output, corrected, new_output, age, declined,
):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    segment = await _narrate(client, test_db, await _session(client, project_id), old)
    fake_ai.payload = old_output
    await _run(test_db, segment.utterance_id)
    version = await _new_version(test_db, segment.utterance_id, corrected)
    await narrator_profile.schedule_narrator_profile(
        test_db, utterance=await test_db.get(MemoirVoiceUtterance, segment.utterance_id),
    )
    await test_db.commit()
    fake_ai.prompts.clear()
    fake_ai.payload = new_output
    await _run(test_db, segment.utterance_id)
    _, profile = await _profile(test_db, project_id)
    assert profile.get("narrator_age") == age
    assert ("narrator_age" in profile.get(projects.SKIPPED_PROFILE_FIELDS_KEY, {})) == declined
    inferred = profile.get(projects.INFERRED_PROFILE_FIELDS_KEY, {})
    if age is None:
        assert "narrator_age" not in inferred
    else:
        assert inferred["narrator_age"]["transcript_version_id"] == version
    # An unchanged answer must be extracted again, not hidden by the "same as known => null" rule.
    assert "已经记下的资料：还没有" in fake_ai.prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("protected_by", ["manual", "later_value", "later_same_value", "later_refusal"])
async def test_source_retraction_preserves_manual_and_later_evidence(client, test_db, fake_ai, protected_by):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    session_id = await _session(client, project_id)
    first = await _narrate(client, test_db, session_id, "我的年纪就不说了。" if protected_by == "later_refusal" else "我七十岁。")
    fake_ai.payload = {"skip": ["age"]} if protected_by == "later_refusal" else {"age": 70}
    await _run(test_db, first.utterance_id)
    if protected_by == "manual":
        assert (await client.patch(f"/api/mobile/memoir/projects/{project_id}", json={"narrator_age": 71})).status_code == 200
    else:
        text = "我还是不想说年纪。" if protected_by == "later_refusal" else (
            "我七十岁。" if protected_by == "later_same_value" else "我七十一岁。"
        )
        later = await _narrate(client, test_db, session_id, text)
        fake_ai.payload = {"skip": ["age"]} if protected_by == "later_refusal" else {
            "age": 70 if protected_by == "later_same_value" else 71,
        }
        await _run(test_db, later.utterance_id)
    await _new_version(test_db, first.utterance_id, "我母亲七十岁。")
    await narrator_profile.schedule_narrator_profile(
        test_db, utterance=await test_db.get(MemoirVoiceUtterance, first.utterance_id),
    )
    await test_db.commit()
    fake_ai.payload = {}
    await _run(test_db, first.utterance_id)
    _, profile = await _profile(test_db, project_id)
    if protected_by == "later_refusal":
        assert profile[projects.SKIPPED_PROFILE_FIELDS_KEY]["narrator_age"] == later.utterance_id
    else:
        assert profile["narrator_age"] == (70 if protected_by == "later_same_value" else 71)
        if protected_by == "manual":
            assert "narrator_age" not in profile[projects.INFERRED_PROFILE_FIELDS_KEY]
        else:
            assert profile[projects.INFERRED_PROFILE_FIELDS_KEY]["narrator_age"]["utterance_id"] == later.utterance_id


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_title", [False, True])
async def test_retracting_a_name_also_retracts_only_its_automatic_book_title(client, test_db, fake_ai, custom_title):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    segment = await _narrate(client, test_db, await _session(client, project_id), "我叫王秀英。")
    fake_ai.payload = {"name": "王秀英"}
    await _run(test_db, segment.utterance_id)
    if custom_title:
        assert (await client.patch(f"/api/mobile/memoir/projects/{project_id}", json={"title": "我的岁月"})).status_code == 200
    await _new_version(test_db, segment.utterance_id, "我母亲叫王秀英。")
    await narrator_profile.schedule_narrator_profile(
        test_db, utterance=await test_db.get(MemoirVoiceUtterance, segment.utterance_id),
    )
    await test_db.commit()
    fake_ai.payload = {}
    await _run(test_db, segment.utterance_id)
    project, profile = await _profile(test_db, project_id)
    assert not profile.get("narrator_name")
    assert project.title == ("我的岁月" if custom_title else "我的回忆录")


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,text,correction", [
    ("age", 70, "我七十岁。", "我母亲七十岁。"),
    ("name", "王秀英", "我叫王秀英。", "我母亲叫王秀英。"),
])
@pytest.mark.parametrize("withdraw_latest", [False, True])
async def test_retracting_either_duplicate_preserves_the_other_until_both_are_withdrawn(
    client, test_db, fake_ai, field, value, text, correction, withdraw_latest,
):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    session_id = await _session(client, project_id)
    segments = []
    for _ in range(2):
        segment = await _narrate(client, test_db, session_id, text)
        fake_ai.payload = {field: value}
        await _run(test_db, segment.utterance_id)
        segments.append(segment)
    withdrawn, remaining = segments[::-1] if withdraw_latest else segments
    key = f"narrator_{field}"
    for segment in (withdrawn, remaining):
        await _new_version(test_db, segment.utterance_id, correction)
        await narrator_profile.schedule_narrator_profile(
            test_db, utterance=await test_db.get(MemoirVoiceUtterance, segment.utterance_id),
        )
        await test_db.commit()
        fake_ai.payload = {}
        await _run(test_db, segment.utterance_id)
        project, profile = await _profile(test_db, project_id)
        if segment is withdrawn:
            assert profile[key] == value
            assert profile[projects.INFERRED_PROFILE_FIELDS_KEY][key]["utterance_id"] == remaining.utterance_id
            if field == "name":
                assert project.title == "王秀英的回忆录"
        else:
            assert not profile.get(key)
            assert key not in profile.get(projects.INFERRED_PROFILE_FIELDS_KEY, {})
            assert project.title == "我的回忆录"


@pytest.mark.asyncio
async def test_retraction_restores_latest_remaining_value_even_when_jobs_finish_out_of_order(client, test_db, fake_ai):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    segments = [
        await _narrate(client, test_db, await _session(client, project_id), f"我{age}岁。")
        for age in (68, 69, 70)
    ]
    for index in (2, 0, 1):
        fake_ai.payload = {"age": 68 + index}
        await _run(test_db, segments[index].utterance_id)
    for index, expected in ((2, 69), (1, 68)):
        segment = segments[index]
        await _new_version(test_db, segment.utterance_id, f"我母亲{68 + index}岁。")
        await narrator_profile.schedule_narrator_profile(
            test_db, utterance=await test_db.get(MemoirVoiceUtterance, segment.utterance_id),
        )
        await test_db.commit()
        fake_ai.payload = {}
        await _run(test_db, segment.utterance_id)
        _, profile = await _profile(test_db, project_id)
        assert profile["narrator_age"] == expected
        assert profile[projects.INFERRED_PROFILE_FIELDS_KEY]["narrator_age"]["utterance_id"] == segments[index - 1].utterance_id


@pytest.mark.asyncio
@pytest.mark.parametrize("earlier_state", ["legacy", "changed_age", "changed_subject", "deleted", "manual"])
async def test_retraction_uses_only_current_evidence_and_recovers_legacy_readings(
    client, test_db, fake_ai, monkeypatch, earlier_state,
):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    session_id = await _session(client, project_id)
    first = await _narrate(client, test_db, session_id, "我七十岁。")
    later = await _narrate(client, test_db, session_id, "我也是七十岁。")
    fake_ai.payload = {"age": 70}
    for segment in (first, later):
        await _run(test_db, segment.utterance_id)
    if earlier_state == "legacy":
        # Existing deployments did not retain per-recording extraction results.
        message = await test_db.get(MemoirChatMessage, first.message_id)
        message.metadata_json = {}
        await test_db.commit()
    elif earlier_state.startswith("changed_"):
        # The previous reading is stale; the corrected version has not run its profile job yet.
        await _new_version(test_db, first.utterance_id, (
            "我七十一岁。" if earlier_state == "changed_age" else "我母亲七十岁。"
        ))
    elif earlier_state == "deleted":
        assert (await client.delete(f"/api/mobile/memoir/voice-transcripts/{first.utterance_id}")).status_code == 204
    else:
        assert (await client.patch(f"/api/mobile/memoir/projects/{project_id}", json={"narrator_age": 70})).status_code == 200

    async def extract(_system_prompt, user_prompt, **_kwargs):
        fake_ai.prompts.append(user_prompt)
        return json.dumps({"age": 71} if "叙述者：我七十一岁。" in user_prompt else (
            {"age": 70} if "叙述者：我七十岁。" in user_prompt else {}
        ))

    monkeypatch.setattr(fake_ai, "_call_llm", extract)
    await _new_version(test_db, later.utterance_id, "我母亲七十岁。")
    await narrator_profile.schedule_narrator_profile(
        test_db, utterance=await test_db.get(MemoirVoiceUtterance, later.utterance_id),
    )
    await test_db.commit()
    await _run(test_db, later.utterance_id)
    _, profile = await _profile(test_db, project_id)
    expected = {"legacy": 70, "changed_age": 71, "manual": 70}.get(earlier_state)
    assert profile.get("narrator_age") == expected
    if earlier_state in {"legacy", "changed_age"}:
        assert profile[projects.INFERRED_PROFILE_FIELDS_KEY]["narrator_age"]["utterance_id"] == first.utterance_id
    else:
        assert "narrator_age" not in profile.get(projects.INFERRED_PROFILE_FIELDS_KEY, {})


@pytest.mark.asyncio
async def test_retracting_latest_refusal_keeps_an_earlier_refusal(client, test_db, fake_ai):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    session_id = await _session(client, project_id)
    first = await _narrate(client, test_db, session_id, "我的年纪就不说了。")
    later = await _narrate(client, test_db, session_id, "我还是不想说年纪。")
    fake_ai.payload = {"skip": ["age"]}
    for segment in (first, later):
        await _run(test_db, segment.utterance_id)
    await _new_version(test_db, later.utterance_id, "我母亲的年纪就不说了。")
    await narrator_profile.schedule_narrator_profile(
        test_db, utterance=await test_db.get(MemoirVoiceUtterance, later.utterance_id),
    )
    await test_db.commit()
    fake_ai.payload = {}
    await _run(test_db, later.utterance_id)
    _, profile = await _profile(test_db, project_id)
    assert "narrator_age" not in projects.profile_items_to_ask(profile)
    assert profile[projects.SKIPPED_PROFILE_FIELDS_KEY]["narrator_age"] == first.utterance_id


@pytest.mark.asyncio
async def test_failed_legacy_recovery_does_not_clear_the_profile(client, test_db, fake_ai, monkeypatch):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    session_id = await _session(client, project_id)
    first = await _narrate(client, test_db, session_id, "我七十岁。")
    later = await _narrate(client, test_db, session_id, "我也是七十岁。")
    fake_ai.payload = {"age": 70}
    for segment in (first, later):
        await _run(test_db, segment.utterance_id)
    message = await test_db.get(MemoirChatMessage, first.message_id)
    message.metadata_json = {}
    await test_db.commit()

    async def fail_recovery(_system_prompt, user_prompt, **_kwargs):
        if "叙述者：我七十岁。" in user_prompt:
            raise RuntimeError("provider down")
        return "{}"

    monkeypatch.setattr(fake_ai, "_call_llm", fail_recovery)
    version = await _new_version(test_db, later.utterance_id, "我母亲七十岁。")
    with pytest.raises(RuntimeError, match="provider down"):
        await narrator_profile.fill_narrator_profile(
            test_db, project=await test_db.get(Project, project_id),
            utterance=await narrator_profile._load_utterance(test_db, later.utterance_id),
            transcript_version_id=version, previous_message_id=None,
        )
    await test_db.rollback()
    _, profile = await _profile(test_db, project_id)
    assert profile["narrator_age"] == 70


@pytest.mark.asyncio
@pytest.mark.parametrize("manually_saved", [False, True])
async def test_deleting_a_source_segment_keeps_heard_values_correctable_but_saved_values_protected(
    client, test_db, fake_ai, manually_saved,
):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    first = await _narrate(client, test_db, session_id, "我叫王秀音。")
    fake_ai.payload = {"name": "王秀音"}
    await _run(test_db, first.utterance_id)
    if manually_saved:
        saved = await client.patch(f"/api/mobile/memoir/projects/{project_id}", json={"elder_name": "王秀音"})
        assert saved.status_code == 200

    deleted = await client.delete(f"/api/mobile/memoir/voice-transcripts/{first.utterance_id}")
    assert deleted.status_code == 204
    assert await test_db.scalar(select(MemoirVoiceUtterance.id).where(
        MemoirVoiceUtterance.id == first.utterance_id,
    )) is None

    later = await _narrate(client, test_db, session_id, "我叫王秀英，英雄的英。")
    fake_ai.payload = {"name": "王秀英"}
    await _run(test_db, later.utterance_id)
    project, profile = await _profile(test_db, project_id)
    expected_name = "王秀音" if manually_saved else "王秀英"
    assert profile["narrator_name"] == expected_name
    assert project.title == f"{expected_name}的回忆录"
    inferred = profile[projects.INFERRED_PROFILE_FIELDS_KEY]
    if manually_saved:
        assert "narrator_name" not in inferred
    else:
        assert inferred["narrator_name"]["utterance_id"] == later.utterance_id
        assert inferred["narrator_name"]["transcript_version_id"] == later.transcript_version_id


@pytest.mark.asyncio
async def test_asking_stops_after_two_asks_or_a_refusal_but_late_answers_are_kept(client, test_db, fake_ai):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    profile = {"narrator_name": "王秀英"}
    await _ask(test_db, session_id, "您是哪年生的？", offered="narrator_age")
    await _narrate(client, test_db, session_id, "那时候我们家在河边，有一棵大槐树。")
    session = await test_db.get(MemoirVoiceSession, session_id)
    await test_db.refresh(session, ["messages"])
    # Right after the narrator speaks, the item just asked waits for the reading of their answer.
    assert narrator_profile.profile_questions_to_ask(profile, list(session.messages)) == (
        "narrator_origin", "narrator_gender",
    )

    # A line offered age but asking about something else does not count.
    await _ask(test_db, session_id, "那棵槐树后来还在吗？", offered="narrator_age", asked=False)
    await _ask(test_db, session_id, "王阿姨，您今年多大年纪了？", offered="narrator_age")
    await test_db.refresh(session, ["messages"])
    assert narrator_profile.profile_questions_to_ask(profile, list(session.messages)) == (
        "narrator_origin", "narrator_gender",
    )

    # After the second ask the answer still comes, much later.
    late = await _narrate(client, test_db, session_id, "我是五二年生的，属龙。")
    fake_ai.payload = {"age": TODAY.year - 1952}
    await _run(test_db, late.utterance_id)
    _project, saved = await _profile(test_db, project_id)
    assert saved["narrator_age"] == TODAY.year - 1952

    refusal = await _narrate(client, test_db, session_id, "老家就不说了吧。")
    fake_ai.payload = {"skip": ["origin"]}
    await _run(test_db, refusal.utterance_id)
    _project, saved = await _profile(test_db, project_id)
    assert saved[projects.SKIPPED_PROFILE_FIELDS_KEY] == {"narrator_origin": refusal.utterance_id}
    assert projects.profile_items_to_ask(saved) == ["narrator_name", "narrator_gender"]


@pytest.mark.asyncio
async def test_a_reply_counts_as_an_ask_only_when_the_check_says_it_asks_for_the_offered_item(fake_ai):
    fake_ai.payload = {"field": "narrator_age"}
    assert await narrator_profile.asked_profile_field(
        ("narrator_name", "narrator_age", "narrator_origin"), "王阿姨，您今年多少岁了？", ai_model=None,
    ) == "narrator_age"
    assert "他本人现在的年纪，或者哪年出生" in fake_ai.prompts[-1]
    fake_ai.payload = {"field": None}
    assert await narrator_profile.asked_profile_field(("narrator_age",), "您是哪年参加工作的？", ai_model=None) is None

    # A classifier cannot count a field whose two asks are already exhausted.
    fake_ai.payload = {"field": "narrator_age"}
    assert await narrator_profile.asked_profile_field(("narrator_origin",), "您老家是哪里的？", ai_model=None) is None

    # No question, or nothing offered: no check call.
    calls = len(fake_ai.prompts)
    assert await narrator_profile.asked_profile_field(("narrator_origin",), "后来呢，您接着说。", ai_model=None) is None
    assert await narrator_profile.asked_profile_field((), "您老家是哪里的？", ai_model=None) is None
    assert len(fake_ai.prompts) == calls


@pytest.mark.asyncio
async def test_when_the_check_fails_plain_wording_decides(monkeypatch):
    async def fail(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(narrator_profile, "generate_structured_with_client", fail)
    fields = ("narrator_name", "narrator_age", "narrator_origin")
    assert await narrator_profile.asked_profile_field(fields, "您今年多少岁了？", ai_model=None) == "narrator_age"
    assert await narrator_profile.asked_profile_field(fields, "您是哪年生的呀？", ai_model=None) == "narrator_age"
    assert await narrator_profile.asked_profile_field(fields, "您是哪年参加工作的？", ai_model=None) is None
    assert await narrator_profile.asked_profile_field(fields, "那年您多大，还记得吗？", ai_model=None) is None
    assert await narrator_profile.asked_profile_field(fields, "您怎么称呼？", ai_model=None) == "narrator_name"
    gender = ("narrator_gender",)
    assert await narrator_profile.asked_profile_field(gender, "我该叫您叔叔还是阿姨呀？", ai_model=None) == "narrator_gender"
    assert await narrator_profile.asked_profile_field(gender, "那时候女的也要下地干活吗？", ai_model=None) is None


def test_pending_ask_checks_cannot_exceed_two_asks_for_any_candidate():
    fields = projects.ASKED_PROFILE_FIELDS
    replies = [
        SimpleNamespace(role="assistant", guidance_intent=None, metadata_json={"profile_questions": list(fields)})
        for _ in range(2)
    ]
    assert narrator_profile.profile_questions_to_ask({}, replies) == ()
    for reply in replies:
        reply.metadata_json["profile_asked"] = "narrator_age"
    assert narrator_profile.profile_asks(replies) == {"narrator_age": 2}
    assert narrator_profile.profile_questions_to_ask({}, replies) == ("narrator_name", "narrator_origin")
    declined = {"declined_in": {field: "segment" for field in fields}}
    assert narrator_profile.profile_questions_to_ask(declined, replies) == ()


def test_gender_is_asked_last_and_only_while_unknown():
    known = {"narrator_name": "老王", "narrator_age": 70, "narrator_origin": "山东"}
    assert narrator_profile.profile_questions_to_ask({"narrator_gender": "unspecified"}, []) == (
        "narrator_name", "narrator_age",
    )
    assert narrator_profile.profile_questions_to_ask({**known, "narrator_gender": "unspecified"}, []) == (
        "narrator_gender",
    )
    # 于大姐 or a gender saved on the project page is never asked; neither is a declined one.
    assert narrator_profile.profile_questions_to_ask({**known, "narrator_gender": "female"}, []) == ()
    declined = {**known, projects.SKIPPED_PROFILE_FIELDS_KEY: {"narrator_gender": "segment"}}
    assert narrator_profile.profile_questions_to_ask(declined, []) == ()
    # Asked once; if the narrator tells a story instead, gender is left to what they say later.
    asked = SimpleNamespace(
        role=MemoirChatMessageRole.ASSISTANT.value, content_text="我该叫您叔叔还是阿姨呀？",
        guidance_intent=None,
        metadata_json={"profile_questions": ["narrator_gender"], "profile_asked": "narrator_gender"},
    )
    story = SimpleNamespace(role=MemoirChatMessageRole.USER.value, content_text="那年我们去赶集……", metadata_json={})
    went_on = SimpleNamespace(
        role=MemoirChatMessageRole.ASSISTANT.value, content_text="后来呢？",
        guidance_intent=None,
        metadata_json={"profile_questions": [], "profile_asked": None},
    )
    finished = SimpleNamespace(role=MemoirChatMessageRole.USER.value, content_text="就这些。", metadata_json={})
    unknown = {**known, "narrator_gender": "unspecified"}
    assert narrator_profile.profile_questions_to_ask(unknown, [asked, story, went_on, finished]) == ()

    narrator = SimpleNamespace(
        id="m1", role=MemoirChatMessageRole.USER.value, content_text="那年我们去赶集。", turn_index=1, created_at=None,
        metadata_json={},
    )
    book = InterviewBookContext(
        chapters=(), last_chapter_title="", topics=(), anchor_topic=None, profile_questions=("narrator_gender",),
    )
    prompt = _prompt("s1", [narrator], book)
    assert "这次要问的资料：他是男是女，比如“冒昧问一句，我该叫您叔叔还是阿姨呀？”" in prompt
    assert "除了问性别，不要用“是……还是……”的二选一问法" in prompt


@pytest.mark.asyncio
async def test_a_gender_answer_sets_gender_but_not_the_name_and_can_be_declined(client, test_db, fake_ai):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    named = await _narrate(client, test_db, session_id, "我叫王建华。")
    fake_ai.payload = {"name": "王建华"}
    await _run(test_db, named.utterance_id)
    await _ask(test_db, session_id, "冒昧问一句，我该叫您叔叔还是阿姨呀？", offered="narrator_gender")
    answer = await _narrate(client, test_db, session_id, "叫我王阿姨就行。")
    fake_ai.payload = {"gender": "female"}
    await _run(test_db, answer.utterance_id)
    assert "只是在说性别，name 填 null" in fake_ai.prompts[-1]
    project, profile = await _profile(test_db, project_id)
    assert (profile["narrator_name"], profile["narrator_gender"]) == ("王建华", "female")
    assert project.title == "王建华的回忆录"
    assert profile[projects.INFERRED_PROFILE_FIELDS_KEY]["narrator_gender"]["utterance_id"] == answer.utterance_id

    other_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    other = await _session(client, other_id)
    refusal = await _narrate(client, test_db, other, "这个就不说了吧。")
    fake_ai.payload = {"skip": ["gender"]}
    await _run(test_db, refusal.utterance_id)
    _project, profile = await _profile(test_db, other_id)
    assert profile[projects.SKIPPED_PROFILE_FIELDS_KEY] == {"narrator_gender": refusal.utterance_id}
    assert "narrator_gender" not in projects.profile_items_to_ask(profile)


@pytest.mark.asyncio
async def test_reply_can_ask_age_after_a_name_answer_while_the_profile_job_is_pending(client, test_db, fake_ai):
    from tests.test_memoir_voice_assistant_turn_stream import _run_stream_turn

    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    await _ask(test_db, session_id, "您怎么称呼？", offered="narrator_name")
    segment = await _narrate(client, test_db, session_id, "你叫我余大姐吧，年年有余的余。")
    _project, profile = await _profile(test_db, project_id)
    assert not profile["narrator_name"]
    assert (await _profile_job(test_db, segment.utterance_id)).status == "pending"

    fake_ai.payload = {"field": "narrator_age"}
    user_id = (await client.get("/api/auth/me")).json()["id"]
    _, ai_client, _, completed = await _run_stream_turn(
        test_db, voice_session_id=session_id, user_id=user_id, chunks=["余大姐，您是哪年出生的？"],
    )
    await narrator_profile.wait_for_profile_ask_checks()
    assert "这次要问的资料：他的年纪" in ai_client.calls[0]["user_prompt"]
    test_db.expire_all()
    reply = await test_db.get(MemoirChatMessage, completed["message_id"])
    assert reply.metadata_json["profile_asked"] == "narrator_age"
    assert narrator_profile.profile_asks([reply]) == {"narrator_age": 1}


@pytest.mark.asyncio
async def test_the_first_two_spoken_ai_sessions_ask_but_later_ai_sessions_still_collect(client, test_db, fake_ai):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    empty = await _session(client, project_id)
    human = await _session(client, project_id, interview_type="human_interview")
    human_segment = await _narrate(client, test_db, human, "我叫王秀英。")
    _project, profile = await _profile(test_db, project_id)
    assert projects.PROFILE_SESSION_KEY not in profile
    assert await _profile_job(test_db, human_segment.utterance_id) is None

    # An old book does not start asking, but can fill its profile from later AI narration.
    first = await _narrate(client, test_db, empty, "我叫王秀英。")
    _project, profile = await _profile(test_db, project_id)
    assert projects.PROFILE_SESSION_KEY not in profile
    assert await _profile_job(test_db, first.utterance_id) is not None
    session = await test_db.get(MemoirVoiceSession, empty)
    assert not await narrator_profile.is_profile_session(test_db, session, await test_db.get(Project, project_id))

    # A fresh book: a session opened without speaking is skipped, the next one is pinned.
    created = await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})
    other_id = created.json()["id"]
    silent = await _session(client, other_id)
    spoken = await _session(client, other_id)
    session = await test_db.get(MemoirVoiceSession, silent)
    assert await narrator_profile.is_profile_session(test_db, session, await test_db.get(Project, other_id))
    named = await _narrate(client, test_db, spoken, "我叫李桂兰。")
    fake_ai.payload = {"name": "李桂兰"}
    await _run(test_db, named.utterance_id)
    _project, profile = await _profile(test_db, other_id)
    assert profile[projects.PROFILE_SESSION_KEY] == spoken
    await _ask(test_db, spoken, "您是哪年生的？", offered="narrator_age")
    await _ask(test_db, spoken, "李阿姨，您今年多大年纪了？", offered="narrator_age")

    # Reproduce 于大姐: she stops early, so the next session keeps asking, with the first session's asks counted.
    stopped = await client.post(f"/api/mobile/memoir/voice-sessions/{spoken}/stop", json={})
    assert stopped.status_code == 200
    later = await _session(client, other_id)
    session = await test_db.get(MemoirVoiceSession, later)
    await test_db.refresh(session, ["messages"])
    project = await test_db.get(Project, other_id)
    assert (await narrator_profile.profile_question_context_for(test_db, session, project)).questions == (
        "narrator_origin", "narrator_gender",
    )
    segment = await _narrate(client, test_db, later, "我小时候住在四川省成都市新都县。")
    assert await _profile_job(test_db, segment.utterance_id) is not None
    fake_ai.payload = {"origin": "四川省成都市新都县"}
    await _run(test_db, segment.utterance_id)
    project, profile = await _profile(test_db, other_id)
    assert profile["narrator_origin"] == "四川省成都市新都县"
    await test_db.refresh(session, ["messages"])
    assert (await narrator_profile.profile_question_context_for(test_db, session, project)).questions == ("narrator_gender",)

    # Once a third session has narration, asking is over; reading goes on.
    third = await _session(client, other_id)
    await _narrate(client, test_db, third, "后来我嫁到了绵阳。")
    for session_id in (later, third):
        session = await test_db.get(MemoirVoiceSession, session_id)
        assert not await narrator_profile.is_profile_session(test_db, session, project)


@pytest.mark.asyncio
async def test_a_value_saved_on_the_project_page_is_never_overwritten(client, test_db, fake_ai):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    first = await _narrate(client, test_db, session_id, "我叫王秀音。")
    fake_ai.payload = {"name": "王秀音"}
    await _run(test_db, first.utterance_id)
    saved = await client.patch(f"/api/mobile/memoir/projects/{project_id}", json={"elder_name": "秀英阿姨"})
    assert saved.status_code == 200

    later = await _narrate(client, test_db, session_id, "我叫王秀英。")
    fake_ai.payload = {"name": "王秀英"}
    await _run(test_db, later.utterance_id)
    _project, profile = await _profile(test_db, project_id)
    assert profile["narrator_name"] == "秀英阿姨"


@pytest.mark.asyncio
async def test_manual_save_overlapping_profile_reading_preserves_both_updates(client, test_db, fake_ai, monkeypatch):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    segment = await _narrate(client, test_db, session_id, "我叫王秀英，五二年生的，老家四川成都。")
    fake_ai.payload = {"name": "王秀英", "gender": "female", "age": 74, "origin": "四川成都"}
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
            job = await _profile_job(worker, segment.utterance_id)
            await narrator_profile.run_narrator_profile_job(worker, job)
        profile_completed.set()
        saved = await asyncio.wait_for(manual_save, timeout=5)
        assert saved.status_code == 200
    finally:
        profile_completed.set()
        if not manual_save.done():
            manual_save.cancel()
        await asyncio.gather(manual_save, return_exceptions=True)

    _project, profile = await _profile(test_db, project_id)
    assert profile["narrator_name"] == "秀英阿姨"
    assert (profile["narrator_gender"], profile["narrator_age"], profile["narrator_origin"]) == (
        "female", 74, "四川成都",
    )
    inferred = profile[projects.INFERRED_PROFILE_FIELDS_KEY]
    assert "narrator_name" not in inferred
    assert inferred["narrator_age"]["utterance_id"] == segment.utterance_id


def _prompt(
    session_id: str, messages: list[MemoirChatMessage], book: InterviewBookContext, *, returning: bool = False,
) -> str:
    session = SimpleNamespace(id=session_id, session_goal="open_narration", messages=messages)
    return build_stream_turn_prompt(
        session,
        has_previous_project_narration=returning,
        activation_context="",
        regenerate=False,
        progress_context=ProgressPromptContext(prompt_text=""),
        persona=InterviewerPersona(host_name="小余", voice_gender="female"),
        completion_prompt_context=None,
        book_context=book,
    )


def test_the_interviewer_asks_the_next_item_while_the_last_answer_is_still_being_read():
    asked = SimpleNamespace(
        id="m0", role=MemoirChatMessageRole.ASSISTANT.value, content_text="您怎么称呼？", turn_index=0, created_at=None,
        guidance_intent=None,
        metadata_json={"profile_questions": ["narrator_name"], "profile_asked": "narrator_name"},
    )
    narrator = SimpleNamespace(
        id="m1", role=MemoirChatMessageRole.USER.value, content_text="你叫我于大姐吧。", turn_index=1, created_at=None,
        metadata_json={},
    )
    # Code picks the item, so the reply cannot ask the hometown or follow the story instead; the hometown is
    # only the fallback when the narrator already gave their age along with the name.
    assert narrator_profile.profile_questions_to_ask({}, [asked]) == ("narrator_name", "narrator_age")
    questions = narrator_profile.profile_questions_to_ask({}, [asked, narrator])
    assert questions == ("narrator_age", "narrator_origin")
    book = InterviewBookContext(
        chapters=(("童年", 2),), last_chapter_title="童年", topics=(), anchor_topic=None, profile_questions=questions,
    )
    prompt = _prompt("s1", [asked, narrator], book)
    assert "这次要问的资料：他的年纪，比如“您是哪年生的？”" in prompt
    assert "就改问：他的老家，比如“您老家是哪里的？”（这一项也说了就不问）" in prompt
    assert "你叫我于大姐吧。" in prompt
    last = InterviewBookContext(
        chapters=(), last_chapter_title="", topics=(), anchor_topic=None, profile_questions=("narrator_origin",),
    )
    assert "或明确不想说，就不问资料" in _prompt("s1", [asked, narrator], last)

    # An ask the narrator did not answer leaves the line counted but not asking, so the item comes back.
    asked.metadata_json["profile_asked"] = None
    assert narrator_profile.profile_questions_to_ask({}, [asked, narrator]) == ("narrator_name", "narrator_age")

    done = InterviewBookContext(chapters=(), last_chapter_title="", topics=(), anchor_topic=None)
    assert "这次要问的资料：" not in _prompt("s1", [narrator], done)

    opening = InterviewBookContext(
        chapters=(), last_chapter_title="", topics=(), anchor_topic=None, profile_questions=("narrator_name",),
    )
    assert "这次要问的资料：怎么称呼他" in _prompt("s1", [], opening)


@pytest.mark.asyncio
async def test_an_opening_ask_left_unanswered_still_counts_in_later_sessions(client, test_db, fake_ai):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    first = await _session(client, project_id)
    await _ask(test_db, first, "您是哪年生的？", offered="narrator_age")
    segment = await _narrate(client, test_db, first, "我叫李桂兰，老家湖南长沙，我年纪就先不说了。")
    fake_ai.payload = {"name": "李桂兰", "origin": "湖南长沙"}
    await _run(test_db, segment.utterance_id)
    # The narrator leaves the next two sessions right after 小余's opening question.
    for line, item in (
        ("李阿姨，欢迎回来。您是哪年生的？", "narrator_age"),
        ("冒昧问一句，我该叫您叔叔还是阿姨呀？", "narrator_gender"),
    ):
        silent = await _session(client, project_id)
        await _ask(test_db, silent, line, offered=item)
    latest = await test_db.get(MemoirVoiceSession, await _session(client, project_id))
    await test_db.refresh(latest, ["messages"])
    project = await test_db.get(Project, project_id)
    assert await narrator_profile.is_profile_session(test_db, latest, project)
    assert (await narrator_profile.profile_question_context_for(test_db, latest, project)).questions == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["叫我余大姐吧。", "叫我余大姐，今年七十岁。", "叫我余大姐，年纪就不说了。"])
async def test_pending_profile_answers_reach_the_next_opening_and_later_turns(client, test_db, fake_ai, answer):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    first = await _session(client, project_id)
    question = "您怎么称呼？"
    await _ask(test_db, first, question, offered="narrator_name")
    segment = await _narrate(client, test_db, first, answer)
    assert (await _profile_job(test_db, segment.utterance_id)).status == "pending"
    assert (await client.post(f"/api/mobile/memoir/voice-sessions/{first}/stop", json={})).status_code == 200
    latest = await test_db.get(MemoirVoiceSession, await _session(client, project_id))
    await test_db.refresh(latest, ["messages"])
    book = await load_interview_book_context(test_db, voice_session=latest, snapshot=None)
    assert book.profile_questions == ("narrator_age", "narrator_origin")
    opening = _prompt(latest.id, latest.messages, book, returning=True)
    assert f"AI：{question}\n叙述者：{answer}" in opening
    assert "或明确不想说，就改问：他的老家" in opening

    # The original reading stays pending even after the next session has another exchange.
    await _ask(test_db, latest.id, "您是哪年生的？", offered="narrator_age")
    await _narrate(client, test_db, latest.id, "一九五六年。")
    await test_db.refresh(latest, ["messages"])
    assert (await _profile_job(test_db, segment.utterance_id)).status == "pending"
    book = await load_interview_book_context(test_db, voice_session=latest, snapshot=None)
    followup = _prompt(latest.id, latest.messages, book, returning=True)
    assert f"AI：{question}\n叙述者：{answer}" in followup
    assert "一九五六年。" in followup
    assert "或明确不想说，就改问：他的老家" in followup


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [(1,), (1, 2)])
@pytest.mark.parametrize("change_topic", [False, True])
async def test_no_answer_prompts_preserve_the_question_until_a_real_topic_change(
    client, test_db, fake_ai, attempts, change_topic,
):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    first = await _session(client, project_id)
    original = await _ask(test_db, first, "您怎么称呼？", offered="narrator_name")
    for attempt in attempts:
        prompt = await client.post(
            f"/api/mobile/memoir/voice-sessions/{first}/no-answer-prompt", json={"attempt": attempt},
        )
        assert prompt.status_code == 200
    previous = await _ask(test_db, first, "您小时候最喜欢做什么？") if change_topic else original
    answer = "最喜欢玩捉迷藏。" if change_topic else "叫我余大姐，今年七十岁。"
    segment = await _narrate(client, test_db, first, answer)
    job = await _profile_job(test_db, segment.utterance_id)
    assert job.status == "pending"
    assert job.payload_json["previous_message_id"] == previous.id

    session = await test_db.get(MemoirVoiceSession, first)
    await test_db.refresh(session, ["messages"])
    expected = ("narrator_name", "narrator_age") if change_topic else ("narrator_age", "narrator_origin")
    assert narrator_profile.profile_questions_to_ask({}, list(session.messages)) == expected
    assert narrator_profile.profile_asks(list(session.messages)) == {"narrator_name": 1}
    assert (await client.post(f"/api/mobile/memoir/voice-sessions/{first}/stop", json={})).status_code == 200

    latest = await test_db.get(MemoirVoiceSession, await _session(client, project_id))
    await test_db.refresh(latest, ["messages"])
    book = await load_interview_book_context(test_db, voice_session=latest, snapshot=None)
    assert book.profile_questions == expected
    if change_topic:
        assert book.profile_answers == ()
    else:
        assert book.profile_answers == (narrator_profile.ProfileAnswer(original.content_text, answer),)
        assert f"AI：{original.content_text}\n叙述者：{answer}" in _prompt(
            latest.id, latest.messages, book, returning=True,
        )


def test_the_second_session_opens_by_asking_what_the_first_left():
    book = InterviewBookContext(
        chapters=(("新都县的家", 4),), last_chapter_title="新都县的家", topics=(), anchor_topic=None,
        narrator="于大姐，女，家乡四川省成都市新都县", profile_questions=("narrator_age", "narrator_gender"),
    )
    prompt = build_stream_turn_prompt(
        SimpleNamespace(id="s2", session_goal="open_narration", messages=[]),
        has_previous_project_narration=True,
        activation_context="",
        regenerate=False,
        progress_context=ProgressPromptContext(prompt_text=""),
        persona=InterviewerPersona(host_name="小余", voice_gender="female"),
        completion_prompt_context=None,
        book_context=book,
    )
    assert "这次要问的资料：他的年纪，比如“您是哪年生的？”" in prompt
    assert "或明确不想说，就改问：他是男是女" in prompt
    assert "接着上次" not in prompt


def test_profile_history_keeps_complete_exchanges_without_crossing_sessions():
    def message(session, role, text, *, field=None, intent=None):
        return SimpleNamespace(
            id=text, voice_session_id=session, role=role, content_text=text,
            guidance_intent=intent,
            metadata_json={"profile_asked": field},
        )

    history = [
        message("first", "assistant", "您怎么称呼？", field="narrator_name"),
        message("silent", "assistant", "您是哪年生的？", field="narrator_age"),
        message("first", "assistant", "您可以接着刚才的问题慢慢说。", intent="no_answer_prompt"),
        message("first", "user", "叫我余大姐。"),
        message("first", "user", "今年七十岁。"),
    ]
    # Later story dialogue must not displace the profile answer or attach to its question.
    for turn in range(8):
        history.extend([
            message("first", "assistant", f"后来呢？{turn}"),
            message("first", "user", f"往事{turn}"),
        ])
    assert narrator_profile._previous_profile_answers(history) == (
        narrator_profile.ProfileAnswer("您怎么称呼？", "叫我余大姐。\n今年七十岁。"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("separate,turns", [(False, 1), (False, 7), (True, 7)])
async def test_pending_profile_answer_survives_story_dialogue(client, test_db, fake_ai, separate, turns):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    first = await _session(client, project_id)
    question = "您怎么称呼？"
    answer = "叫我柳大姐，今年七十岁。"
    await _ask(test_db, first, question, offered="narrator_name")
    segment = await _narrate(client, test_db, first, answer)
    session_id = await _session(client, project_id) if separate else first
    for index in range(turns):
        await _ask(test_db, session_id, "嗯，您接着讲。")
        await _narrate(client, test_db, session_id, f"夏天我们常在村里的树下玩。第{index}段往事讲完了。")
    session = await test_db.get(MemoirVoiceSession, session_id)
    await test_db.refresh(session, ["messages"])
    book = await load_interview_book_context(test_db, voice_session=session, snapshot=None)
    assert (await _profile_job(test_db, segment.utterance_id)).status == "pending"
    assert book.profile_questions == ("narrator_name", "narrator_age")
    assert answer in _prompt(session_id, session.messages, book, returning=separate)


@pytest.mark.asyncio
@pytest.mark.parametrize("all_exhausted", [False, True])
async def test_silent_openings_use_only_remaining_profile_candidates(client, test_db, fake_ai, all_exhausted):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    asks = [("narrator_name", "您怎么称呼？")] * 2 + [("narrator_origin", "您老家是哪里的？")] * 2
    if all_exhausted:
        asks += [("narrator_age", "您是哪年生的？")] * 2 + [("narrator_gender", "我该叫您叔叔还是阿姨？")]
    for field, question in asks:
        await _ask(test_db, await _session(client, project_id), question, offered=field)
    session = await test_db.get(MemoirVoiceSession, await _session(client, project_id))
    await test_db.refresh(session, ["messages"])
    book = await load_interview_book_context(test_db, voice_session=session, snapshot=None)
    assert not await project_has_previous_narration(test_db, session)
    assert book.profile_questions == (() if all_exhausted else ("narrator_age", "narrator_gender"))
    prompt = _prompt(session.id, session.messages, book)
    # The prompt is the boundary passed to the model: capped fields must not reappear as opening examples.
    assert "老家在哪" not in prompt and "小时候住在什么地方" not in prompt
    if all_exhausted:
        assert "这次要问的资料：" not in prompt
    else:
        assert "这次要问的资料：他的年纪" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["free_narration", "guided_interview"])
async def test_returning_narrator_is_recognized_across_legacy_ai_modes(client, test_db, fake_ai, mode):
    project_id = (await client.post("/api/mobile/memoir/projects", json={"title": "我的回忆录"})).json()["id"]
    first = await _session(client, project_id)
    await _ask(test_db, first, "您怎么称呼？", offered="narrator_name")
    answer = "叫我柳大姐，老家是成都。"
    await _narrate(client, test_db, first, answer)
    session = await test_db.get(MemoirVoiceSession, await _session(client, project_id, guidance_mode=mode))
    await test_db.refresh(session, ["messages"])
    book = await load_interview_book_context(test_db, voice_session=session, snapshot=None)
    returning = await project_has_previous_narration(test_db, session)
    assert returning
    assert book.profile_questions == ("narrator_age", "narrator_origin")
    prompt = _prompt(session.id, session.messages, book, returning=returning)
    assert answer in prompt
    assert "这次要问的资料：他的年纪" in prompt


@pytest.fixture
def job_handlers():
    from app.services.memoir.editing_job_handlers import register_memoir_editing_job_handlers

    register_memoir_editing_job_handlers()


@pytest.mark.asyncio
async def test_a_version_corrected_while_reading_is_read_by_the_worker_afterwards(
    client, test_db, fake_ai, job_handlers,
):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    segment = await _narrate(client, test_db, session_id, "我叫王秀音。")
    corrected: list[str] = []

    async def correct_during_call(_system_prompt, _user_prompt, **_kwargs):
        if not corrected:
            async with AsyncSession(test_db.bind, expire_on_commit=False) as other:
                corrected.append(await _new_version(other, segment.utterance_id, "我叫王秀英。"))
                await narrator_profile.schedule_narrator_profile(
                    other, utterance=await other.get(MemoirVoiceUtterance, segment.utterance_id),
                )
                await other.commit()
            return json.dumps({"name": "王秀音"}, ensure_ascii=False)
        return json.dumps({"name": "王秀英"}, ensure_ascii=False)

    fake_ai._call_llm = correct_during_call
    job = await _profile_job(test_db, segment.utterance_id)
    await run_test_editing_job(test_db, job.id)
    await test_db.refresh(job)
    assert job.status == "pending"
    assert job.payload_json["transcript_version_id"] == corrected[0]
    _project, profile = await _profile(test_db, project_id)
    assert not profile.get("narrator_name")

    await run_test_editing_job(test_db, job.id)
    await test_db.refresh(job)
    assert job.status == "succeeded"
    _project, profile = await _profile(test_db, project_id)
    assert profile["narrator_name"] == "王秀英"


@pytest.mark.asyncio
async def test_a_failed_reading_is_retried_by_the_worker_then_left_failed(
    client, test_db, fake_ai, job_handlers,
):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    segment = await _narrate(client, test_db, session_id, "我叫王秀英。")
    failures = 0

    async def fail_once(_system_prompt, _user_prompt, **_kwargs):
        nonlocal failures
        failures += 1
        if failures == 1:
            raise RuntimeError("provider down")
        return json.dumps({"name": "王秀英"}, ensure_ascii=False)

    fake_ai._call_llm = fail_once
    job = await _profile_job(test_db, segment.utterance_id)
    await run_test_editing_job(test_db, job.id)
    await test_db.refresh(job)
    assert job.status == "pending"
    assert job.result_json["failures"] == 1
    await run_test_editing_job(test_db, job.id)
    _project, profile = await _profile(test_db, project_id)
    assert profile["narrator_name"] == "王秀英"

    # A segment that keeps failing is tried a bounded number of times.
    later = await _narrate(client, test_db, session_id, "我老家在四川。")

    async def always_fail(*_args, **_kwargs):
        raise RuntimeError("provider down")

    fake_ai._call_llm = always_fail
    job = await _profile_job(test_db, later.utterance_id)
    for _ in narrator_profile.RETRY_DELAYS:
        await run_test_editing_job(test_db, job.id)
        await test_db.refresh(job)
        assert job.status == "pending"
    await run_test_editing_job(test_db, job.id)
    await test_db.refresh(job)
    assert job.status == "failed"

    # When the transcript is corrected during the last failing try, the new version still runs.
    last = await _narrate(client, test_db, session_id, "我是五二年生的。")
    corrected: list[str] = []

    async def fail_while_corrected(*_args, **_kwargs):
        if not corrected:
            async with AsyncSession(test_db.bind, expire_on_commit=False) as other:
                corrected.append(await _new_version(other, last.utterance_id, "我是一九五二年生的。"))
                await narrator_profile.schedule_narrator_profile(
                    other, utterance=await other.get(MemoirVoiceUtterance, last.utterance_id),
                )
                await other.commit()
            raise RuntimeError("provider down")
        return json.dumps({"age": 74}, ensure_ascii=False)

    job = await _profile_job(test_db, last.utterance_id)
    job.result_json = {"failures": len(narrator_profile.RETRY_DELAYS)}
    await test_db.commit()
    fake_ai._call_llm = fail_while_corrected
    await run_test_editing_job(test_db, job.id)
    await test_db.refresh(job)
    assert job.status == "pending"
    assert job.payload_json["transcript_version_id"] == corrected[0]
    await run_test_editing_job(test_db, job.id)
    _project, profile = await _profile(test_db, project_id)
    assert profile["narrator_age"] == 74


@pytest.mark.asyncio
async def test_a_book_whose_profile_was_all_saved_is_not_read(client, test_db, fake_ai):
    project_id = await ensure_mobile_memoir_project(client, title="外婆的故事")
    saved = await client.patch(f"/api/mobile/memoir/projects/{project_id}", json={
        "elder_name": "李桂兰", "narrator_gender": "female", "narrator_age": 80, "narrator_origin": "湖南长沙",
    })
    assert saved.status_code == 200
    session_id = await _session(client, project_id)
    segment = await _narrate(client, test_db, session_id, "我小时候住在河边。")
    _project, profile = await _profile(test_db, project_id)
    assert profile[projects.PROFILE_SESSION_KEY] == session_id
    assert await _profile_job(test_db, segment.utterance_id) is None


@pytest.mark.asyncio
async def test_people_are_read_from_the_corrected_version_and_folded_into_the_roster(
    client, test_db, fake_ai, job_handlers,
):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    first = await _narrate(client, test_db, session_id, "我二舅叫李建国。")
    second = await _narrate(client, test_db, session_id, "建国舅带我第一次进城。")
    readings = iter([
        {"people": [{"ref": "new", "call": "二舅", "name": "李建国", "relation": "我妈的弟弟"}]},
        {"people": [{"ref": "P1", "call": "他", "note": "带我第一次进城"}]},
    ])

    async def answer(_system_prompt, user_prompt, **_kwargs):
        fake_ai.prompts.append(user_prompt)
        return json.dumps(next(readings) if "记住书里出现的人" in user_prompt else {}, ensure_ascii=False)

    fake_ai._call_llm = answer
    for segment, text in ((first, "我二舅叫李建国，是我妈的弟弟。"), (second, "建国舅带我第一次进城。")):
        # Only a corrected or edited version is read for people.
        await _new_version(test_db, segment.utterance_id, text)
        await narrator_profile.schedule_narrator_profile(
            test_db, utterance=await test_db.get(MemoirVoiceUtterance, segment.utterance_id),
        )
        await test_db.commit()
        await run_test_editing_job(test_db, (await _profile_job(test_db, segment.utterance_id)).id)

    assert "P1 二舅｜名字：李建国" in fake_ai.prompts[-1]
    people = await narration_people.load_people(test_db, project_id)
    assert [person.line for person in people.values()] == ["二舅（李建国），我妈的弟弟：带我第一次进城"]
