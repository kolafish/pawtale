import asyncio
import base64
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.services.ai_base import AITextServiceBase
from app.config import DEFAULT_AI_TEXT_MODEL, get_settings
from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
)
from app.models.memoir import (
    AudioFile,
    MemoirChatMessage,
    MemoirCompletionIssue,
    MemoirEditingJob,
    MemoirVoiceSession,
)
from app.models.memoir_narrative import (
    MemoirInterviewPolicyDecision,
)
from app.models.projects import Project
from app.services.ai_streaming import AITextStreamChunk
from app.services.memoir import automation_event_metadata
from app.services.memoir import automation_events
from app.services.memoir import voice_progress_notices
from app.services.memoir.memory_activation import MemoryActivationResult
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_assistant_turn_stream import VoiceAssistantTurnStreamSession
from app.services.memoir.voice_tts_types import StreamingTTSChunk
from tests.memoir_helpers import (
    create_test_book_plan,
    ensure_mobile_memoir_project,
    create_test_voice_transcript,
    post_mobile_memoir_voice_session,
)


async def test_voice_assistant_turn_streams_text_audio_and_persists_message(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我想聊聊第一份工作，当时是在厂里学技术。",
        guidance_mode="guided_interview",
        current_guidance_module="work",
    )
    await voice_progress_notices.enqueue_utterance_milestone_notice(
        test_db,
        project_id=created["session"]["project_id"],
        utterance_id=created["utterance_id"],
    )
    await test_db.commit()
    stream_client = FakeTurnClient()
    ai_client = FakeStreamingAI(
        [
            "好，那我们就接着聊工作。",
            "第一份工作里，",
            "最让您记到现在的是哪一件事？",
        ]
    )
    tts_client = FakeStreamingTTS()
    session = VoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        ai_client_factory=lambda model=None: ai_client,
        tts_client_factory=lambda: tts_client,
    )

    run_task = asyncio.create_task(session.run())
    ready = await _wait_for_event(stream_client, "turn.ready")
    await stream_client.push(
        {
            "type": "turn.start",
            "ai_model": DEFAULT_AI_TEXT_MODEL,
            "voice": "S_test_voice",
            "speed_ratio": 1.1,
        }
    )
    started = await _wait_for_event(stream_client, "turn.started")
    completed = await _wait_for_event(stream_client, "turn.completed")
    await asyncio.wait_for(run_task, timeout=1)

    assert ready["state"] == "ready"
    assert started["ai_provider"] == "fake-ai"
    assert started["requested_ai_model"] == DEFAULT_AI_TEXT_MODEL
    assert started["resolved_ai_model"] == "fake-stream-model"
    assert started["audio_protocol"] == "pcm_frame"
    assert completed["state"] == "completed"
    assert completed["requested_ai_model"] == DEFAULT_AI_TEXT_MODEL
    assert completed["resolved_ai_model"] == "fake-stream-model"
    assert completed["response_ai_model"] == "fake-stream-model"
    assert completed["ai_model_mismatch"] is False
    assert completed["usage"] == {
        "input_tokens": 10,
        "output_tokens": 32,
        "total_tokens": 42,
    }
    assert completed["text"] == "好，那我们就接着聊工作。第一份工作里，最让您记到现在的是哪一件事？"
    assert completed["primary_intent"] == "follow_up"
    assert completed["audio_file_id"]
    assert completed["progress_update_used"] is False
    assert completed["progress_update"] is None

    text_deltas = [event["text_delta"] for event in stream_client.sent if event["type"] == "text.delta"]
    assert text_deltas == ai_client.chunks
    assert [event["type"] for event in stream_client.sent if event["type"].startswith("audio.")] == [
        "audio.format",
        "audio.frame",
        "audio.frame",
        "audio.done",
    ]
    frame_events = [event for event in stream_client.sent if event["type"] == "audio.frame"]
    assert [base64.b64decode(event["audio"]) for event in frame_events] == [b"audio-0-1", b"audio-0-2"]
    assert frame_events[0]["sample_rate"] == 16000
    assert frame_events[0]["channels"] == 1
    assert frame_events[0]["bits_per_sample"] == 16
    done = [event for event in stream_client.sent if event["type"] == "audio.done"][0]
    assert done["frame_count"] == 2
    assert tts_client.calls[0]["text_chunks"] == [
        "好，那我们就接着聊工作。",
        "第一份工作里，最让您记到现在的是哪一件事？",
    ]
    assert tts_client.calls[0]["voice"] == "S_test_voice"
    assert tts_client.calls[0]["speed_ratio"] == 1.1
    assert tts_client.calls[0]["response_format"] == "pcm"
    assert tts_client.calls[0]["sample_rate"] == 16000

    messages = (
        (
            await test_db.execute(
                select(MemoirChatMessage)
                .where(MemoirChatMessage.voice_session_id == created["session"]["id"])
                .order_by(MemoirChatMessage.turn_index)
            )
        )
        .scalars()
        .all()
    )
    assistant_messages = [message for message in messages if message.role == MemoirChatMessageRole.ASSISTANT.value]
    assert len(assistant_messages) == 1
    assistant_message = assistant_messages[0]
    assert assistant_message.id == completed["message_id"]
    assert assistant_message.audio_file_id == completed["audio_file_id"]
    assert assistant_message.metadata_json["source"] == "ai.streaming_assistant_turn"
    assert assistant_message.metadata_json["turn_pipeline"] == "streaming_llm_tts"
    assert assistant_message.metadata_json["provider"] == "fake-ai"
    assert assistant_message.metadata_json["requested_model"] == DEFAULT_AI_TEXT_MODEL
    assert assistant_message.metadata_json["resolved_model"] == "fake-stream-model"
    assert assistant_message.metadata_json["response_model"] == "fake-stream-model"
    assert assistant_message.metadata_json["model_mismatch"] is False
    assert assistant_message.metadata_json["tts_provider"] == "fake-tts"
    assert assistant_message.metadata_json["tts_duration_seconds"] == 0.4
    assert assistant_message.metadata_json["tts_alignment"][0]["text"] == "好"
    assert assistant_message.metadata_json["progress_update_used"] is False
    prompt = ai_client.calls[0]["user_prompt"]
    assert "这个项目已经留下 1 条讲述记录" not in prompt
    assert "叙述者：我想聊聊第一份工作，当时是在厂里学技术。" in prompt
    assert "人生话题" in prompt

    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == completed["audio_file_id"]))
    assert audio_file is not None
    assert audio_file.mime_type == "audio/pcm"
    assert audio_file.size_bytes == len(b"audio-0-1audio-0-2")
    audio_relative_path = Path(audio_file.storage_path).relative_to(Path("memoir") / "audio")
    assert (tmp_path / "audio" / audio_relative_path).read_bytes() == b"audio-0-1audio-0-2"


