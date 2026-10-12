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
from app.services.memoir import narrator_profile, projects, voice_transcript_correction
from app.services.memoir.interview_book_context import InterviewBookContext
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
    assert "叙述者：我叫王秀英。" in fake_ai.prompts[-1]
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
async def test_asking_stops_after_two_asks_or_a_refusal_but_late_answers_are_kept(client, test_db, fake_ai):
    project_id = await ensure_mobile_memoir_project(client, title="我的回忆录")
    session_id = await _session(client, project_id)
    profile = {"narrator_name": "王秀英"}
    await _ask(test_db, session_id, "您是哪年生的？", offered="narrator_age")
    await _narrate(client, test_db, session_id, "那时候我们家在河边，有一棵大槐树。")
    session = await test_db.get(MemoirVoiceSession, session_id)
    await test_db.refresh(session, ["messages"])
    assert narrator_profile.profile_questions_to_ask(profile, list(session.messages)) == ("narrator_age", "narrator_origin")

    # A line offered age but asking about something else does not count.
    await _ask(test_db, session_id, "那棵槐树后来还在吗？", offered="narrator_age", asked=False)
    await _ask(test_db, session_id, "王阿姨，您今年多大年纪了？", offered="narrator_age")
    await test_db.refresh(session, ["messages"])
    assert narrator_profile.profile_questions_to_ask(profile, list(session.messages)) == ("narrator_origin",)

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
    assert projects.profile_items_to_ask(saved) == ["narrator_name"]


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


def test_pending_ask_checks_cannot_exceed_two_asks_for_any_candidate():
    fields = ("narrator_name", "narrator_age", "narrator_origin")
    replies = [SimpleNamespace(role="assistant", metadata_json={"profile_questions": list(fields)}) for _ in range(2)]
    assert narrator_profile.profile_questions_to_ask({}, replies) == ()
    for reply in replies:
        reply.metadata_json["profile_asked"] = "narrator_age"
    assert narrator_profile.profile_asks(replies) == {"narrator_age": 2}
    assert narrator_profile.profile_questions_to_ask({}, replies) == ("narrator_name", "narrator_origin")
    declined = {"declined_in": {field: "segment" for field in fields}}
    assert narrator_profile.profile_questions_to_ask(declined, replies) == ()


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
    assert "如果年纪仍没说，就接着问年纪" in ai_client.calls[0]["user_prompt"]
    test_db.expire_all()
    reply = await test_db.get(MemoirChatMessage, completed["message_id"])
    assert reply.metadata_json["profile_asked"] == "narrator_age"
    assert narrator_profile.profile_asks([reply]) == {"narrator_age": 1}


@pytest.mark.asyncio
async def test_only_the_first_spoken_ai_session_asks_but_later_ai_sessions_still_collect(client, test_db, fake_ai):
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
    await _narrate(client, test_db, spoken, "我叫李桂兰。")
    _project, profile = await _profile(test_db, other_id)
    assert profile[projects.PROFILE_SESSION_KEY] == spoken
    assert not await narrator_profile.is_profile_session(test_db, session, await test_db.get(Project, other_id))

    # Reproduce the device test: stop after answering the name, then give the hometown next session.
    stopped = await client.post(f"/api/mobile/memoir/voice-sessions/{spoken}/stop", json={})
    assert stopped.status_code == 200
    later = await _session(client, other_id)
    segment = await _narrate(client, test_db, later, "我小时候住在四川省成都市新都县。")
    assert await _profile_job(test_db, segment.utterance_id) is not None
    fake_ai.payload = {"origin": "四川省成都市新都县"}
    await _run(test_db, segment.utterance_id)
    project, profile = await _profile(test_db, other_id)
    assert profile["narrator_origin"] == "四川省成都市新都县"
    session = await test_db.get(MemoirVoiceSession, later)
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


def _prompt(session_id: str, messages: list[MemoirChatMessage], book: InterviewBookContext) -> str:
    session = SimpleNamespace(id=session_id, session_goal="open_narration", messages=messages)
    return build_stream_turn_prompt(
        session,
        has_previous_project_narration=False,
        activation_context="",
        regenerate=False,
        progress_context=ProgressPromptContext(prompt_text=""),
        persona=InterviewerPersona(host_name="小余", voice_gender="female"),
        completion_prompt_context=None,
        book_context=book,
    )


def test_the_interviewer_can_advance_past_a_recent_answer_even_before_it_is_saved():
    narrator = SimpleNamespace(
        id="m1", role=MemoirChatMessageRole.USER.value, content_text="我叫王秀英。", turn_index=1, created_at=None,
        metadata_json={},
    )
    book = InterviewBookContext(
        chapters=(("童年", 2),), last_chapter_title="童年", topics=(), anchor_topic=None,
        profile_questions=narrator_profile.profile_questions_to_ask({}, []),
    )
    prompt = _prompt("s1", [narrator], book)
    assert "他的年纪" in prompt
    assert "您是哪年生的？" in prompt
    assert "如果年纪仍没说，就接着问年纪" in prompt
    assert "我叫王秀英。" in prompt
    assert "最多问一项" in prompt

    capped = InterviewBookContext(
        chapters=(), last_chapter_title="", topics=(), anchor_topic=None,
        profile_questions=("narrator_name", "narrator_origin"),
    )
    assert "接着问年纪" not in _prompt("s1", [narrator], capped)
    assert "您是哪年生的？" not in _prompt("s1", [narrator], capped)

    done = InterviewBookContext(chapters=(), last_chapter_title="", topics=(), anchor_topic=None)
    assert "还可以问的资料" not in _prompt("s1", [narrator], done)

    opening = InterviewBookContext(
        chapters=(), last_chapter_title="", topics=(), anchor_topic=None, profile_questions=("narrator_name",),
    )
    assert "再问一句怎么称呼他" in _prompt("s1", [], opening)


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
