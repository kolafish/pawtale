import asyncio
import base64
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.domain.memoir.types import (
    MemoirAutomationEventStatus,
    MemoirAutomationEventType,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
)
from app.models.memoir import AudioFile, MemoirBookPlanChapter, MemoirChatMessage
from app.services.ai_streaming import AITextStreamChunk
from app.services.memoir import automation_event_metadata
from app.services.memoir import automation_events
from app.services.memoir.semantic_state import upsert_book_quality_audit_snapshot, upsert_chapter_card
from app.services.memoir.tts_alignment import TTSAlignmentSegment
from app.services.memoir.voice_assistant_turn_stream import VoiceAssistantTurnStreamSession
from app.services.memoir.voice_tts_types import StreamingTTSChunk
from tests.memoir_helpers import (
    add_test_chapter_source_links,
    create_test_book_plan,
    create_test_voice_transcript,
    memoir_source_ref,
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
            "ai_model": "fake-model",
            "voice": "S_test_voice",
            "speed_ratio": 1.1,
        }
    )
    started = await _wait_for_event(stream_client, "turn.started")
    completed = await _wait_for_event(stream_client, "turn.completed")
    await asyncio.wait_for(run_task, timeout=1)

    assert ready["state"] == "ready"
    assert started["ai_provider"] == "fake-ai"
    assert started["audio_protocol"] == "pcm_frame"
    assert completed["state"] == "completed"
    assert completed["text"] == "好，那我们就接着聊工作。第一份工作里，最让您记到现在的是哪一件事？"
    assert completed["primary_intent"] == "follow_up"
    assert completed["audio_file_id"]
    assert completed["progress_update_used"] is True
    assert completed["progress_update"]["kind"] == "valid_utterance_milestone"

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
        await test_db.execute(
            select(MemoirChatMessage)
            .where(MemoirChatMessage.voice_session_id == created["session"]["id"])
            .order_by(MemoirChatMessage.turn_index)
        )
    ).scalars().all()
    assistant_messages = [message for message in messages if message.role == MemoirChatMessageRole.ASSISTANT.value]
    assert len(assistant_messages) == 1
    assistant_message = assistant_messages[0]
    assert assistant_message.id == completed["message_id"]
    assert assistant_message.audio_file_id == completed["audio_file_id"]
    assert assistant_message.metadata_json["source"] == "ai.streaming_assistant_turn"
    assert assistant_message.metadata_json["turn_pipeline"] == "streaming_llm_tts"
    assert assistant_message.metadata_json["provider"] == "fake-ai"
    assert assistant_message.metadata_json["tts_provider"] == "fake-tts"
    assert assistant_message.metadata_json["tts_duration_seconds"] == 0.4
    assert assistant_message.metadata_json["tts_alignment"][0]["text"] == "好"
    assert assistant_message.metadata_json["progress_prompt_version"] == "memoir_voice_progress_context_v2"
    assert assistant_message.metadata_json["progress_update_used"] is True
    assert assistant_message.metadata_json["progress_update"]["text"] == "这个项目已经留下 1 条讲述记录。"
    assert "本轮可选进度鼓励：这个项目已经留下 1 条讲述记录。" in ai_client.calls[0]["user_prompt"]

    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == completed["audio_file_id"]))
    assert audio_file is not None
    assert audio_file.mime_type == "audio/pcm"
    assert audio_file.size_bytes == len(b"audio-0-1audio-0-2")
    audio_relative_path = Path(audio_file.storage_path).relative_to(Path("memoir") / "audio")
    assert (tmp_path / "audio" / audio_relative_path).read_bytes() == b"audio-0-1audio-0-2"


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
        select(func.count(MemoirChatMessage.id))
        .where(
            MemoirChatMessage.voice_session_id == created["session"]["id"],
            MemoirChatMessage.role == MemoirChatMessageRole.ASSISTANT.value,
        )
    )
    assert assistant_count == 0