async def test_voice_assistant_turn_injects_retrieved_context_and_records_trace(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我又想起她去医院前很害怕。",
        guidance_mode="guided_interview",
    )
    await _as_book_completion(test_db, created["session"]["id"])
    activated_context = (
        "以下是从同一回忆录过往讲述中检索到的候选原文。\n"
        "历史候选 1：李桂芳去华西医院做手术前很害怕，是姐姐陪她办的住院。"
    )

    async def fake_activate(*args, **kwargs):
        return MemoryActivationResult(
            trace_id="activation-trace-1",
            status="succeeded",
            context=activated_context,
            injected_count=1,
        )

    monkeypatch.setattr(
        "app.services.memoir.memory_activation.activate_memory_for_turn",
        fake_activate,
    )

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["听起来那次住院让她很不安，当时姐姐是怎么陪着她的？"],
    )

    assert activated_context in ai_client.calls[0]["user_prompt"]
    assistant_message = await test_db.get(MemoirChatMessage, completed["message_id"])
    assert assistant_message is not None
    assert assistant_message.metadata_json["memory_activation_trace_id"] == ("activation-trace-1")
    assert assistant_message.metadata_json["memory_activation_status"] == "succeeded"
    assert assistant_message.metadata_json["memory_activation_injected_count"] == 1


async def test_voice_assistant_turn_continues_when_memory_activation_crashes(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我继续讲刚才那件事。",
        guidance_mode="guided_interview",
    )
    await _as_book_completion(test_db, created["session"]["id"])

    async def fail_activation(*args, **kwargs):
        raise RuntimeError("trace database unavailable")

    monkeypatch.setattr(
        "app.services.memoir.memory_activation.activate_memory_for_turn",
        fail_activation,
    )

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["好，您接着讲，我在听。"],
    )

    assert completed["state"] == "completed"
    assert "本轮没有检索到历史讲述候选" in ai_client.calls[0]["user_prompt"]
    assistant_message = await test_db.get(MemoirChatMessage, completed["message_id"])
    assert assistant_message is not None
    assert assistant_message.metadata_json["memory_activation_status"] == "failed"
    assert assistant_message.metadata_json["memory_activation_trace_id"] is None


async def test_follow_up_listens_with_life_topics_but_no_retrieval(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="那年我们全家搬进了县城的大杂院。",
    )
    await voice_progress_notices.enqueue_utterance_milestone_notice(
        test_db,
        project_id=created["session"]["project_id"],
        utterance_id=created["utterance_id"],
    )
    await test_db.commit()

    async def unexpected_activation(*args, **kwargs):
        raise AssertionError("the AI interview must not retrieve earlier material mid-story")

    monkeypatch.setattr(
        "app.services.memoir.memory_activation.activate_memory_for_turn",
        unexpected_activation,
    )

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["搬进大杂院，日子一下子热闹起来了。后来呢？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert completed["state"] == "completed"
    assert completed["progress_update"] is None
    assert "叙述者：那年我们全家搬进了县城的大杂院。" in prompt
    assert "这个项目已经留下 1 条讲述记录" not in prompt
    assert "不另起问题" in prompt
    assert "人生话题（按人生先后" in prompt
    assert "历史讲述候选" not in prompt
    assert ai_client.calls[0]["kwargs"]["max_tokens"] == 260


async def test_book_completion_acknowledgement_suppresses_global_progress_and_ends_round(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="今年看到山桃花照片以后，我们重新联系并约好一起爬山。",
        guidance_mode="guided_interview",
    )
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=created["session"]["project_id"],
        chapter_title="山桃花开的心动",
    )
    issue = MemoirCompletionIssue(
        project_id=created["session"]["project_id"],
        scope="book",
        issue_type="context",
        channel="interview",
        question="这段内容开始前，读者还需要知道哪些重要背景？",
        reason="说明两人如何重新联系。",
        priority="medium",
        fingerprint="a" * 64,
        related_material_ids_json=[],
        input_signature="assistant-turn-completion",
        status="closed",
        outcome="answered",
        answered_material_id=created["utterance_id"],
    )
    test_db.add(issue)
    await test_db.flush()
    session = await test_db.get(MemoirVoiceSession, created["session"]["id"])
    assert session is not None
    session.session_goal = "book_completion"
    session.current_completion_issue_id = issue.id
    test_db.add(session)
    await automation_events.record_automation_event(
        test_db,
        project_id=created["session"]["project_id"],
        event_type=MemoirAutomationEventType.BOOK_OUTPUT_GENERATED,
        summary_text="另有5条讲述未归入章节。",
        metadata_json={"source": "book_quality_audit"},
    )
    await test_db.commit()

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=session.id,
        user_id=created["user_id"],
        chunks=["这段背景已经保存，接下来请返回全书概览。"],
    )

    assert completed["text"] == "这段背景已经保存，接下来请返回全书概览。"
    assert completed["expects_answer"] is False
    assert completed["progress_update_used"] is False
    assert completed["progress_spoken_text"] == ""
    assert "另有5条讲述未归入章节" not in ai_client.calls[0]["user_prompt"]
    assert "不要播报全书进度" in ai_client.calls[0]["user_prompt"]
    assert "这段内容开始前，读者还需要知道哪些重要背景" in ai_client.calls[0]["user_prompt"]
    assert "只做结束确认，不再追问" in ai_client.calls[0]["user_prompt"]
    assistant_message = await test_db.get(MemoirChatMessage, completed["message_id"])
    assert assistant_message is not None
    assert assistant_message.metadata_json["expects_answer"] is False
    assert (
        await test_db.scalar(
            select(func.count(MemoirInterviewPolicyDecision.id)).where(
                MemoirInterviewPolicyDecision.session_id == session.id
            )
        )
        == 0
    )


async def test_voice_assistant_turn_only_enqueues_shadow_policy_without_changing_reply(
    client,
    test_db,
    monkeypatch,
):
    current_settings = get_settings()
    monkeypatch.setattr(
        "app.services.memoir.narrative.interview_policy_runtime.get_settings",
        lambda: current_settings.model_copy(update={"memoir_interview_policy_shadow_enabled": True}),
    )
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我还想接着讲小时候在河边玩的事。",
        guidance_mode="free_narration",
    )

    _, _, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["您慢慢讲，我在听。"],
    )

    shadow_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == created["session"]["project_id"],
            MemoirEditingJob.job_type == MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_SHADOW.value,
        )
    )
    assistant_message = await test_db.get(
        MemoirChatMessage,
        completed["message_id"],
    )
    assert completed["text"].endswith("您慢慢讲，我在听。")
    assert assistant_message is not None
    assert assistant_message.content_text == completed["text"]
    assert shadow_job is not None
    assert shadow_job.status == MemoirEditingJobStatus.PENDING.value
    assert "河边" not in str(shadow_job.payload_json)


async def test_voice_assistant_turn_applies_and_records_low_risk_active_policy(
    client,
    test_db,
    monkeypatch,
):
    current_settings = get_settings()
    monkeypatch.setattr(
        "app.services.memoir.narrative.interview_policy_runtime.get_settings",
        lambda: current_settings.model_copy(
            update={
                "memoir_interview_policy_shadow_enabled": True,
                "memoir_interview_policy_active_enabled": True,
                "memoir_interview_policy_active_rollout_percent": 100,
            }
        ),
    )
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我还想接着讲小时候在河边玩的事。",
        guidance_mode="free_narration",
    )
    policy_ai = FakePolicyAI("follow_current_story")

    _, reply_ai, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["河边那次，后来还发生了什么？"],
        policy_ai_client=policy_ai,
    )

    decision = await test_db.scalar(
        select(MemoirInterviewPolicyDecision).where(
            MemoirInterviewPolicyDecision.generated_message_id == completed["message_id"]
        )
    )
    shadow_job_count = await test_db.scalar(
        select(func.count(MemoirEditingJob.id)).where(
            MemoirEditingJob.project_id == created["session"]["project_id"],
            MemoirEditingJob.job_type == MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_SHADOW.value,
        )
    )

    assert "当前受控访谈策略" in reply_ai.calls[0]["user_prompt"]
    assert "顺着当前故事追问" in reply_ai.calls[0]["user_prompt"]
    assert policy_ai.calls
    assert decision is not None
    assert decision.execution_mode == "active"
    assert decision.selected_action == "follow_current_story"
    assert decision.generated_message_id == completed["message_id"]
    assert "河边" not in str(decision.state_snapshot_json)
    assert shadow_job_count == 0


async def test_voice_assistant_turn_records_medium_policy_as_suggestion_only(
    client,
    test_db,
    monkeypatch,
):
    current_settings = get_settings()
    monkeypatch.setattr(
        "app.services.memoir.narrative.interview_policy_runtime.get_settings",
        lambda: current_settings.model_copy(
            update={
                "memoir_interview_policy_shadow_enabled": True,
                "memoir_interview_policy_active_enabled": True,
                "memoir_interview_policy_active_rollout_percent": 100,
            }
        ),
    )
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我还想接着讲小时候在河边玩的事。",
        guidance_mode="free_narration",
    )

    _, reply_ai, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["您慢慢讲，我在听。"],
        policy_ai_client=FakePolicyAI("return_to_prior_story"),
    )

    decision = await test_db.scalar(
        select(MemoirInterviewPolicyDecision).where(
            MemoirInterviewPolicyDecision.generated_message_id == completed["message_id"]
        )
    )
    assert "当前受控访谈策略" not in reply_ai.calls[0]["user_prompt"]
    assert decision is not None
    assert decision.execution_mode == "suggest"
    assert decision.selected_action == "return_to_prior_story"