async def test_voice_assistant_turn_guided_opening_prompt_uses_topic_menu(
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
    assert completed["progress_update_used"] is True
    assert completed["progress_update"]["kind"] == "project_progress_snapshot"
    assert ai_client.calls[0]["kwargs"]["max_tokens"] == 420
    assert "主题讲述首次开场" in prompt
    assert "用“老余”做很短的自我介绍" in prompt
    assert "今天想先讲哪一段" in prompt
    assert "不要念成长列表" in prompt
    assert "人物、事情、家庭、工作、地点、某一天、人生阶段、闲聊" in prompt
    assert "开场最多 2 句" in prompt
    assert "这个项目还没有留下讲述记录，今天可以从第一条开始。" in prompt
    assert "调试阶段每次回应都尽量自然带一句进度鼓励或后台整理结果" in prompt


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
    assert "用“小余”做很短的自我介绍" in prompt


async def test_voice_assistant_turn_progress_prompt_uses_project_snapshot_without_event(
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
    assert completed["progress_update_used"] is True
    assert completed["progress_update"]["kind"] == "project_progress_snapshot"
    assert completed["progress_update"]["valid_utterance_count"] == 2
    assert "当前项目进度：2 条讲述记录，0 条已收入章节，2 条待整理" in prompt
    assert "本轮可选进度鼓励：这个项目已经留下 2 条讲述记录，接下来可以慢慢整理成章节。" in prompt


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
        event_type=MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
        utterance_ids=[created["utterance_id"]],
        summary_text="刚才新增的 1 条讲述已补进《外婆家的院子》。",
        confidence=0.92,
    )
    await test_db.commit()

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["外婆在院子里最常做什么？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert "本轮可选进度鼓励：刚才新增的 1 条讲述已补进《外婆家的院子》。" in prompt
    assert "本轮语音开头会先播报：刚才新增的1条讲述已补进《外婆家的院子》。" in prompt
    assert "你不要重复同一条进度、数字或章节名" in prompt
    assert "只允许引用以下事实" in prompt
    assert completed["text"] == "刚才新增的1条讲述已补进《外婆家的院子》。外婆在院子里最常做什么？"
    assert completed["progress_update"]["kind"] == "automation_event"
    assert completed["progress_update"]["event_ids"] == [event.id]
    assert completed["progress_spoken_text"] == "刚才新增的1条讲述已补进《外婆家的院子》。"

    assistant_message = await test_db.scalar(
        select(MemoirChatMessage).where(MemoirChatMessage.id == completed["message_id"])
    )
    assert assistant_message.metadata_json["progress_update"]["kind"] == "automation_event"
    assert assistant_message.metadata_json["progress_automation_event_ids"] == [event.id]
    assert assistant_message.metadata_json["progress_voice_selected_event_ids"] == [event.id]
    assert assistant_message.metadata_json["progress_spoken_text"] == "刚才新增的1条讲述已补进《外婆家的院子》。"
    await test_db.refresh(event)
    assert event.metadata_json[automation_event_metadata.VOICE_SELECTED_SESSION_ID_KEY] == created["session"]["id"]
    assert event.metadata_json[automation_event_metadata.VOICE_SELECTED_MESSAGE_ID_KEY] == completed["message_id"]
    assert automation_event_metadata.VOICE_PLAYED_AT_KEY not in event.metadata_json


async def test_voice_assistant_turn_strips_host_label_after_progress_prefix(
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
        event_type=MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
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
            "余：嗯，你刚才提到辞职后想做一人公司，",
            "那辞职前最早是怎么进入这个行业的？",
        ],
    )

    assert completed["host_name"] == "小余"
    assert "小余：" not in completed["text"]
    assert completed["text"] == (
        "「职场浮沉与升职加薪」已自动补写4条新增讲述。"
        "嗯，你刚才提到辞职后想做一人公司，那辞职前最早是怎么进入这个行业的？"
    )
    text_deltas = [event["text_delta"] for event in stream_client.sent if event["type"] == "text.delta"]
    assert text_deltas == [
        "「职场浮沉与升职加薪」已自动补写4条新增讲述。",
        "嗯，你刚才提到辞职后想做一人公司，",
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
        event_type=MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
        status=MemoirAutomationEventStatus.FAILED,
        utterance_ids=[created["utterance_id"]],
        summary_text="章节自动补写失败，稍后可重试。",
    )
    await test_db.commit()

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["那我们继续聊外婆家的院子。"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert "本轮可选进度鼓励：章节自动补写失败，稍后可重试。" in prompt
    assert "本轮语音开头会先播报：章节自动补写失败，稍后可重试。" in prompt
    assert completed["progress_update"]["kind"] == "automation_event_failure"
    assert completed["progress_update"]["event_ids"] == [event.id]
    assert completed["progress_update"]["event_status"] == "failed"
    assert completed["progress_spoken_text"] == "章节自动补写失败，稍后可重试。"
    assert completed["text"].startswith("章节自动补写失败，稍后可重试。")


async def test_voice_assistant_turn_speaks_grouped_source_assignment_progress(
    client,
    test_db,
):
    first = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常在外婆家院子里摘菜。",
        guidance_mode="guided_interview",
        current_guidance_module="family",
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        text="春节那几天厨房里总是特别热闹。",
        guidance_mode="guided_interview",
        current_guidance_module="family",
    )
    _, first_chapter = await create_test_book_plan(
        test_db,
        project_id=first["session"]["project_id"],
        source_ref=memoir_source_ref(first),
        chapter_title="外婆家的童年",
    )
    second_chapter = MemoirBookPlanChapter(
        plan_id=first_chapter.plan_id,
        project_id=first["session"]["project_id"],
        title="春节厨房",
        sort_order=1,
    )
    test_db.add(second_chapter)
    await test_db.flush()
    await add_test_chapter_source_links(
        test_db,
        project_id=first["session"]["project_id"],
        plan_id=first_chapter.plan_id,
        chapter_id=second_chapter.id,
        source_refs=[memoir_source_ref(second)],
    )
    first_event = await automation_events.record_automation_event(
        test_db,
        project_id=first["session"]["project_id"],
        event_type=MemoirAutomationEventType.SOURCE_ASSIGNED,
        chapter_id=first_chapter.id,
        utterance_ids=[first["utterance_id"]],
        summary_text="1 条讲述已自动整理进「外婆家的童年」。",
        metadata_json={
            "routes": [
                {
                    "utterance_id": first["utterance_id"],
                    "source_index": 1,
                    "target_chapter_title": "外婆家的童年",
                }
            ]
        },
    )
    second_event = await automation_events.record_automation_event(
        test_db,
        project_id=first["session"]["project_id"],
        event_type=MemoirAutomationEventType.SOURCE_ASSIGNED,
        chapter_id=second_chapter.id,
        utterance_ids=[second["utterance_id"]],
        summary_text="1 条讲述已自动整理进「春节厨房」。",
        metadata_json={
            "routes": [
                {
                    "utterance_id": second["utterance_id"],
                    "source_index": 2,
                    "target_chapter_title": "春节厨房",
                }
            ]
        },
    )
    await test_db.commit()

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=second["session"]["id"],
        user_id=second["user_id"],
        chunks=["那春节厨房里，谁通常最先开始忙？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert "本轮可选进度鼓励：刚才有 2 条讲述已自动整理进 2 个章节" in prompt
    assert "自动归章明细：语音记录 2 -> 「春节厨房」" in prompt
    assert "自动归章明细：语音记录 1 -> 「外婆家的童年」" in prompt
    assert completed["progress_update"]["kind"] == "source_assignment_batch"
    assert set(completed["progress_update"]["event_ids"]) == {first_event.id, second_event.id}
    assert completed["progress_spoken_text"].startswith("刚才有2条讲述已自动整理进2个章节")
    assert completed["text"].startswith(completed["progress_spoken_text"])

    await test_db.refresh(first_event)
    await test_db.refresh(second_event)
    assert first_event.metadata_json[automation_event_metadata.VOICE_SELECTED_MESSAGE_ID_KEY] == completed["message_id"]
    assert second_event.metadata_json[automation_event_metadata.VOICE_SELECTED_MESSAGE_ID_KEY] == completed["message_id"]
    assert automation_event_metadata.VOICE_PLAYED_AT_KEY not in first_event.metadata_json
    assert automation_event_metadata.VOICE_PLAYED_AT_KEY not in second_event.metadata_json


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
        event_type=MemoirAutomationEventType.CHAPTER_AUTO_REVISED,
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
    assert completed["progress_update"]["kind"] != "automation_event"


async def test_voice_assistant_turn_progress_prompt_uses_book_quality_audit_hint(
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
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
        summary_text="当前有 1 个章节需要优先处理。",
        metadata_json={
            "source": "book_quality_audit",
            "overall_status": "needs_updates",
            "next_actions": ["可以追问院子里的童年还有哪些人物细节"],
            "chapter_items": [
                {
                    "chapter_id": "chapter-1",
                    "title": "院子里的童年",
                    "sort_order": 0,
                    "source_count": 2,
                    "pending_source_count": 1,
                    "draft_word_count": 80,
                    "status": "needs_update",
                    "attention": "high",
                    "reason": "章节有新增讲述待补写",
                    "interview_hint": "可以请叙述者补充外婆在院子里常做的事。",
                }
            ],
        },
    )
    await test_db.commit()

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["刚才院子的内容还有空间。外婆在院子里最常做什么？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert "本轮可选进度鼓励：当前有 1 个章节需要优先处理。" in prompt
    assert "可选访谈引导方向：可以请叙述者补充外婆在院子里常做的事。" in prompt
    assert "审计建议只是方向，不是硬性脚本" in prompt
    assert completed["progress_update"]["kind"] == "book_quality_audit"
    assert completed["progress_update"]["event_ids"] == [event.id]
    assert completed["progress_update"]["audit_chapter_title"] == "院子里的童年"

    assistant_message = await test_db.scalar(
        select(MemoirChatMessage).where(MemoirChatMessage.id == completed["message_id"])
    )
    assert assistant_message.metadata_json["progress_update"]["kind"] == "book_quality_audit"
    assert assistant_message.metadata_json["progress_automation_event_ids"] == [event.id]


async def test_voice_assistant_turn_progress_prompt_prefers_semantic_chapter_card_questions(
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
    plan, chapter = await create_test_book_plan(
        test_db,
        project_id=created["session"]["project_id"],
        chapter_title="院子里的童年",
    )
    chapter_card = await upsert_chapter_card(
        test_db,
        project_id=created["session"]["project_id"],
        plan_id=plan.id,
        chapter_id=chapter.id,
        input_signature="chapter-card-signature-1",
        engine="llm_chapter_card_v1",
        card_json={
            "summary": "本章围绕院子里的童年生活。",
            "gaps": [
                {"type": "event_sequence", "severity": "medium", "message": "剥青豆的前后经过还不连贯"}
            ],
            "next_questions": ["外婆在院子里最常做哪件事？"],
            "evidence_refs": [{"utterance_id": created["utterance_id"], "quote": "外婆家院子里摘菜"}],
        },
    )
    audit_snapshot = await upsert_book_quality_audit_snapshot(
        test_db,
        project_id=created["session"]["project_id"],
        engine="llm_semantic_v1",
        input_signature="audit-signature-1",
        overall_status="needs_updates",
        summary_text="语义审计认为「院子里的童年」可以继续补充。",
        display_json={
            "title": "院子里的童年可以继续补问",
            "summary": "章节已有素材，但人物动作和事件经过还可以更具体。",
            "detail": "优先顺着院子、外婆和摘菜继续追问。",
        },
        primary_action_json={
            "type": "continue_interview",
            "cta_text": "继续补问",
            "chapter_id": chapter.id,
        },
        chapter_audits_json=[
            {
                "chapter_id": chapter.id,
                "summary": "审计认为可以补充剥青豆的起因。",
                "gaps": [
                    {"type": "event_sequence", "severity": "medium", "message": "剥青豆事件缺少起因和经过"}
                ],
                "next_questions": ["那天为什么会一起剥青豆？"],
                "evidence_refs": [{"utterance_id": created["utterance_id"], "quote": "外婆家院子里摘菜"}],
            }
        ],
        chapter_card_ids_json=[chapter_card.id],
    )
    event = await automation_events.record_automation_event(
        test_db,
        project_id=created["session"]["project_id"],
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
        summary_text="语义审计认为「院子里的童年」可以继续补充。",
        metadata_json={
            "source": "book_quality_audit",
            "snapshot_id": audit_snapshot.id,
            "snapshot_engine": "llm_semantic_v1",
            "overall_status": "needs_updates",
            "display": audit_snapshot.display_json,
            "primary_action": audit_snapshot.primary_action_json,
        },
    )
    await test_db.commit()

    _, ai_client, _, completed = await _run_stream_turn(
        test_db,
        voice_session_id=created["session"]["id"],
        user_id=created["user_id"],
        chunks=["那院子里和外婆有关的细节，您还记得哪一件？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert "语义追问候选（可改写，不要照读）：外婆在院子里最常做哪件事？" in prompt
    assert "章节缺口提示：剥青豆的前后经过还不连贯" in prompt
    assert "那天为什么会一起剥青豆？" not in prompt
    assert completed["progress_update"]["kind"] == "book_quality_audit"
    assert completed["progress_update"]["event_ids"] == [event.id]
    assert completed["progress_update"]["audit_question_source"] == "chapter_card"
    assert completed["progress_update"]["audit_chapter_title"] == "院子里的童年"


async def test_automation_event_response_does_not_expand_other_project_audit_snapshot(
    client,
    test_db,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常在外婆家院子里摘菜。",
    )
    other_project_response = await client.post("/api/mobile/memoir/projects", json={"title": "另一本回忆录"})
    assert other_project_response.status_code == 201
    other_project_id = other_project_response.json()["id"]
    other_snapshot = await upsert_book_quality_audit_snapshot(
        test_db,
        project_id=other_project_id,
        engine="llm_semantic_v1",
        input_signature="other-project-audit-signature",
        overall_status="needs_updates",
        summary_text="另一个项目的审计摘要。",
        display_json={
            "title": "不应该出现在当前项目",
            "summary": "跨项目 snapshot 不应展开。",
            "detail": "这是一条隔离测试。",
        },
    )
    await automation_events.record_automation_event(
        test_db,
        project_id=created["session"]["project_id"],
        event_type=MemoirAutomationEventType.AUDIT_COMPLETED,
        summary_text="当前项目的事件摘要。",
        metadata_json={
            "source": "book_quality_audit",
            "snapshot_id": other_snapshot.id,
            "snapshot_engine": "llm_semantic_v1",
        },
    )
    await test_db.commit()

    response = await automation_events.list_automation_events(
        test_db,
        user_id=created["user_id"],
        project_id=created["session"]["project_id"],
    )

    assert len(response.items) == 1
    assert "audit_snapshot" not in response.items[0].metadata_json


async def test_voice_assistant_turn_guided_opening_references_previous_session(
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
    assert "主题讲述继续开场" in prompt
    assert "叙述者之前已有同模式讲述记录" in prompt
    assert "今天想先讲哪一段" in prompt
    assert "不要一开场就切进上次的具体细节" in prompt
    assert "想不起来也没关系" in prompt
    assert "叙述者：上次讲到我第一次去上海找工作。" in prompt


async def test_voice_assistant_turn_prompt_marks_explicit_topic_switch(
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
    user_id = (await client.get("/api/auth/me")).json()["id"]

    _, ai_client, _, _ = await _run_stream_turn(
        test_db,
        voice_session_id=voice_session["id"],
        user_id=user_id,
        chunks=["好，那我们聊工作。第一份工作里，最让您记到现在的是哪一件事？"],
    )

    prompt = ai_client.calls[0]["user_prompt"]
    assert ai_client.calls[0]["kwargs"]["max_tokens"] == 320
    assert "明确选择或切换到“工作”主题" in prompt
    assert "如果用户最新一句明确要求换方向，必须尊重最新一句" in prompt
    assert "下一句必须围绕新主题展开，不要继续旧话题" in prompt
    assert "叙述者：今天聊聊工作吧。" in prompt


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
        select(func.count(MemoirChatMessage.id))
        .where(
            MemoirChatMessage.voice_session_id == created["session"]["id"],
            MemoirChatMessage.role == MemoirChatMessageRole.ASSISTANT.value,
        )
    )
    assert assistant_count == 0


async def _run_stream_turn(
    test_db,
    *,
    voice_session_id: str,
    user_id: str,
    chunks: list[str],
    regenerate: bool = False,
    voice: str | None = None,
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


class FakeStreamingAI:
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
        for chunk in self.chunks:
            yield AITextStreamChunk(text_delta=chunk)
        yield AITextStreamChunk(finish_reason="stop", usage={"total_tokens": 42})


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