async def test_voice_assistant_turn_policy_failure_falls_back_to_existing_reply_and_shadow(
    client,
    test_db,
    monkeypatch,
):
    current_settings = get_settings()
    monkeypatch.setattr(
        "app.services.memoir.narrative.interview_policy_runtime.get_settings",
        lambda: current_settings.model_copy(
            update={
                "memoir_interview_policy_shadow_enabled": True,
                "memoir_interview_policy_active_enabled": True,
                "memoir_interview_policy_active_rollout_percent": 100,
            }
        ),
    )
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我还想接着讲小时候在河边玩的事。",
        guidance_mode="free_narration",
    )

    _, reply_ai, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["您慢慢讲，我在听。"],
        policy_ai_client=FailingPolicyAI(),
    )

    shadow_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == created["session"]["project_id"],
            MemoirEditingJob.job_type == MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_SHADOW.value,
        )
    )
    decision = await test_db.scalar(select(MemoirInterviewPolicyDecision))

    assert completed["text"].endswith("您慢慢讲，我在听。")
    assert "当前受控访谈策略" not in reply_ai.calls[0]["user_prompt"]
    assert decision is None
    assert shadow_job is not None


async def test_voice_assistant_turn_policy_persistence_failure_falls_back_to_shadow(
    client,
    test_db,
    monkeypatch,
):
    current_settings = get_settings()
    monkeypatch.setattr(
        "app.services.memoir.narrative.interview_policy_runtime.get_settings",
        lambda: current_settings.model_copy(
            update={
                "memoir_interview_policy_shadow_enabled": True,
                "memoir_interview_policy_active_enabled": True,
                "memoir_interview_policy_active_rollout_percent": 100,
            }
        ),
    )

    async def fail_policy_persistence(*args, **kwargs):
        raise RuntimeError("policy persistence unavailable")

    monkeypatch.setattr(
        "app.services.memoir.narrative.interview_policy_runtime.persist_policy_turn_decision",
        fail_policy_persistence,
    )
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我还想接着讲小时候在河边玩的事。",
        guidance_mode="free_narration",
    )

    _, _, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["河边那次，后来还发生了什么？"],
        policy_ai_client=FakePolicyAI("follow_current_story"),
    )

    shadow_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == created["session"]["project_id"],
            MemoirEditingJob.job_type == MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_SHADOW.value,
        )
    )
    assistant_message = await test_db.get(
        MemoirChatMessage,
        completed["message_id"],
    )
    assert assistant_message is not None
    assert assistant_message.content_text == completed["text"]
    assert shadow_job is not None


async def test_voice_assistant_turn_cancel_does_not_persist_partial_message(
    client,
    test_db,
):
    created = await create_test_voice_transcript(client, test_db)
    stream_client = FakeTurnClient()
    session = VoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        ai_client_factory=lambda model=None: SlowStreamingAI(),
        tts_client_factory=lambda: FakeStreamingTTS(),
    )

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(stream_client, "turn.ready")
    await stream_client.push({"type": "turn.start"})
    await _wait_for_event(stream_client, "turn.started")
    await stream_client.push({"type": "turn.cancel", "reason": "interrupted_by_user"})
    cancelled = await _wait_for_event(stream_client, "turn.cancelled")
    await asyncio.wait_for(run_task, timeout=1)

    assert cancelled["state"] == "interrupted"
    assert cancelled["reason"] == "interrupted_by_user"
    assistant_count = await test_db.scalar(
        select(func.count(MemoirChatMessage.id)).where(
            MemoirChatMessage.voice_session_id == created["session"]["id"],
            MemoirChatMessage.role == MemoirChatMessageRole.ASSISTANT.value,
        )
    )
    assert assistant_count == 0


async def test_voice_assistant_turn_first_opening_asks_how_to_address_the_narrator(
    client,
    test_db,
):
    response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "person"},
    )
    voice_session = response.json()["session"]
    user_id = (await client.get("/api/auth/me")).json()["id"]

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=voice_session["id"],
        user_id=user_id,
        chunks=["你想先聊哪一位亲人？"],
        regenerate=True,
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert completed["primary_intent"] == "opening"
    assert completed["progress_update_used"] is False
    assert completed["progress_update"] is None
    assert ai_client.calls[0]["kwargs"]["max_tokens"] == 420
    assert "“老余”" in prompt
    assert "当前是第一次开场" in prompt
    assert "怎么称呼他" in prompt
    assert "待讲主题" not in prompt
    assert "人物、事情、家庭、工作、地点、某一天、人生阶段、闲聊" not in prompt
    assert "本轮没有可播报的结构化进度变化" not in prompt


async def test_first_narration_offers_one_profile_item_and_records_the_ask(client, test_db):
    created = await post_mobile_memoir_voice_session(client, json={"guidance_mode": "guided_interview"})
    voice_session = created.json()["session"]
    user_id = (await client.get("/api/auth/me")).json()["id"]
    test_db.add_all([
        MemoirChatMessage(
            project_id=voice_session["project_id"], voice_session_id=voice_session["id"],
            role=role, content_text=text, content_status=MemoirChatContentStatus.FINAL.value,
            turn_index=index,
        )
        for index, (role, text) in enumerate([("assistant", "您是哪年生的？"), ("user", "年龄就别问了。")])
    ])
    project = await test_db.get(Project, voice_session["project_id"])
    profile = project.metadata_json["memoir_profile"]
    project.metadata_json = {
        **project.metadata_json,
        "memoir_profile": {
            **profile,
            "narrator_name": "王秀英",
            "declined_in": {"narrator_age": "utterance-1"},
        },
    }
    await test_db.commit()

    _, ai_client, _, _ = await _run_stream_turn(
        test_db, voice_session_id=voice_session["id"], user_id=user_id, chunks=["好的，您老家是哪里的？"],
    )

    assert "还想知道他的老家" in ai_client.calls[0]["user_prompt"]
    reply = await test_db.scalar(
        select(MemoirChatMessage)
        .where(
            MemoirChatMessage.voice_session_id == voice_session["id"],
            MemoirChatMessage.role == MemoirChatMessageRole.ASSISTANT.value,
        )
        .order_by(MemoirChatMessage.turn_index.desc())
        .limit(1)
    )
    assert reply.metadata_json["profile_question"] == "narrator_origin"
    assert reply.metadata_json["profile_asked"] == "narrator_origin"


async def test_voice_assistant_turn_female_voice_opening_uses_xiaoyu_persona(
    client,
    test_db,
):
    response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "person"},
    )
    voice_session = response.json()["session"]
    user_id = (await client.get("/api/auth/me")).json()["id"]

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=voice_session["id"],
        user_id=user_id,
        chunks=["您好，我是小余。今天我会陪您慢慢聊聊这些回忆，想先从哪类人或事开始？"],
        voice="zh_female_xiaohe_uranus_bigtts",
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert completed["primary_intent"] == "opening"
    assert completed["host_name"] == "小余"
    assert "你在对话中自称“小余”" in ai_client.calls[0]["system_prompt"]
    assert "下一句“小余”要说的话" in prompt
    assert "你自称“小余”" in prompt


async def test_voice_assistant_turn_does_not_turn_project_snapshot_into_notice(
    client,
    test_db,
):
    await create_test_voice_transcript(
        client,
        test_db,
        text="第一条讲的是小时候在院子里摘菜。",
        guidance_mode="guided_interview",
        current_guidance_module="family",
    )
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="第二条讲的是过年时一家人在厨房忙活。",
        guidance_mode="guided_interview",
        current_guidance_module="family",
    )

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["现在已经有两条讲述了，我们接着把过年的厨房讲具体一点。那天是谁先开始忙的？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert completed["progress_update_used"] is False
    assert completed["progress_update"] is None
    assert "2 条讲述记录" not in prompt


async def test_voice_assistant_turn_does_not_offer_same_progress_twice(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常在外婆家院子里摘菜。",
        guidance_mode="guided_interview",
        current_guidance_module="family",
    )
    await voice_progress_notices.enqueue_utterance_milestone_notice(
        test_db,
        project_id=created["session"]["project_id"],
        utterance_id=created["utterance_id"],
    )
    await test_db.commit()
    opening_session_id = await _opening_session(client, created["session"]["project_id"])

    _, first_ai, _, first = await _run_stream_turn(
        test_db,
        voice_session_id=opening_session_id,
        user_id=created["user_id"],
        chunks=["这已经是第一条讲述了，今天想接着讲外婆家的院子吗？"],
    )
    await automation_events.mark_automation_events_voice_played(
        test_db,
        user_id=created["user_id"],
        project_id=created["session"]["project_id"],
        event_ids=first["progress_voice_selected_event_ids"],
        voice_session_id=opening_session_id,
        message_id=first["message_id"],
    )
    _, second_ai, _, second = await _run_stream_turn(
        test_db,
        voice_session_id=opening_session_id,
        user_id=created["user_id"],
        chunks=["换个说法，今天想从哪儿讲起？"],
        regenerate=True,
    )

    assert first["progress_update_used"] is True
    assert "本轮进展：这个项目已经留下 1 条讲述记录。" in first_ai.calls[0]["user_prompt"]
    assert second["progress_update_used"] is False
    assert "这个项目已经留下 1 条讲述记录" not in second_ai.calls[0]["user_prompt"]


async def test_voice_assistant_turn_progress_prompt_uses_recent_automation_event(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常在外婆家院子里摘菜。",
        guidance_mode="guided_interview",
        current_guidance_module="family",
    )
    event = await automation_events.record_automation_event(
        test_db,
        project_id=created["session"]["project_id"],
        event_type=MemoirAutomationEventType.CHAPTER_DRAFT_GENERATED,
        utterance_ids=[created["utterance_id"]],
        summary_text="刚才新增的 1 条讲述已补进《外婆家的院子》。",
        confidence=0.92,
    )
    await test_db.commit()

    _, mid_story_ai, _, mid_story = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["外婆在院子里最常做什么？"],
    )
    assert mid_story["progress_update"] is None
    assert "外婆家的院子》。" not in mid_story_ai.calls[0]["user_prompt"]
    await test_db.refresh(event)
    assert automation_event_metadata.VOICE_SELECTED_SESSION_ID_KEY not in (event.metadata_json or {})

    opening_session_id = await _opening_session(client, created["session"]["project_id"])
    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=opening_session_id,
        user_id=created["user_id"],
        chunks=["刚才新增的一条讲述已经补进《外婆家的院子》，接下来想听听外婆在院子里最常做什么？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert "本轮进展：刚才新增的 1 条讲述已补进《外婆家的院子》。" in prompt
    assert "把进度与本轮需要的问候、承接或问题统一组织成一段自然口语" in prompt
    assert "语音开头会先播报" not in prompt
    assert "只允许引用以下事实" in prompt
    assert completed["text"] == "刚才新增的一条讲述已经补进《外婆家的院子》，接下来想听听外婆在院子里最常做什么？"
    assert completed["progress_update"]["kind"] == "automation_event"
    assert completed["progress_update"]["event_ids"] == [event.id]
    assert completed["progress_spoken_text"] == ""

    assistant_message = await test_db.scalar(
        select(MemoirChatMessage).where(MemoirChatMessage.id == completed["message_id"])
    )
    assert assistant_message.metadata_json["progress_update"]["kind"] == "automation_event"
    assert assistant_message.metadata_json["progress_automation_event_ids"] == [event.id]
    assert assistant_message.metadata_json["progress_voice_selected_event_ids"] == [event.id]
    assert assistant_message.metadata_json["progress_spoken_text"] == ""
    await test_db.refresh(event)
    assert event.metadata_json[automation_event_metadata.VOICE_SELECTED_SESSION_ID_KEY] == opening_session_id
    assert event.metadata_json[automation_event_metadata.VOICE_SELECTED_MESSAGE_ID_KEY] == completed["message_id"]
    assert automation_event_metadata.VOICE_PLAYED_AT_KEY not in event.metadata_json


async def test_voice_assistant_turn_strips_host_label_from_unified_progress_reply(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我辞职后想做一个内容创作平台。",
        guidance_mode="guided_interview",
        current_guidance_module="work",
    )
    await automation_events.record_automation_event(
        test_db,
        project_id=created["session"]["project_id"],
        event_type=MemoirAutomationEventType.CHAPTER_DRAFT_GENERATED,
        utterance_ids=[created["utterance_id"]],
        summary_text="「职场浮沉与升职加薪」已自动补写 4 条新增讲述。",
        confidence=0.92,
    )
    await test_db.commit()

    stream_client, _, tts_client, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        voice="S_xiaohe_female",
        chunks=[
            "小",
            "余：最近《职场浮沉与升职加薪》已经自动补写了四条讲述，顺着你刚才提到的一人公司，",
            "那辞职前最早是怎么进入这个行业的？",
        ],
    )

    assert completed["host_name"] == "小余"
    assert "小余：" not in completed["text"]
    assert completed["text"] == (
        "最近《职场浮沉与升职加薪》已经自动补写了四条讲述，顺着你刚才提到的一人公司，"
        "那辞职前最早是怎么进入这个行业的？"
    )
    text_deltas = [event["text_delta"] for event in stream_client.sent if event["type"] == "text.delta"]
    assert text_deltas == [
        "最近《职场浮沉与升职加薪》已经自动补写了四条讲述，顺着你刚才提到的一人公司，",
        "那辞职前最早是怎么进入这个行业的？",
    ]
    assert all("小余：" not in chunk for chunk in tts_client.calls[0]["text_chunks"])


async def test_voice_assistant_turn_progress_prompt_uses_failed_automation_event(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常在外婆家院子里摘菜。",
        guidance_mode="guided_interview",
        current_guidance_module="family",
    )
    event = await automation_events.record_automation_event(
        test_db,
        project_id=created["session"]["project_id"],
        event_type=MemoirAutomationEventType.CHAPTER_DRAFT_GENERATED,
        status=MemoirAutomationEventStatus.FAILED,
        utterance_ids=[created["utterance_id"]],
        summary_text="章节自动补写失败，稍后可重试。",
    )
    await test_db.commit()
    opening_session_id = await _opening_session(client, created["session"]["project_id"])

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=opening_session_id,
        user_id=created["user_id"],
        chunks=["章节自动补写暂时没有成功，稍后可以重试；咱们先继续聊外婆家的院子。"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert "本轮进展：章节自动补写失败，稍后可重试。" in prompt
    assert "语音开头会先播报" not in prompt
    assert completed["progress_update"]["kind"] == "automation_event_failure"
    assert completed["progress_update"]["event_ids"] == [event.id]
    assert completed["progress_update"]["event_status"] == "failed"
    assert completed["progress_spoken_text"] == ""
    assert completed["text"] == "章节自动补写暂时没有成功，稍后可以重试；咱们先继续聊外婆家的院子。"


async def test_voice_assistant_turn_skips_voice_played_automation_event(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常在外婆家院子里摘菜。",
        guidance_mode="guided_interview",
        current_guidance_module="family",
    )
    await automation_events.record_automation_event(
        test_db,
        project_id=created["session"]["project_id"],
        event_type=MemoirAutomationEventType.CHAPTER_DRAFT_GENERATED,
        utterance_ids=[created["utterance_id"]],
        summary_text="这条自动补写记录不应该重复播报。",
        metadata_json={
            automation_event_metadata.VOICE_PLAYED_AT_KEY: "2026-06-23T12:00:00+00:00",
            automation_event_metadata.VOICE_PLAYED_SESSION_ID_KEY: "previous-session",
        },
    )
    await test_db.commit()

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["那我们继续聊外婆家的院子。院子里最热闹的时候通常是什么时候？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert "这条自动补写记录不应该重复播报" not in prompt
    assert completed["progress_update"] is None


async def test_voice_assistant_turn_guided_opening_does_not_copy_previous_session_text(
    client,
    test_db,
):
    previous_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "event"},
    )
    previous_session = previous_response.json()["session"]
    previous_message = MemoirChatMessage(
        project_id=previous_session["project_id"],
        voice_session_id=previous_session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text="上次讲到我第一次去上海找工作。",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=0,
        metadata_json={"source": "test"},
    )
    test_db.add(previous_message)
    await test_db.commit()
    response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "event"},
    )
    voice_session = response.json()["session"]
    user_id = (await client.get("/api/auth/me")).json()["id"]

    _, ai_client, _, _ = await _run_stream_turn(
        test_db,
        voice_session_id=voice_session["id"],
        user_id=user_id,
        chunks=["欢迎回来，咱们接着聊上次去上海找工作的事，今天想接着讲哪里？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert "以前讲过" in prompt
    assert "待讲主题" in prompt
    assert "上次讲到我第一次去上海找工作" not in prompt


async def test_voice_assistant_follow_up_sees_book_and_life_topics(
    client,
    test_db,
):
    response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "current_guidance_module": "family"},
    )
    voice_session = response.json()["session"]
    messages = [
        MemoirChatMessage(
            project_id=voice_session["project_id"],
            voice_session_id=voice_session["id"],
            role=MemoirChatMessageRole.USER.value,
            content_text="我小时候经常住在外婆家。",
            content_status=MemoirChatContentStatus.FINAL.value,
            turn_index=0,
            metadata_json={"source": "test"},
        ),
        MemoirChatMessage(
            project_id=voice_session["project_id"],
            voice_session_id=voice_session["id"],
            role=MemoirChatMessageRole.ASSISTANT.value,
            content_text="那我们接着聊外婆。",
            content_status=MemoirChatContentStatus.FINAL.value,
            guidance_intent="follow_up",
            turn_index=1,
            metadata_json={"expects_answer": True},
        ),
        MemoirChatMessage(
            project_id=voice_session["project_id"],
            voice_session_id=voice_session["id"],
            role=MemoirChatMessageRole.USER.value,
            content_text="今天聊聊工作吧。",
            content_status=MemoirChatContentStatus.FINAL.value,
            turn_index=2,
            metadata_json={"source": "test"},
        ),
    ]
    test_db.add_all(messages)
    await test_db.commit()
    await create_test_book_plan(
        test_db,
        project_id=voice_session["project_id"],
        chapter_title="外婆家的童年",
    )
    patched = await client.patch(
        f"/api/mobile/memoir/projects/{voice_session['project_id']}",
        json={"narrator_gender": "female", "narrator_age": 34, "narrator_origin": "成都"},
    )
    assert patched.status_code == 200, patched.text
    user_id = (await client.get("/api/auth/me")).json()["id"]

    _, ai_client, _, _ = await _run_stream_turn(
        test_db,
        voice_session_id=voice_session["id"],
        user_id=user_id,
        chunks=["好，那我们聊工作。第一份工作里，最让您记到现在的是哪一件事？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert ai_client.calls[0]["kwargs"]["max_tokens"] == 260
    assert "叙述者：今天聊聊工作吧。" in prompt
    assert "《外婆家的童年》" in prompt
    assert "工作与闯荡" in prompt
    assert "讲述者：女，34 岁，家乡成都" in prompt
    assert "主题线索" not in prompt


async def test_returning_opening_offers_last_chapter_and_a_thin_topic(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="小时候我总在外婆家的院子里摘菜。",
    )
    await create_test_book_plan(
        test_db,
        project_id=created["session"]["project_id"],
        source_ref={"utterance_id": created["utterance_id"]},
        chapter_title="外婆家的童年",
    )
    opening_session_id = await _opening_session(
        client,
        created["session"]["project_id"],
        guidance_mode="free_narration",
    )

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=opening_session_id,
        user_id=created["user_id"],
        chunks=["欢迎回来，上次讲到外婆家的童年，今天想接着讲吗？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert completed["primary_intent"] == "opening"
    assert "“外婆家的童年”" in prompt
    assert "待讲主题" in prompt


async def test_guided_opening_from_life_topic_starts_with_that_topic(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    topic_map = (await client.get(f"/api/mobile/memoir/projects/{project_id}/topic-map")).json()
    topic = topic_map["modules"][0]["items"][1]
    response = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={"guidance_mode": "guided_interview", "anchor_framework_item_id": topic["id"]},
    )
    assert response.status_code == 201
    user_id = (await client.get("/api/auth/me")).json()["id"]

    _, ai_client, _, _ = await _run_stream_turn(
        test_db,
        voice_session_id=response.json()["session"]["id"],
        user_id=user_id,
        chunks=["今天从上学说起，您最早是在哪儿念的书？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert f"叙述者选了“{topic['title']}”" in prompt
    assert topic["intent"] in prompt


async def test_voice_assistant_turn_tts_failure_does_not_persist_partial_message(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="今天我想聊聊朋友吧，不太想讲工作了。",
        guidance_mode="guided_interview",
        current_guidance_module="work",
    )
    stream_client = FakeTurnClient()
    ai_client = FakeStreamingAI(
        [
            "好，那我们就聊聊朋友。",
            "你说到朋友，我想问这些年里有没有一个人让你觉得一见面还是特别亲？",
        ]
    )
    session = VoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        ai_client_factory=lambda model=None: ai_client,
        tts_client_factory=lambda: FailingStreamingTTS(),
    )

    run_task = asyncio.create_task(session.run())
    await _wait_for_event(stream_client, "turn.ready")
    await stream_client.push({"type": "turn.start"})
    failed = await _wait_for_event(stream_client, "turn.failed")
    await asyncio.wait_for(run_task, timeout=1)

    assert "tts failed" in failed["error"]
    assert not [event for event in stream_client.sent if event["type"].startswith("audio.")]
    assistant_count = await test_db.scalar(
        select(func.count(MemoirChatMessage.id)).where(
            MemoirChatMessage.voice_session_id == created["session"]["id"],
            MemoirChatMessage.role == MemoirChatMessageRole.ASSISTANT.value,
        )
    )
    assert assistant_count == 0


async def _as_book_completion(test_db, voice_session_id: str) -> None:
    session = await test_db.get(MemoirVoiceSession, voice_session_id)
    assert session is not None
    session.session_goal = "book_completion"
    await test_db.commit()


async def _opening_session(client, project_id: str, guidance_mode: str = "guided_interview") -> str:
    response = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={"guidance_mode": guidance_mode},
    )
    return response.json()["session"]["id"]


async def _run_stream_turn(
    test_db,
    *,
    voice_session_id: str,
    user_id: str,
    chunks: list[str],
    regenerate: bool = False,
    voice: str | None = None,
    policy_ai_client=None,
) -> tuple["FakeTurnClient", "FakeStreamingAI", "FakeStreamingTTS", dict[str, Any]]:
    stream_client = FakeTurnClient()
    ai_client = FakeStreamingAI(chunks)
    tts_client = FakeStreamingTTS()
    session = VoiceAssistantTurnStreamSession(
        db=test_db,
        client=stream_client,
        voice_session_id=voice_session_id,
        user_id=user_id,
        ai_client_factory=lambda model=None: ai_client,
        policy_ai_client_factory=(
            (lambda model=None: policy_ai_client) if policy_ai_client is not None else (lambda model=None: ai_client)
        ),
        tts_client_factory=lambda: tts_client,
    )
    run_task = asyncio.create_task(session.run())
    await _wait_for_event(stream_client, "turn.ready")
    payload: dict[str, Any] = {"type": "turn.start", "regenerate": regenerate}
    if voice:
        payload["voice"] = voice
    await stream_client.push(payload)
    completed = await _wait_for_event(stream_client, "turn.completed")
    await asyncio.wait_for(run_task, timeout=1)
    return stream_client, ai_client, tts_client, completed


async def _wait_for_event(client: "FakeTurnClient", event_type: str) -> dict[str, Any]:
    for _ in range(100):
        for event in client.sent:
            if event["type"] == event_type:
                return event
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {event_type}; sent={client.sent}")


class FakeTurnClient:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []

    async def push(self, payload: dict[str, Any]) -> None:
        await self.incoming.put(payload)

    async def receive_json(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class FakeStreamingAI(AITextServiceBase):
    provider_name = "fake-ai"
    model_name = "fake-stream-model"

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def _call_llm(self, *args, **kwargs):
        raise AssertionError("streaming assistant turn should not call non-streaming LLM")

    async def stream_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        yield AITextStreamChunk(
            usage={"input_tokens": 10},
            response_model=self.model_name,
        )
        for chunk in self.chunks:
            yield AITextStreamChunk(
                text_delta=chunk,
                response_model=self.model_name,
            )
        yield AITextStreamChunk(
            finish_reason="stop",
            usage={"output_tokens": 32, "total_tokens": 42},
        )


class FakePolicyAI(AITextServiceBase):
    provider_name = "fake-policy"
    model_name = "fake-policy-model"

    def __init__(self, selected_action: str) -> None:
        self.selected_action = selected_action
        self.calls: list[dict[str, Any]] = []

    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        return f'{{"selected_action":"{self.selected_action}"}}'


class FailingPolicyAI(FakePolicyAI):
    def __init__(self) -> None:
        super().__init__("continue_listening")

    async def _call_llm(self, system_prompt, user_prompt, **kwargs):
        raise RuntimeError("policy unavailable")


class SlowStreamingAI(FakeStreamingAI):
    def __init__(self) -> None:
        super().__init__(["这段不会返回"])

    async def stream_llm(self, system_prompt, user_prompt, **kwargs):
        await asyncio.sleep(10)
        yield AITextStreamChunk(text_delta="这段不会返回")


class FakeStreamingTTS:
    provider_name = "fake-tts"
    model_name = "fake-tts-model"
    default_voice = "S_default"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def synthesize(self, *args, **kwargs):
        raise AssertionError("streaming assistant turn should not call non-streaming TTS")

    async def stream_synthesize(self, *args, **kwargs):
        raise AssertionError("streaming assistant turn should use bidirectional TTS")

    async def stream_input_synthesize(
        self,
        text_chunks,
        *,
        voice=None,
        speed_ratio=None,
        response_format=None,
        sample_rate=None,
    ):
        segment_index = len(self.calls)
        call = {
            "text_chunks": [],
            "voice": voice,
            "speed_ratio": speed_ratio,
            "response_format": response_format,
            "sample_rate": sample_rate,
        }
        self.calls.append(call)
        async for text in text_chunks:
            call["text_chunks"].append(text)
            yield StreamingTTSChunk(
                audio=f"audio-{segment_index}-{len(call['text_chunks'])}".encode(),
                mime_type="audio/L16",
                provider=self.provider_name,
                model=self.model_name,
                voice=voice or self.default_voice,
                speed_ratio=speed_ratio or 1.0,
                sequence=len(call["text_chunks"]),
                sample_rate=sample_rate,
            )
        yield StreamingTTSChunk(
            audio=b"",
            mime_type="audio/L16",
            provider=self.provider_name,
            model=self.model_name,
            voice=voice or self.default_voice,
            speed_ratio=speed_ratio or 1.0,
            sequence=len(call["text_chunks"]) + 1,
            alignment=[TTSAlignmentSegment(text="好", start_ms=0, end_ms=400, text_start=0, text_end=1)],
            duration_seconds=0.4,
            sample_rate=sample_rate,
            is_final=True,
        )


class FailingStreamingTTS(FakeStreamingTTS):
    async def stream_input_synthesize(
        self,
        text_chunks,
        *,
        voice=None,
        speed_ratio=None,
        response_format=None,
        sample_rate=None,
    ):
        call = {
            "text_chunks": [],
            "voice": voice,
            "speed_ratio": speed_ratio,
            "response_format": response_format,
            "sample_rate": sample_rate,
        }
        self.calls.append(call)
        async for text in text_chunks:
            call["text_chunks"].append(text)
        if False:
            yield StreamingTTSChunk(
                audio=b"",
                mime_type="audio/L16",
                provider=self.provider_name,
                model=self.model_name,
                voice=voice or self.default_voice,
            )
        raise RuntimeError("tts failed after text completed")
