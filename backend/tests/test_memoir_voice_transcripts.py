import base64
import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote
from unittest.mock import AsyncMock

from PIL import Image
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.ai_base import AITextServiceBase
from app.config import DEFAULT_AI_TEXT_MODEL
from app.clock import utcnow_naive
from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirEditingJobType,
    MemoirShareVideoJobStatus,
    MemoirTranscriptPolishLevel,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    AudioFile,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirImageAsset,
    MemoirMaterialShareVideo,
    MemoirSection,
    MemoirSectionMaterial,
    MemoirTranscriptAlignment,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.services.memoir import image_assets
from app.services.memoir import share_video_backgrounds
from app.services.memoir import share_video_alignment
from app.services.memoir import share_video_custom_backgrounds
from app.services.memoir import share_video_jobs
from app.services.memoir import share_video_rendering
from app.services.memoir import share_video_scene_planner
from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text
from app.services.asr.streaming import StreamingASREvent
from app.services.memoir.voice_transcripts import (
    MemoirTranscriptCleanupContext,
    _build_cleanup_prompt,
    _remove_obvious_oral_fillers,
    generate_cleaned_transcript_text,
)
from app.services.memoir.voice_gateway_persistence import VoiceGatewayPersistence
from tests.memoir_helpers import (
    create_test_book_plan,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
    post_mobile_memoir_voice_session,
)

UTC_SUFFIXES = ("Z", "+00:00")
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def _stub_optional_video_title_ai(monkeypatch):
    async def no_suggested_title(selected_text, *, ai_model):
        return None, ""

    monkeypatch.setattr(share_video_jobs, "_suggest_share_video_title_and_hook", no_suggested_title)


async def _claim_and_run_share_video_job(test_db, job_id: str) -> bool:
    lease = await share_video_jobs.claim_share_video_job(
        test_db,
        job_id,
        lease_seconds=60,
        max_attempts=3,
    )
    assert lease is not None
    completed = await share_video_jobs.run_share_video_job_with_session(
        test_db,
        lease,
        lease_seconds=60,
        max_attempts=3,
        retry_seconds=1,
    )
    # The test client reuses this session across requests; production requests use fresh sessions.
    test_db.expire_all()
    return completed


class FakeAIClient(AITextServiceBase):
    provider_name = "fake-ai"
    model_name = "fake-cleaner"

    async def _call_llm(self, *args, **kwargs):
        return "我小时候住在外婆家。"


class EchoFillerAIClient(AITextServiceBase):
    provider_name = "fake-ai"
    model_name = "fake-cleaner"

    async def _call_llm(self, *args, **kwargs):
        return "那个就是今天下雨了，那个那个那个雨下的很大。"


class FailingAIClient(AITextServiceBase):
    provider_name = "fake-ai"
    model_name = "fake-cleaner"

    async def _call_llm(self, *args, **kwargs):
        raise RuntimeError("provider down")


class CapturingAIClient(AITextServiceBase):
    provider_name = "fake-ai"
    model_name = "fake-cleaner"

    def __init__(self, response: str = "现在有几个章节啊？") -> None:
        self.response = response
        self.prompt = ""
        self.call_kwargs: dict[str, object] = {}

    async def _call_llm(self, _system_prompt, user_prompt, **kwargs):
        self.prompt = user_prompt
        self.call_kwargs = kwargs
        return self.response


class FakeRecoveryASRClient:
    def __init__(self, events: list[StreamingASREvent]) -> None:
        self.events_to_emit = events
        self.sent_audio: list[bytes] = []
        self.started = False
        self.finished = False
        self.closed = False
        self._finish_event = asyncio.Event()

    async def start(self) -> None:
        self.started = True

    async def send_audio(self, audio: bytes) -> None:
        self.sent_audio.append(audio)

    async def finish(self) -> None:
        self.finished = True
        self._finish_event.set()

    async def events(self):
        await self._finish_event.wait()
        for event in self.events_to_emit:
            yield event

    async def close(self) -> None:
        self.closed = True


async def _append_voice_turn(
    test_db,
    *,
    user_id: str,
    session: dict,
    text: str,
    question_text: str,
    question_turn_index: int,
    user_turn_index: int,
) -> dict:
    audio_file = AudioFile(
        user_id=user_id,
        storage_path=f"memoir/audio/{user_id}/sample-{user_turn_index}.pcm",
        mime_type="audio/pcm",
        size_bytes=3200,
        duration_seconds=8,
    )
    test_db.add(audio_file)
    question_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text=question_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=question_turn_index,
        guidance_intent="follow_up",
        metadata_json={"source": "test.question"},
    )
    test_db.add(question_message)
    version = MemoirTranscriptVersion(
        voice_session_id=session["id"],
        version_type=MemoirTranscriptVersionType.ASR_RAW.value,
        content=text,
        provider="volc_streaming_asr",
        created_by=MemoirCreatedBy.ASR.value,
    )
    test_db.add(version)
    await test_db.flush()
    message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.USER.value,
        content_text=text,
        content_status=MemoirChatContentStatus.FINAL.value,
        audio_file_id=audio_file.id,
        transcript_version_id=version.id,
        turn_index=user_turn_index,
        metadata_json={"source": "test"},
    )
    test_db.add(message)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=session["id"],
        chat_message_id=message.id,
        audio_file_id=audio_file.id,
        transcript_raw=text,
        transcript_edited=text,
        start_ms=0,
        end_ms=8000,
        provider="volc_streaming_asr",
        provider_payload_json={"source": "test"},
    )
    test_db.add(utterance)
    await test_db.commit()
    return {
        "utterance_id": utterance.id,
        "message_id": message.id,
        "question_message_id": question_message.id,
    }


async def _append_human_interview_turn(
    test_db,
    *,
    user_id: str,
    session: dict,
    interviewer_text: str,
    narrator_text: str | None = None,
    later_interviewer_text: str | None = None,
) -> dict:
    interviewer_audio = AudioFile(
        user_id=user_id,
        storage_path=f"memoir/audio/{user_id}/human-interviewer-{len(interviewer_text)}.pcm",
        mime_type="audio/pcm",
        size_bytes=1600,
        duration_seconds=4,
    )
    interviewer_version = MemoirTranscriptVersion(
        voice_session_id=session["id"],
        version_type=MemoirTranscriptVersionType.ASR_RAW.value,
        content=interviewer_text,
        provider="volc_streaming_asr",
        created_by=MemoirCreatedBy.ASR.value,
    )
    test_db.add_all([interviewer_audio, interviewer_version])
    await test_db.flush()
    interviewer_message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=MemoirChatMessageRole.INTERVIEWER.value,
        content_text=interviewer_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        audio_file_id=interviewer_audio.id,
        transcript_version_id=interviewer_version.id,
        turn_index=0,
        metadata_json={"source": "test", "speaker_role": "interviewer", "interview_type": "human_interview"},
    )
    test_db.add(interviewer_message)
    await test_db.flush()
    interviewer_utterance = MemoirVoiceUtterance(
        voice_session_id=session["id"],
        chat_message_id=interviewer_message.id,
        audio_file_id=interviewer_audio.id,
        transcript_raw=interviewer_text,
        transcript_edited="",
        start_ms=0,
        end_ms=4000,
        provider="volc_streaming_asr",
        provider_payload_json={"source": "test"},
    )
    test_db.add(interviewer_utterance)
    await test_db.flush()
    interviewer_version.source_artifact_id = interviewer_utterance.id

    later_interviewer_message = None
    later_interviewer_utterance = None
    if later_interviewer_text is not None:
        later_interviewer_audio = AudioFile(
            user_id=user_id,
            storage_path=f"memoir/audio/{user_id}/human-later-interviewer-{len(later_interviewer_text)}.pcm",
            mime_type="audio/pcm",
            size_bytes=1600,
            duration_seconds=4,
        )
        later_interviewer_version = MemoirTranscriptVersion(
            voice_session_id=session["id"],
            version_type=MemoirTranscriptVersionType.ASR_RAW.value,
            content=later_interviewer_text,
            provider="volc_streaming_asr",
            created_by=MemoirCreatedBy.ASR.value,
        )
        test_db.add_all([later_interviewer_audio, later_interviewer_version])
        await test_db.flush()
        later_interviewer_message = MemoirChatMessage(
            project_id=session["project_id"],
            voice_session_id=session["id"],
            role=MemoirChatMessageRole.INTERVIEWER.value,
            content_text=later_interviewer_text,
            content_status=MemoirChatContentStatus.FINAL.value,
            audio_file_id=later_interviewer_audio.id,
            transcript_version_id=later_interviewer_version.id,
            turn_index=1,
            metadata_json={"source": "test", "speaker_role": "interviewer", "interview_type": "human_interview"},
        )
        test_db.add(later_interviewer_message)
        await test_db.flush()
        later_interviewer_utterance = MemoirVoiceUtterance(
            voice_session_id=session["id"],
            chat_message_id=later_interviewer_message.id,
            audio_file_id=later_interviewer_audio.id,
            transcript_raw=later_interviewer_text,
            transcript_edited="",
            start_ms=4100,
            end_ms=8100,
            provider="volc_streaming_asr",
            provider_payload_json={"source": "test"},
        )
        test_db.add(later_interviewer_utterance)
        await test_db.flush()
        later_interviewer_version.source_artifact_id = later_interviewer_utterance.id

    narrator_message = None
    narrator_utterance = None
    if narrator_text is not None:
        narrator_turn_index = 2 if later_interviewer_message else 1
        narrator_audio = AudioFile(
            user_id=user_id,
            storage_path=f"memoir/audio/{user_id}/human-narrator-{len(narrator_text)}.pcm",
            mime_type="audio/pcm",
            size_bytes=3200,
            duration_seconds=8,
        )
        narrator_version = MemoirTranscriptVersion(
            voice_session_id=session["id"],
            version_type=MemoirTranscriptVersionType.ASR_RAW.value,
            content=narrator_text,
            provider="volc_streaming_asr",
            created_by=MemoirCreatedBy.ASR.value,
        )
        test_db.add_all([narrator_audio, narrator_version])
        await test_db.flush()
        narrator_message = MemoirChatMessage(
            project_id=session["project_id"],
            voice_session_id=session["id"],
            role=MemoirChatMessageRole.USER.value,
            content_text=narrator_text,
            content_status=MemoirChatContentStatus.FINAL.value,
            audio_file_id=narrator_audio.id,
            transcript_version_id=narrator_version.id,
            parent_message_id=interviewer_message.id,
            turn_index=narrator_turn_index,
            metadata_json={"source": "test", "speaker_role": "narrator", "interview_type": "human_interview"},
        )
        test_db.add(narrator_message)
        await test_db.flush()
        narrator_utterance = MemoirVoiceUtterance(
            voice_session_id=session["id"],
            chat_message_id=narrator_message.id,
            audio_file_id=narrator_audio.id,
            transcript_raw=narrator_text,
            transcript_edited="",
            start_ms=4100,
            end_ms=12100,
            provider="volc_streaming_asr",
            provider_payload_json={"source": "test"},
        )
        test_db.add(narrator_utterance)
        await test_db.flush()
        narrator_version.source_artifact_id = narrator_utterance.id

    await test_db.commit()
    return {
        "interviewer_message_id": interviewer_message.id,
        "interviewer_utterance_id": interviewer_utterance.id,
        "later_interviewer_message_id": later_interviewer_message.id if later_interviewer_message else None,
        "later_interviewer_utterance_id": later_interviewer_utterance.id if later_interviewer_utterance else None,
        "narrator_message_id": narrator_message.id if narrator_message else None,
        "narrator_utterance_id": narrator_utterance.id if narrator_utterance else None,
    }


async def test_mobile_memoir_lists_saved_voice_transcripts(client, test_db):
    created = await create_test_voice_transcript(client, test_db, question_text="你小时候住在哪里？")

    response = await client.get("/api/mobile/memoir/voice-transcripts")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["limit"] == 50
    item = data["items"][0]
    assert item["id"] == created["utterance_id"]
    assert item["voice_session_id"] == created["session"]["id"]
    assert item["project_title"] == "测试回忆录"
    assert item["chat_message_id"] == created["message_id"]
    assert item["audio_file_id"] == created["audio_file_id"]
    assert item["question_message_id"] == created["question_message_id"]
    assert item["question_text"] == "你小时候住在哪里？"
    assert item["question_audio_file_id"] == created["question_audio_file_id"]
    assert item["audio_mime_type"] == "audio/pcm"
    assert item["audio_size_bytes"] == 3200
    assert item["audio_duration_seconds"] == 8
    assert item["display_text"] == "嗯，我小时候就是住在外婆家。"
    assert item["summary_title"] == "嗯，我小时候就是住在外婆家。"
    assert item["summary_text"] == "嗯，我小时候就是住在外婆家。"
    assert item["summary_source"] == "fallback"
    assert item["transcription_status"] == "ready"
    assert item["can_recover_transcription"] is False
    assert item["display_char_count"] == len("嗯，我小时候就是住在外婆家。")
    assert item["is_included_in_book"] is False
    assert item["included_chapters"] == []
    assert item["created_at"].endswith(UTC_SUFFIXES)
    assert item["session_created_at"].endswith(UTC_SUFFIXES)


async def test_mobile_memoir_voice_transcript_share_video_context_aligns_edited_text(
    client, test_db, monkeypatch
):
    monkeypatch.setattr(
        share_video_alignment,
        "get_settings",
        lambda: SimpleNamespace(
            memoir_share_video_ai_background_enabled=True,
            volc_ark_api_key="test-key",
        ),
    )
    raw_text = "是我们心不平的吧。我就带你回去。"
    edited_text = "说我们的心不平的嘛。我就带你回去。"
    created = await create_test_voice_transcript(client, test_db, text=raw_text)
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    assert utterance is not None
    utterance.transcript_edited = edited_text
    utterance.provider_payload_json = {
        "payload": {
            "audio_info": {"duration": 12000},
            "result": {
                "text": raw_text,
                "utterances": [
                    {
                        "start_time": 0,
                        "end_time": 5200,
                        "text": "是我们心不平的吧。",
                        "words": [
                            {"text": "是", "start_time": 0, "end_time": 500},
                            {"text": "我", "start_time": 600, "end_time": 900},
                            {"text": "们", "start_time": 900, "end_time": 1200},
                            {"text": "心", "start_time": 1400, "end_time": 1700},
                            {"text": "不", "start_time": 1700, "end_time": 2000},
                            {"text": "平", "start_time": 2000, "end_time": 2300},
                            {"text": "的", "start_time": 2600, "end_time": 2900},
                            {"text": "吧", "start_time": 3300, "end_time": 3800},
                        ],
                    },
                    {
                        "start_time": 6200,
                        "end_time": 10400,
                        "text": "我就带你回去。",
                        "words": [
                            {"text": "我", "start_time": 6200, "end_time": 6600},
                            {"text": "就", "start_time": 6600, "end_time": 7000},
                            {"text": "带", "start_time": 7200, "end_time": 7600},
                            {"text": "你", "start_time": 7600, "end_time": 8000},
                            {"text": "回", "start_time": 8400, "end_time": 8800},
                            {"text": "去", "start_time": 8800, "end_time": 9300},
                        ],
                    },
                ],
            },
        }
    }
    await test_db.commit()

    response = await client.get(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/context"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["utterance_id"] == created["utterance_id"]
    assert data["audio_file_id"] == created["audio_file_id"]
    assert data["transcript_source"] == "edited"
    assert data["transcript_text"] == edited_text
    assert data["duration_ms"] == 12000
    assert data["alignment_strategy"] == "provider_words"
    assert data["alignment_confidence"] >= 0.7
    assert data["ai_background_enabled"] is True
    assert data["default_background_source"] == "ai"
    assert [source["value"] for source in data["background_sources"]] == ["ai", "custom", "classic"]
    assert data["default_background_mode"] == "auto"
    assert [mode["value"] for mode in data["background_modes"]] == ["auto", "single", "smart_multi"]
    assert [segment["text"] for segment in data["segments"]] == [
        "说我们的心不平的嘛。",
        "我就带你回去。",
    ]
    assert data["segments"][0]["start_char"] == 0
    assert 3000 <= data["segments"][0]["end_ms"] <= 6200
    assert data["segments"][1]["start_ms"] >= data["segments"][0]["end_ms"]
    alignment = await test_db.scalar(
        select(MemoirTranscriptAlignment).where(
            MemoirTranscriptAlignment.utterance_id == created["utterance_id"]
        )
    )
    assert alignment is not None
    assert alignment.text_hash == data["text_hash"]
    assert alignment.segments_json[0]["text"] == "说我们的心不平的嘛。"


@pytest.mark.parametrize(
    ("enabled", "api_key"),
    [(False, "test-key"), (True, "")],
)
async def test_share_video_context_hides_unavailable_ai_and_creation_rejects_it(
    client, test_db, monkeypatch, enabled, api_key
):
    settings = SimpleNamespace(
        memoir_share_video_ai_background_enabled=enabled,
        volc_ark_api_key=api_key,
    )
    monkeypatch.setattr(share_video_alignment, "get_settings", lambda: settings)
    monkeypatch.setattr(share_video_jobs, "get_settings", lambda: settings)
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    end_char = text.index("。") + 1
    url = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video"

    context = (await client.get(f"{url}/context")).json()
    assert context["ai_background_enabled"] is False
    assert context["default_background_source"] == "classic"
    assert [source["value"] for source in context["background_sources"]] == ["custom", "classic"]

    rejected = await client.post(
        f"{url}/jobs",
        json={"start_char": 0, "end_char": end_char, "background_source": "ai"},
    )
    assert rejected.status_code == 400
    created_job = await client.post(
        f"{url}/jobs", json={"start_char": 0, "end_char": end_char}
    )
    assert created_job.status_code == 201
    assert created_job.json()["background_source"] == "classic"


async def test_share_video_context_returns_latest_job_only_for_current_text(client, test_db):
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)

    initial_context = await client.get(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/context"
    )

    assert initial_context.status_code == 200
    assert initial_context.json()["latest_job_id"] is None

    create_response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={
            "start_char": 0,
            "end_char": len(text),
            "background_source": "classic",
        },
    )

    assert create_response.status_code == 201
    job_id = create_response.json()["id"]

    resumable_context = await client.get(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/context"
    )

    assert resumable_context.status_code == 200
    assert resumable_context.json()["latest_job_id"] == job_id

    utterance = await test_db.get(MemoirVoiceUtterance, created["utterance_id"])
    assert utterance is not None
    utterance.transcript_edited = f"{text}这是后来补充的内容。"
    await test_db.commit()

    changed_context = await client.get(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/context"
    )

    assert changed_context.status_code == 200
    assert changed_context.json()["latest_job_id"] is None


def test_share_video_captions_use_memoir_page_layout_and_packaged_font():
    text = "对孙女的偏爱与心平之辩"
    timings = [{"start_ms": index * 100, "end_ms": (index + 1) * 100} for index, _char in enumerate(text)]

    pages = share_video_rendering._caption_pages(text, timings, len(text) * 100)

    assert len(pages) == 1
    assert [text[line.start:line.end] for line in pages[0].lines] == ["对孙女的偏爱", "与心平之辩"]
    assert (
        share_video_rendering._SHARE_VIDEO_FONT_DIR / share_video_rendering._SHARE_VIDEO_FONT_FILE
    ).is_file()


def test_share_video_subtitle_ranges_do_not_split_tiny_fragments():
    text = (
        "那阵几个娃娃，屋头没得劳动力，要挣工分。我就在外面拿点低工资。"
        "那阵工资只有那么高，拿不到好多，拿不到好多点工资，分的是低口粮，"
        "生产队分的是最少的，工分要工分粮，你没有劳动力，工分少，"
        "分粮也少，所以尽分的低口粮。那个日子也够过了。她还是好，在屋头，"
        "分点胡豆，分点那个胡豆，煮好后接起。我那阵在县上公交局，县医院，"
        "在新繁公交局住起，她都要把一点胡豆给我端来，分点吃的胡豆，有半碗碗。"
    )

    page_ranges = share_video_rendering._subtitle_ranges(
        text,
        max_chars=share_video_rendering._QUOTE_PAGE_MAX_CHARS,
    )
    page_texts = [text[start:end].strip() for start, end in page_ranges]
    line_texts = [
        text[line_start:line_end].strip()
        for page_start, page_end in page_ranges
        for line_start, line_end in share_video_rendering._quote_line_ranges(text, page_start, page_end)
    ]

    assert "粮。" not in page_texts
    assert "，" not in page_texts
    assert "，" not in line_texts
    assert all(len(fragment) >= 4 for fragment in page_texts)
    assert all(fragment[0] not in "，,、。！？!?；;" for fragment in line_texts)


def test_share_video_long_scenes_become_several_moving_shots(tmp_path):
    first, second = tmp_path / "scene-1.png", tmp_path / "scene-2.png"
    for path in (first, second):
        Image.new("RGB", (900, 1600), (120, 110, 90)).save(path)

    shots = share_video_rendering._picture_shots([(first, 0.0, 12.0), (second, 12.0, 15.0)])

    assert [(shot.start, shot.end) for shot in shots] == [(0.0, 6.0), (6.0, 12.0), (12.0, 15.0)]
    assert len({shot.move for shot in shots}) == 3
    assert shots[0].canvas is shots[1].canvas
    frame = share_video_rendering._shots_visual(shots)(11.9)
    assert frame.size == (share_video_rendering._VIDEO_WIDTH, share_video_rendering._VIDEO_HEIGHT)


def test_share_video_background_prompts_cover_default_styles():
    options = share_video_backgrounds.share_video_background_style_options()
    assert [option["value"] for option in options] == [
        "watercolor",
        "ink_paper",
        "old_album",
    ]
    assert [option["label"] for option in options] == [
        "清透水彩",
        "水墨淡彩",
        "怀旧影像",
    ]
    mode_options = share_video_backgrounds.share_video_background_mode_options()
    assert [option["value"] for option in mode_options] == ["auto", "single", "smart_multi"]
    assert [option["label"] for option in mode_options] == ["自动", "单张背景", "智能多图"]

    watercolor = share_video_backgrounds.build_share_video_background_prompt(
        "周末一家人一起在院子里吃饭。",
        None,
    )
    pencil = share_video_backgrounds.build_share_video_background_prompt(
        "母亲常在窗边缝补衣服。",
        "pencil",
    )
    ink_paper = share_video_backgrounds.build_share_video_background_prompt(
        "雨后沿着河边慢慢走回家。",
        "ink_paper",
    )
    gouache = share_video_backgrounds.build_share_video_background_prompt(
        "第一次到县城上学，街上很热闹。",
        "gouache",
    )
    old_album = share_video_backgrounds.build_share_video_background_prompt(
        "爷爷说偏爱孙女，但做事情心里要讲公道。",
        "old_album",
    )
    storybook = share_video_backgrounds.build_share_video_background_prompt(
        "小时候一家人在老房子里吃饭，后来去县城读书。",
        "storybook",
    )

    assert watercolor.style.value == "watercolor"
    assert "透明水色" in watercolor.prompt
    assert "石墨铅笔" in pencil.prompt
    assert "现代水墨淡彩" in ink_paper.prompt
    assert "叙事水粉画" in gouache.prompt
    assert "银盐胶片颗粒" in old_album.prompt
    assert "二十世纪中国老连环画" in storybook.prompt
    assert {"pencil", "gouache", "storybook"}.isdisjoint(option["value"] for option in options)
    for prompt in (watercolor, pencil, ink_paper, gouache, old_album, storybook):
        assert "画面上方约六成" in prompt.prompt
        assert "下方约三分之一" in prompt.prompt
        assert "不出现文字" in prompt.prompt
        assert "竖版 9:16" in prompt.prompt
        assert "不要因为这是回忆录内容" not in prompt.prompt

    long_prompt = share_video_backgrounds.build_share_video_background_prompt(
        "乡" * 420,
        "watercolor",
    )
    assert len(long_prompt.content) == share_video_backgrounds.SHARE_VIDEO_BACKGROUND_CONTENT_MAX_CHARS + 3
    assert long_prompt.content.endswith("...")


def test_share_video_background_prompt_prioritizes_spoken_scene_details():
    spoken_text = "小时候在老房子的院子里，爷爷摘了一朵栀子花送给我。"

    prompt = share_video_backgrounds.build_share_video_background_prompt(spoken_text, None)

    assert prompt.template_version == "memoir-share-video-background-v9"
    assert spoken_text in prompt.prompt
    assert "一个具体、可见的回忆瞬间" in prompt.prompt
    assert "时间、地点、人物关系、动作和关键物件" in prompt.prompt
    assert "不要用泛泛的漂亮风景代替讲述的场景" in prompt.prompt
    assert "原文没有说明的年代、地点、人物身份或生活细节不要猜测" in prompt.prompt
    assert "下方约三分之一" in prompt.prompt


def test_share_video_scene_planner_fallback_splits_by_duration_and_punctuation():
    text = "小时候一家人住在老房子里。后来去县城读书。再后来大家各自成家。"
    char_timings = [
        {"start_ms": index * 1000, "end_ms": (index + 1) * 1000}
        for index, _char in enumerate(text)
    ]

    plan = share_video_scene_planner.fallback_share_video_scene_plan(
        text,
        char_timings,
        38_000,
    )
    metadata = share_video_scene_planner.scene_plan_to_metadata(plan)

    assert plan.provider == "fallback"
    assert plan.strategy == "sentence_units"
    assert len(plan.scenes) == 3
    assert plan.scenes[0].start_char == 0
    assert plan.scenes[-1].end_char == len(text)
    assert all(scene.end_ms > scene.start_ms for scene in plan.scenes)
    assert all(text[scene.end_char - 1] == "。" for scene in plan.scenes)
    assert [scene.sentence_ids for scene in plan.scenes] == [(0,), (1,), (2,)]
    assert metadata["version"] == share_video_scene_planner.SHARE_VIDEO_SCENE_PLAN_VERSION
    assert len(metadata["scenes"]) == 3
    assert metadata["scenes"][0]["sentence_ids"] == [0]
    assert metadata["scenes"][0]["evidence_quote"]


def test_share_video_scene_count_follows_content_not_duration_buckets():
    assert share_video_scene_planner._scene_count_range(60_000, 1, None) == (1, 1)
    assert share_video_scene_planner._scene_count_range(60_000, 20, None) == (1, 5)
    assert share_video_scene_planner._scene_count_range(14_000, 20, None) == (1, 2)


def test_share_video_scene_background_prompt_carries_story_context():
    prompt = share_video_backgrounds.build_share_video_background_prompt(
        "他把围巾围在我脖子上。",
        "watercolor",
        story_text="那年冬天父亲送我去县城读书。他把围巾围在我脖子上。",
        visual_context="父亲四十来岁，穿旧棉袄",
    ).prompt
    single = share_video_backgrounds.build_share_video_background_prompt("他把围巾围在我脖子上。", "watercolor").prompt

    assert "那年冬天父亲送我去县城读书。" in prompt
    assert "父亲四十来岁，穿旧棉袄" in prompt
    assert "整段口述" not in single
    assert "讲述者资料" not in single
    narrated = share_video_backgrounds.build_share_video_background_prompt(
        "我们去河边洗衣服。",
        "watercolor",
        narrator_context=share_video_backgrounds.share_video_narrator_context(
            {"narrator_gender": "female", "narrator_origin": "湖南"}
        ),
    ).prompt
    assert "（人物性别、年龄先从口述内容推断，内容里看不出来时再参考）：讲述者“我”是女性；家乡在湖南。" in narrated
    noted = share_video_backgrounds.build_share_video_background_prompt(
        "我们去河边洗衣服。", "watercolor", background_note="“我们”是我和妹妹"
    ).prompt
    assert "用户补充的人物背景（人物性别、年龄和关系以此为准）：“我们”是我和妹妹" in noted
    assert "不要默认画成男性" not in narrated
    assert share_video_backgrounds.share_video_narrator_context({"narrator_gender": "unspecified"}) == ""


async def test_share_video_background_set_requests_one_group_and_keeps_missing_images_empty(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        memoir_share_video_ai_background_enabled=True,
        volc_ark_api_key="test-key",
        volc_ark_base_url="https://ark.cn-beijing.volces.com/api/v3",
        volc_ark_image_model="doubao-seedream-5-0-lite-260128",
        volc_ark_image_size="2K",
        volc_ark_image_output_format="png",
        volc_ark_image_response_format="b64_json",
        volc_ark_image_timeout_seconds=1,
        volc_ark_image_max_concurrent_requests=1,
    )
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        image = base64.b64encode(b"fake image bytes").decode()
        return httpx.Response(
            200,
            json={
                "model": settings.volc_ark_image_model,
                "data": [{"b64_json": image}, {"error": {"code": "OutputImageSensitiveContentDetected"}}],
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        share_video_backgrounds.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(share_video_backgrounds, "get_settings", lambda: settings)
    monkeypatch.setattr(share_video_backgrounds, "MEMOIR_SHARE_VIDEO_BACKGROUND_DIR", tmp_path / "cache")

    assets = await share_video_backgrounds.get_or_generate_share_video_background_set(
        ["小时候住在老房子里。", "后来去县城读书。", "再后来大家各自成家。"],
        "watercolor",
        story_text="小时候住在老房子里。后来去县城读书。再后来大家各自成家。",
    )

    assert len(payloads) == 1
    assert payloads[0]["sequential_image_generation"] == "auto"
    assert payloads[0]["sequential_image_generation_options"] == {"max_images": 3}
    assert "image" not in payloads[0]
    assert "第 2 张：后来去县城读书。" in payloads[0]["prompt"]
    assert assets is not None
    assert assets[0] is not None and assets[0].path.read_bytes() == b"fake image bytes"
    assert assets[1:] == [None, None]


def test_share_video_scene_planner_prompt_uses_sentence_ids_not_char_offsets():
    text = "小时候一家人住在老房子里。后来去县城读书。再后来大家各自成家。"
    char_timings = [
        {"start_ms": index * 1000, "end_ms": (index + 1) * 1000}
        for index, _char in enumerate(text)
    ]
    sentences = share_video_scene_planner.share_video_sentence_units(text, char_timings, 38_000)

    prompt = share_video_scene_planner._user_prompt(text, sentences, 38_000)

    assert "sentence_ids" in prompt
    assert "不要输出 start_char/end_char" in prompt
    assert "每个 scene 只包含 sentence_ids" in prompt
    assert "0. [0.0s-" in prompt
    assert "小时候一家人住在老房子里。" in prompt
    assert "每个 scene 包含 start_char、end_char" not in prompt
    assert "image_prompt_content" not in prompt
    assert "visual_summary" not in prompt
    assert "evidence_quote" not in prompt


@pytest.mark.parametrize("sentence_groups", [[[0, 1, 2, 3]], [[0, 1], [2, 3]]])
async def test_share_video_planned_people_reach_single_and_group_image_requests(
    sentence_groups, tmp_path, monkeypatch,
):
    orchestration = share_video_jobs.background_orchestration
    text = (
        "我们还是走过那条路线。到了夏天，那条路线上还有桑葚。对，桑葚。"
        "到了夏末，晚上就会有萤火虫，星星点点的，有的萤火虫会趴在路边的草丛上，"
        "有的会在路上的空中飞来飞去。甚至我们还看到过两次猫头鹰，"
        "夜晚的时候悄无声息地从头顶上飞过去。"
    )
    person_count = "至少2人，准确人数未说明"
    visual_context = "讲述者“我”与同伴（关系未说明）在夏末的夜晚走过固定路线；讲述者为女性。"
    narrator = share_video_backgrounds.share_video_narrator_context({"narrator_gender": "female", "narrator_age": 40})
    monkeypatch.setattr(share_video_scene_planner, "get_settings", lambda: SimpleNamespace(
        memoir_share_video_scene_planner_enabled=True,
        memoir_share_video_scene_planner_model="deepseek-flash",
    ))
    monkeypatch.setattr(share_video_scene_planner, "get_ai_service", lambda _: SimpleNamespace(
        provider_name="deepseek", model_name="deepseek-flash",
    ))

    async def fake_generate(_client, system_prompt, user_prompt, *, output_model, **kwargs):
        assert narrator in user_prompt
        assert "person_count" in user_prompt
        return SimpleNamespace(value=output_model.model_validate({
            "person_count": person_count,
            "visual_context": visual_context,
            "scenes": [{"sentence_ids": ids} for ids in sentence_groups],
        }))

    monkeypatch.setattr(share_video_scene_planner, "generate_structured_with_client", fake_generate)
    image_prompts = []
    asset = share_video_backgrounds.ShareVideoBackgroundAsset(
        path=tmp_path / "scene.png", storage_path="memoir/share-video-backgrounds/scene.png",
        style_id="watercolor", prompt_hash="test", prompt_template_version="test",
        provider="volc_ark", model="test", cache_hit=False,
    )

    async def fake_single(selected_text, style_id, *, usage_context=None, **context):
        image_prompts.append(share_video_backgrounds.build_share_video_background_prompt(
            selected_text, style_id, **context,
        ).prompt)
        return asset

    async def fake_group(scene_texts, style_id, *, usage_context=None, **context):
        image_prompts.append(share_video_backgrounds.build_share_video_background_set_prompt(
            scene_texts, style_id, **context,
        ).prompt)
        return [asset] * len(scene_texts)

    monkeypatch.setattr(orchestration, "get_or_generate_share_video_background", fake_single)
    monkeypatch.setattr(orchestration, "get_or_generate_share_video_background_set", fake_group)
    monkeypatch.setattr(orchestration, "register_share_video_background_asset", AsyncMock())
    metadata = orchestration.ShareVideoJobMetadata(background_mode="smart_multi")
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(metadata_json={
        "memoir_profile": {"narrator_gender": "female", "narrator_age": 40},
    })))
    prepared = await orchestration.prepare_share_video_background(
        db, job=SimpleNamespace(project_id="test", selected_text=text, start_ms=0, end_ms=31_400),
        metadata=metadata, persist_progress=AsyncMock(), ai_background_enabled=True, volc_ark_api_key="test",
    )

    assert metadata.background_status == "ready"
    assert metadata.background_scene_plan.strategy == "llm_sentence_units"
    restored = orchestration.ShareVideoJobMetadata.from_json(metadata.to_json())
    assert restored.background_scene_plan.person_count == person_count
    assert restored.background_scene_plan.visual_context == visual_context
    assert len(prepared.segments) == len(sentence_groups)
    assert len(image_prompts) == 1
    assert person_count in image_prompts[0] and visual_context in image_prompts[0]


def test_share_video_scene_planner_normalizes_continuous_sentence_groups():
    text = "小时候一家人住在老房子里。后来去县城读书。再后来大家各自成家。"
    char_timings = [
        {"start_ms": index * 1000, "end_ms": (index + 1) * 1000}
        for index, _char in enumerate(text)
    ]
    sentences = share_video_scene_planner.share_video_sentence_units(text, char_timings, 38_000)

    scenes = share_video_scene_planner._normalize_llm_sentence_scenes(
        {
            "scenes": [
                {"sentence_ids": [0, 1]},
                {"sentence_ids": [2]},
            ]
        },
        text,
        sentences,
        char_timings,
        38_000,
        2,
        3,
    )
    invalid_gap = share_video_scene_planner._normalize_llm_sentence_scenes(
        {"scenes": [{"sentence_ids": [0]}, {"sentence_ids": [2]}]},
        text,
        sentences,
        char_timings,
        38_000,
        2,
        3,
    )
    invalid_offsets = share_video_scene_planner._normalize_llm_sentence_scenes(
        {"scenes": [{"start_char": 0, "end_char": 5}, {"start_char": 5, "end_char": len(text)}]},
        text,
        sentences,
        char_timings,
        38_000,
        2,
        3,
    )

    assert len(scenes) == 2
    assert scenes[0].sentence_ids == (0, 1)
    assert scenes[0].start_char == 0
    assert scenes[0].end_char == sentences[1].end_char
    assert scenes[0].evidence_quote.startswith("小时候一家人住在老房子里")
    assert scenes[1].start_char == sentences[2].start_char
    assert scenes[1].end_char == len(text)
    assert scenes[1].evidence_quote == "再后来大家各自成家。"
    assert invalid_gap == []
    assert invalid_offsets == []


async def test_mobile_memoir_uploads_custom_share_video_background(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(share_video_custom_backgrounds, "DATA_DIR", tmp_path)
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)
    process_calls: list[tuple[Path, Path]] = []

    async def fake_process_background_image(source_path: Path, target_path: Path) -> tuple[int, int]:
        process_calls.append((source_path, target_path))
        assert source_path.read_bytes() == PNG_1X1
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"processed background jpg")
        return 1080, 1440

    monkeypatch.setattr(
        share_video_custom_backgrounds,
        "_process_background_image",
        fake_process_background_image,
    )
    project_id = await ensure_mobile_memoir_project(client)

    response = await client.post(
        "/api/mobile/memoir/share-video/background-assets",
        data={"project_id": project_id, "processing_preset": "memoir_soft"},
        files={"file": ("background.png", PNG_1X1, "image/png")},
    )
    duplicate_response = await client.post(
        "/api/mobile/memoir/share-video/background-assets",
        data={"project_id": project_id, "processing_preset": "memoir_soft"},
        files={"file": ("background-again.png", PNG_1X1, "image/png")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert duplicate_response.status_code == 201
    assert duplicate_response.json()["id"] == payload["id"]
    assert len(process_calls) == 1
    assert payload["project_id"] == project_id
    assert payload["status"] == "ready"
    assert payload["preview_url"] == f"/api/mobile/memoir/projects/{project_id}/image-assets/{payload['id']}/file"
    assert payload["mime_type"] == "image/jpeg"
    assert payload["width"] == 1080
    assert payload["height"] == 1440
    assert payload["processing_preset"] == "memoir_soft"
    assert payload["original_mime_type"] == "image/png"
    assert payload["original_size_bytes"] == len(PNG_1X1)

    asset = await test_db.scalar(
        select(MemoirImageAsset).where(MemoirImageAsset.id == payload["id"])
    )
    assert asset is not None
    assert asset.source_type == "share_video_background"
    assert asset.style_id == "custom"
    assert asset.provider == "user_upload"
    assert asset.metadata_json["share_video_background_source"] == "custom_upload"
    assert asset.metadata_json["original_storage_path"].endswith("/original.png")
    assert asset.metadata_json["processing_json"]["fit"] == "keep_aspect"

    preview_response = await client.get(payload["preview_url"])

    assert preview_response.status_code == 200
    assert preview_response.headers["content-type"].startswith("image/jpeg")
    assert preview_response.content == b"processed background jpg"


async def test_mobile_memoir_rejects_non_image_custom_share_video_background(client):
    project_id = await ensure_mobile_memoir_project(client)

    response = await client.post(
        "/api/mobile/memoir/share-video/background-assets",
        data={"project_id": project_id},
        files={"file": ("background.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 400
    assert "JPEG、PNG 或 WebP" in response.json()["detail"]


async def test_share_video_background_generation_caches_provider_result(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        memoir_share_video_ai_background_enabled=True,
        volc_ark_api_key="test-key",
        volc_ark_base_url="https://ark.cn-beijing.volces.com/api/v3",
        volc_ark_image_model="doubao-seedream-5-0-lite-260128",
        volc_ark_image_size="2K",
        volc_ark_image_output_format="png",
        volc_ark_image_response_format="b64_json",
        volc_ark_image_timeout_seconds=1,
        volc_ark_image_max_concurrent_requests=1,
    )
    calls: list[str] = []

    async def fake_generate(self, prompt):
        calls.append(prompt)
        return share_video_backgrounds.VolcArkGeneratedImage(
            content=b"fake image bytes",
            mime_type="image/png",
            model=settings.volc_ark_image_model,
            usage={},
        )

    monkeypatch.setattr(share_video_backgrounds, "get_settings", lambda: settings)
    monkeypatch.setattr(share_video_backgrounds, "MEMOIR_SHARE_VIDEO_BACKGROUND_DIR", tmp_path)
    monkeypatch.setattr(share_video_backgrounds.VolcArkImageGenerationService, "generate", fake_generate)

    first = await share_video_backgrounds.get_or_generate_share_video_background(
        "爷爷说偏爱孙女，但做事情心里要讲公道。",
        "old_album",
    )
    second = await share_video_backgrounds.get_or_generate_share_video_background(
        "爷爷说偏爱孙女，但做事情心里要讲公道。",
        "old_album",
    )

    assert first is not None
    assert second is not None
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.path == second.path
    assert first.path.read_bytes() == b"fake image bytes"
    assert len(calls) == 1


@pytest.mark.parametrize("suggested_title", ["外婆家的冬天", None])
async def test_mobile_memoir_creates_and_downloads_share_video_job(
    client, test_db, tmp_path, monkeypatch, suggested_title,
):
    monkeypatch.setattr(
        "app.services.memoir.share_video_jobs.MEMOIR_SHARE_VIDEO_DIR",
        tmp_path / "share-videos",
    )

    render_requests = []

    async def fake_render_share_video_clip(request):
        render_requests.append(request)
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        request.target_path.write_bytes(b"fake mp4")

    monkeypatch.setattr(
        "app.services.memoir.share_video_rendering.render_share_video_clip",
        fake_render_share_video_clip,
    )

    monkeypatch.setattr(
        "app.services.memoir.share_video_jobs.get_settings",
        lambda: SimpleNamespace(
            memoir_share_video_ai_background_enabled=True,
            volc_ark_api_key="test-key",
        ),
    )

    async def fail_background_generation(_selected_text, _style_id, *, narrator_context="", background_note="", usage_context=None):
        raise share_video_backgrounds.ShareVideoBackgroundGenerationError("provider unavailable")

    monkeypatch.setattr(
        "app.services.memoir.share_video_background_orchestration.get_or_generate_share_video_background",
        fail_background_generation,
    )

    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    end_char = text.index("。") + 1
    fallback_title = "我小时候住在外婆家"
    expected_title = suggested_title or fallback_title

    async def suggest_title(selected_text, *, ai_model):
        assert selected_text == text[:end_char]
        if suggested_title is None:
            raise RuntimeError("title provider unavailable")
        return suggested_title, ""

    monkeypatch.setattr(share_video_jobs, "_suggest_share_video_title_and_hook", suggest_title)

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={"start_char": 0, "end_char": end_char, "background_style": "ink_paper"},
    )

    assert response.status_code == 201
    created_job = response.json()
    assert created_job["status"] == "pending"
    assert created_job["title"] == fallback_title
    assert created_job["output"] is None
    job_id = created_job["id"]
    assert await _claim_and_run_share_video_job(test_db, job_id) is True
    detail_response = await client.get(f"/api/mobile/memoir/share-video/jobs/{job_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail is not None
    assert detail["status"] == "succeeded"
    assert detail["title"] == expected_title
    assert detail["selected_text"] == "我小时候住在外婆家。"
    assert detail["duration_ms"] >= 3000
    assert detail["background_style"] == "ink_paper"
    assert detail["background_mode"] == "auto"
    assert detail["background_effective_mode"] == "single"
    assert detail["background_status"] == "failed"
    assert "provider unavailable" in detail["background_error_message"]
    assert detail["output"]["size_bytes"] == len(b"fake mp4")
    assert detail["output"]["download_url"] == f"/api/mobile/memoir/share-video/jobs/{job_id}/download"
    assert not {
        "video_storage_path",
        "video_mime_type",
        "video_size_bytes",
        "download_url",
    } & detail.keys()
    openapi = (await client.get("/openapi.json")).json()
    response_schema = openapi["components"]["schemas"]["MemoirShareVideoJobResponse"]
    assert "output" in response_schema["required"]
    assert len(render_requests) == 1
    assert render_requests[0].background_image_path is None
    assert render_requests[0].hook_text == ""
    stored_job = await test_db.scalar(
        select(MemoirMaterialShareVideo).where(MemoirMaterialShareVideo.id == job_id)
    )
    assert stored_job is not None
    assert stored_job.output is not None
    assert stored_job.output.storage_path
    project_id, user_id = stored_job.project_id, stored_job.user_id
    counts = await share_video_jobs.list_completed_share_video_counts(
        test_db,
        project_id=project_id,
        user_id=user_id,
    )
    assert counts[0].job_titles[job_id] == expected_title

    download_response = await client.get(f"/api/mobile/memoir/share-video/jobs/{job_id}/download")

    assert download_response.status_code == 200
    assert download_response.headers["content-type"].startswith("video/mp4")
    assert unquote(download_response.headers["content-disposition"]).endswith(f"{expected_title}.mp4")
    assert download_response.content == b"fake mp4"

    if suggested_title is not None:
        # Videos created before titles were stored still have a usable name.
        stored_job.metadata_json = {
            key: value for key, value in stored_job.metadata_json.items()
            if key not in {"title", "title_attempted"}
        }
        await test_db.commit()
        test_db.expire_all()
        old_job = (await client.get(f"/api/mobile/memoir/share-video/jobs/{job_id}")).json()
        assert old_job["title"] == fallback_title
        old_counts = await share_video_jobs.list_completed_share_video_counts(
            test_db, project_id=project_id, user_id=user_id,
        )
        assert old_counts[0].job_titles[job_id] == fallback_title
        old_download = await client.get(f"/api/mobile/memoir/share-video/jobs/{job_id}/download")
        assert unquote(old_download.headers["content-disposition"]).endswith(f"{fallback_title}.mp4")

    retry_response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={"start_char": 0, "end_char": end_char, "background_style": "ink_paper"},
    )
    assert retry_response.status_code == 201
    assert retry_response.json()["id"] != job_id


async def test_share_video_dedupes_running_job_after_background_progress(
    client, test_db, monkeypatch
):
    monkeypatch.setattr(
        share_video_jobs,
        "get_settings",
        lambda: SimpleNamespace(
            memoir_share_video_ai_background_enabled=True,
            volc_ark_api_key="test-key",
        ),
    )
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    end_char = text.index("。") + 1
    url = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs"
    request = {
        "start_char": 0,
        "end_char": end_char,
        "background_source": "ai",
    }
    first = await client.post(url, json=request)
    assert first.status_code == 201
    job_id = first.json()["id"]
    assert first.json()["background_status"] == "pending"
    assert await share_video_jobs.claim_share_video_job(
        test_db, job_id, lease_seconds=60, max_attempts=3
    ) is not None
    job = await test_db.get(MemoirMaterialShareVideo, job_id)
    assert job is not None
    job.metadata_json = {
        **job.metadata_json,
        "background_status": "planning",
        "background_effective_mode": "smart_multi",
    }
    await test_db.commit()

    duplicate = await client.post(url, json=request)
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == job_id
    assert duplicate.json()["status"] == "running"

    different_style = await client.post(url, json={**request, "background_style": "ink_paper"})
    assert different_style.status_code == 201
    assert different_style.json()["id"] != job_id


async def test_share_video_reuses_completed_file_but_regenerates_missing_file(
    client, test_db, tmp_path, monkeypatch
):
    monkeypatch.setattr(share_video_jobs, "MEMOIR_SHARE_VIDEO_DIR", tmp_path / "share-videos")

    async def fake_render_share_video_clip(request):
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        request.target_path.write_bytes(b"complete mp4")

    monkeypatch.setattr(share_video_rendering, "render_share_video_clip", fake_render_share_video_clip)
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    url = f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs"
    request = {
        "start_char": 0,
        "end_char": text.index("。") + 1,
        "background_source": "classic",
    }
    first = await client.post(url, json=request)
    assert first.status_code == 201
    job_id = first.json()["id"]
    assert await _claim_and_run_share_video_job(test_db, job_id) is True

    reused = await client.post(url, json=request)
    assert reused.status_code == 201
    assert reused.json()["id"] == job_id
    assert reused.json()["status"] == "succeeded"

    job = await test_db.get(MemoirMaterialShareVideo, job_id)
    assert job is not None
    share_video_jobs.share_video_file_path(job).unlink()
    regenerated = await client.post(url, json=request)
    assert regenerated.status_code == 201
    assert regenerated.json()["id"] != job_id
    assert regenerated.json()["status"] == "pending"


async def test_share_video_snapshot_marks_old_transcript_and_alignment(
    client, test_db, tmp_path, monkeypatch
):
    monkeypatch.setattr(share_video_jobs, "MEMOIR_SHARE_VIDEO_DIR", tmp_path / "share-videos")

    async def fake_render_share_video_clip(request):
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        request.target_path.write_bytes(b"complete mp4")

    monkeypatch.setattr(share_video_rendering, "render_share_video_clip", fake_render_share_video_clip)
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={
            "start_char": 0,
            "end_char": text.index("。") + 1,
            "background_source": "classic",
        },
    )
    assert response.status_code == 201
    job_id = response.json()["id"]
    assert await _claim_and_run_share_video_job(test_db, job_id) is True

    async def current_job_ids():
        snapshot = (await client.get("/api/mobile/memoir/book-snapshot")).json()
        return snapshot["share_video_counts"][0]["current_job_ids"]

    assert await current_job_ids() == [job_id]
    utterance = await test_db.get(MemoirVoiceUtterance, created["utterance_id"])
    assert utterance is not None
    original_edited_text = utterance.transcript_edited
    utterance.transcript_edited = f"{text}我还记得那棵树。"
    await test_db.commit()
    assert await current_job_ids() == []

    utterance.transcript_edited = original_edited_text
    utterance.provider_payload_json = {"revised_alignment": True}
    await test_db.commit()
    assert await current_job_ids() == []


async def test_share_video_worker_retries_and_removes_partial_output(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.memoir.share_video_jobs.MEMOIR_SHARE_VIDEO_DIR",
        tmp_path / "share-videos",
    )
    render_attempts = 0

    async def flaky_render_share_video_clip(request):
        nonlocal render_attempts
        render_attempts += 1
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        if render_attempts == 1:
            request.target_path.write_bytes(b"partial mp4")
            raise share_video_rendering.ShareVideoGenerationError("ffmpeg interrupted")
        request.target_path.write_bytes(b"final mp4")

    monkeypatch.setattr(
        "app.services.memoir.share_video_rendering.render_share_video_clip",
        flaky_render_share_video_clip,
    )
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    end_char = text.index("。") + 1
    create_response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={
            "start_char": 0,
            "end_char": end_char,
            "background_source": "classic",
        },
    )
    assert create_response.status_code == 201
    job_id = create_response.json()["id"]

    first_lease = await share_video_jobs.claim_share_video_job(
        test_db,
        job_id,
        lease_seconds=60,
        max_attempts=2,
    )
    assert first_lease is not None
    assert await share_video_jobs.run_share_video_job_with_session(
        test_db,
        first_lease,
        lease_seconds=60,
        max_attempts=2,
        retry_seconds=1,
    ) is False

    stored_job = await test_db.get(MemoirMaterialShareVideo, job_id)
    await test_db.refresh(stored_job)
    target_path = share_video_jobs.share_video_file_path(stored_job)
    assert stored_job.status == MemoirShareVideoJobStatus.PENDING.value
    assert stored_job.attempts == 1
    assert stored_job.run_after is not None
    assert stored_job.lease_token is None
    assert not target_path.exists()

    before_retry = await share_video_jobs.claim_share_video_job(
        test_db,
        job_id,
        now=stored_job.run_after - timedelta(milliseconds=1),
        lease_seconds=60,
        max_attempts=2,
    )
    second_lease = await share_video_jobs.claim_share_video_job(
        test_db,
        job_id,
        now=stored_job.run_after + timedelta(milliseconds=1),
        lease_seconds=60,
        max_attempts=2,
    )
    assert before_retry is None
    assert second_lease is not None
    assert await share_video_jobs.run_share_video_job_with_session(
        test_db,
        second_lease,
        lease_seconds=60,
        max_attempts=2,
        retry_seconds=1,
    ) is True

    await test_db.refresh(stored_job)
    assert stored_job.status == MemoirShareVideoJobStatus.SUCCEEDED.value
    assert stored_job.attempts == 2
    assert stored_job.lease_token is None
    assert target_path.read_bytes() == b"final mp4"


@pytest.mark.db_independent
async def test_share_video_worker_deadline_fails_without_retry(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.memoir.share_video_jobs.MEMOIR_SHARE_VIDEO_DIR",
        tmp_path / "share-videos",
    )
    render_started = asyncio.Event()
    render_cancelled = asyncio.Event()

    async def stalled_render(request):
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        request.target_path.write_bytes(b"partial mp4")
        render_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            render_cancelled.set()

    monkeypatch.setattr(
        "app.services.memoir.share_video_rendering.render_share_video_clip",
        stalled_render,
    )
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    end_char = text.index("。") + 1
    create_response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={
            "start_char": 0,
            "end_char": end_char,
            "background_source": "classic",
        },
    )
    job_id = create_response.json()["id"]
    lease = await share_video_jobs.claim_share_video_job(
        test_db,
        job_id,
        lease_seconds=60,
        max_attempts=3,
    )
    assert lease is not None
    session_maker = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    worker_task = asyncio.create_task(
        share_video_jobs.run_share_video_job(
            lease,
            session_maker=session_maker,
            timeout_seconds=1.0,
        )
    )
    await asyncio.wait_for(render_started.wait(), timeout=2.0)
    assert await worker_task is False
    assert render_cancelled.is_set()
    await test_db.rollback()
    stored_job = await test_db.get(MemoirMaterialShareVideo, job_id)
    await test_db.refresh(stored_job)
    target_path = share_video_jobs.share_video_file_path(stored_job)
    assert stored_job.status == MemoirShareVideoJobStatus.FAILED.value
    assert stored_job.attempts == 1
    assert stored_job.run_after is None
    assert stored_job.metadata_json["error_type"] == "job_deadline_exceeded"
    assert stored_job.metadata_json["timeout_seconds"] == 1.0
    assert "已停止，请重新生成" in stored_job.error_message
    assert not target_path.exists()


@pytest.mark.db_independent
async def test_share_video_worker_recovers_when_completion_write_stalls(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.memoir.share_video_jobs.MEMOIR_SHARE_VIDEO_DIR",
        tmp_path / "share-videos",
    )
    rendered = asyncio.Event()

    async def fake_render_share_video_clip(request):
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        request.target_path.write_bytes(b"complete mp4")
        rendered.set()

    monkeypatch.setattr(
        "app.services.memoir.share_video_rendering.render_share_video_clip",
        fake_render_share_video_clip,
    )
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    end_char = text.index("。") + 1
    create_response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={
            "start_char": 0,
            "end_char": end_char,
            "background_source": "classic",
        },
    )
    assert create_response.status_code == 201
    job_id = create_response.json()["id"]
    lease = await share_video_jobs.claim_share_video_job(
        test_db,
        job_id,
        lease_seconds=60,
        max_attempts=2,
    )
    assert lease is not None

    original_complete = share_video_jobs._complete_share_video_job
    completion_calls = 0
    stalled_completion_cancelled = asyncio.Event()

    async def stall_first_completion(*args, **kwargs):
        nonlocal completion_calls
        completion_calls += 1
        if completion_calls == 1:
            try:
                await asyncio.Event().wait()
            finally:
                stalled_completion_cancelled.set()
        assert stalled_completion_cancelled.is_set()
        return await original_complete(*args, **kwargs)

    monkeypatch.setattr(share_video_jobs, "_complete_share_video_job", stall_first_completion)
    session_maker = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    result = await asyncio.wait_for(
        share_video_jobs.run_share_video_job(
            lease,
            session_maker=session_maker,
            completion_recovery_poll_seconds=10.0,
            timeout_seconds=1.0,
        ),
        timeout=3,
    )

    assert result is True
    assert rendered.is_set()
    assert completion_calls == 2
    assert stalled_completion_cancelled.is_set()
    stored_job = await test_db.get(MemoirMaterialShareVideo, job_id)
    await test_db.refresh(stored_job)
    assert stored_job.status == MemoirShareVideoJobStatus.SUCCEEDED.value
    assert stored_job.output is not None
    assert stored_job.output.size_bytes == len(b"complete mp4")
    target_path = share_video_jobs.share_video_file_path(stored_job, stored_job.output)
    assert target_path.read_bytes() == b"complete mp4"
    assert not share_video_jobs._completion_marker_path(target_path).exists()


async def test_share_video_ffmpeg_process_is_terminated_when_worker_is_cancelled(monkeypatch):
    communicate_started = asyncio.Event()

    class FakeProcess:
        returncode = None
        terminated = False
        killed = False

        async def communicate(self):
            communicate_started.set()
            await asyncio.Event().wait()

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = FakeProcess()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(
        share_video_rendering.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    task = asyncio.create_task(share_video_rendering._run_ffmpeg(["ffmpeg", "-version"]))
    await communicate_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.terminated is True
    assert process.killed is False


async def test_mobile_memoir_share_video_job_uses_custom_background_asset(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(share_video_custom_backgrounds, "DATA_DIR", tmp_path)
    monkeypatch.setattr(image_assets, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "app.services.memoir.share_video_jobs.MEMOIR_SHARE_VIDEO_DIR",
        tmp_path / "share-videos",
    )

    async def fake_process_background_image(source_path: Path, target_path: Path) -> tuple[int, int]:
        assert source_path.read_bytes() == PNG_1X1
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"processed background jpg")
        return 1080, 1440

    async def fail_ai_background_generation(_selected_text, _style_id):
        raise AssertionError("custom background should not call AI background generation")

    render_requests: list[share_video_rendering.ShareVideoRenderRequest] = []

    async def fake_render_share_video_clip(request):
        render_requests.append(request)
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        request.target_path.write_bytes(b"fake custom mp4")

    monkeypatch.setattr(
        share_video_custom_backgrounds,
        "_process_background_image",
        fake_process_background_image,
    )
    monkeypatch.setattr(
        "app.services.memoir.share_video_background_orchestration.get_or_generate_share_video_background",
        fail_ai_background_generation,
    )
    monkeypatch.setattr(
        "app.services.memoir.share_video_rendering.render_share_video_clip",
        fake_render_share_video_clip,
    )
    project_id = await ensure_mobile_memoir_project(client)
    upload_response = await client.post(
        "/api/mobile/memoir/share-video/background-assets",
        data={"project_id": project_id},
        files={"file": ("background.png", PNG_1X1, "image/png")},
    )
    assert upload_response.status_code == 201
    custom_asset_id = upload_response.json()["id"]
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, project_id=project_id, text=text)
    end_char = text.index("。") + 1

    create_response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={
            "start_char": 0,
            "end_char": end_char,
            "background_source": "custom",
            "background_mode": "smart_multi",
            "custom_background_asset_id": custom_asset_id,
        },
    )

    assert create_response.status_code == 201
    created_job = create_response.json()
    assert created_job["background_source"] == "custom"
    assert created_job["background_effective_mode"] == "single"
    assert created_job["custom_background_asset_id"] == custom_asset_id
    assert created_job["background_status"] == "ready"
    job_id = created_job["id"]

    assert await _claim_and_run_share_video_job(test_db, job_id) is True
    detail_response = await client.get(f"/api/mobile/memoir/share-video/jobs/{job_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()

    assert detail["status"] == "succeeded"
    assert detail["background_source"] == "custom"
    assert detail["background_effective_mode"] == "single"
    assert detail["background_scene_count"] == 1
    assert detail["background_status"] == "ready"
    assert detail["background_provider"] == "user_upload"
    assert detail["background_model"] == "ffmpeg-background-processor"
    assert detail["output"]["size_bytes"] == len(b"fake custom mp4")
    assert len(render_requests) == 1
    render_request = render_requests[0]
    assert render_request.background_segments == []
    assert render_request.background_image_path is not None
    assert render_request.background_image_path.read_bytes() == b"processed background jpg"

    stored_job = await test_db.scalar(
        select(MemoirMaterialShareVideo).where(MemoirMaterialShareVideo.id == job_id)
    )
    assert stored_job is not None
    assert stored_job.metadata_json["background_storage_path"].endswith("/processed.jpg")


async def test_mobile_memoir_share_video_job_rejects_missing_custom_background_asset(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, project_id=project_id, text=text)
    end_char = text.index("。") + 1

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={
            "start_char": 0,
            "end_char": end_char,
            "background_source": "custom",
            "custom_background_asset_id": "missing-background",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "自定义背景图不存在"


async def test_mobile_memoir_share_video_job_uses_classic_background(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.memoir.share_video_jobs.MEMOIR_SHARE_VIDEO_DIR",
        tmp_path / "share-videos",
    )

    async def fail_ai_background_generation(_selected_text, _style_id):
        raise AssertionError("classic background should not call AI background generation")

    render_requests: list[share_video_rendering.ShareVideoRenderRequest] = []

    async def fake_render_share_video_clip(request):
        render_requests.append(request)
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        request.target_path.write_bytes(b"fake classic mp4")

    monkeypatch.setattr(
        "app.services.memoir.share_video_background_orchestration.get_or_generate_share_video_background",
        fail_ai_background_generation,
    )
    monkeypatch.setattr(
        "app.services.memoir.share_video_rendering.render_share_video_clip",
        fake_render_share_video_clip,
    )
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    end_char = text.index("。") + 1

    create_response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={
            "start_char": 0,
            "end_char": end_char,
            "background_source": "classic",
            "background_mode": "smart_multi",
        },
    )

    assert create_response.status_code == 201
    created_job = create_response.json()
    assert "style" not in created_job
    assert created_job["background_source"] == "classic"
    assert created_job["background_effective_mode"] == "single"
    assert created_job["background_status"] == "classic"
    job_id = created_job["id"]

    assert await _claim_and_run_share_video_job(test_db, job_id) is True
    detail_response = await client.get(f"/api/mobile/memoir/share-video/jobs/{job_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()

    assert detail["status"] == "succeeded"
    assert detail["background_source"] == "classic"
    assert detail["background_effective_mode"] == "single"
    assert detail["background_status"] == "classic"
    assert detail["background_scene_count"] == 0
    assert len(render_requests) == 1
    assert render_requests[0].background_image_path is None
    assert render_requests[0].background_segments == []


async def test_mobile_memoir_share_video_job_uses_smart_multi_background_segments(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.memoir.share_video_jobs.MEMOIR_SHARE_VIDEO_DIR",
        tmp_path / "share-videos",
    )
    monkeypatch.setattr(
        "app.services.memoir.share_video_jobs.get_settings",
        lambda: SimpleNamespace(
            memoir_share_video_ai_background_enabled=True,
            volc_ark_api_key="test-key",
        ),
    )

    text = "小时候一家人住在老房子里。后来去县城读书。再后来大家各自成家。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    project = await test_db.get(Project, created["session"]["project_id"])
    project.metadata_json = {
        "ai_model": DEFAULT_AI_TEXT_MODEL,
        "memoir_profile": {"narrator_gender": "female", "narrator_age": 82},
    }
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == created["audio_file_id"]))
    assert utterance is not None
    assert audio_file is not None
    utterance.end_ms = 36_000
    audio_file.duration_seconds = 36
    await test_db.commit()

    first_end = text.index("。") + 1
    second_end = text.index("。", first_end) + 1

    narrator = "讲述者“我”是女性；现在约 82 岁，回忆里的年纪按故事发生的时间推算。"

    note = "“我们”是我和妹妹，那年我十岁。"

    async def fake_plan(
        selected_text, char_timings, duration_ms, *, ai_model=None, narrator_context="", background_note=""
    ):
        assert (narrator_context, background_note) == (narrator, note)
        assert selected_text == text
        assert duration_ms == 36_000
        assert len(char_timings) == len(text)
        assert ai_model == DEFAULT_AI_TEXT_MODEL
        return share_video_scene_planner.ShareVideoScenePlan(
            provider="fake_llm",
            model="fake-scene-planner",
            strategy="llm",
            plan_hash="plan-hash",
            visual_context="父亲四十来岁，老县城",
            scenes=[
                share_video_scene_planner.ShareVideoScene(
                    index=0,
                    start_char=0,
                    end_char=first_end,
                    start_ms=0,
                    end_ms=12_000,
                    title="老房子",
                ),
                share_video_scene_planner.ShareVideoScene(
                    index=1,
                    start_char=first_end,
                    end_char=second_end,
                    start_ms=12_000,
                    end_ms=24_000,
                    title="县城读书",
                ),
                share_video_scene_planner.ShareVideoScene(
                    index=2,
                    start_char=second_end,
                    end_char=len(text),
                    start_ms=24_000,
                    end_ms=36_000,
                    title="各自成家",
                ),
            ],
        )

    image_calls: list[tuple[str, str]] = []
    image_dir = tmp_path / "backgrounds"
    image_dir.mkdir()

    image_contexts: list[tuple[str, str]] = []
    set_calls: list[list[str]] = []

    async def fake_background_set(
        scene_texts,
        style_id,
        *,
        story_text,
        visual_context="",
        narrator_context="",
        background_note="",
        usage_context=None,
    ):
        # The group returns only the first image; the rest are drawn one by one.
        set_calls.append(list(scene_texts))
        assert (story_text, visual_context, narrator_context) == (text, "父亲四十来岁，老县城", narrator)
        first = await fake_background(
            scene_texts[0],
            style_id,
            story_text=story_text,
            visual_context=visual_context,
            narrator_context=narrator_context,
            background_note=background_note,
        )
        return [first] + [None] * (len(scene_texts) - 1)

    async def fake_background(
        selected_text,
        style_id,
        *,
        story_text="",
        visual_context="",
        narrator_context="",
        background_note="",
        usage_context=None,
    ):
        assert (narrator_context, background_note) == (narrator, note)
        image_calls.append((selected_text, style_id))
        image_contexts.append((story_text, visual_context))
        image_path = image_dir / f"scene-{len(image_calls)}.png"
        image_path.write_bytes(b"fake image")
        return share_video_backgrounds.ShareVideoBackgroundAsset(
            path=image_path,
            storage_path=f"memoir/share-video-backgrounds/scene-{len(image_calls)}.png",
            style_id=style_id,
            prompt_hash=f"prompt-{len(image_calls)}",
            prompt_template_version="test",
            provider="volc_ark",
            model="doubao-seedream-5-0-lite-260128",
            cache_hit=False,
        )

    render_requests: list[share_video_rendering.ShareVideoRenderRequest] = []

    async def fake_render_share_video_clip(request):
        render_requests.append(request)
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        request.target_path.write_bytes(b"fake multi mp4")

    monkeypatch.setattr(
        "app.services.memoir.share_video_background_orchestration.plan_share_video_background_scenes",
        fake_plan,
    )
    monkeypatch.setattr(
        "app.services.memoir.share_video_background_orchestration.get_or_generate_share_video_background",
        fake_background,
    )
    monkeypatch.setattr(
        "app.services.memoir.share_video_background_orchestration.get_or_generate_share_video_background_set",
        fake_background_set,
    )
    monkeypatch.setattr(
        "app.services.memoir.share_video_rendering.render_share_video_clip",
        fake_render_share_video_clip,
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={
            "start_char": 0,
            "end_char": len(text),
            "background_style": "old_album",
            "background_mode": "smart_multi",
            "background_note": f"  {note} ",
        },
    )

    assert response.status_code == 201
    job_id = response.json()["id"]
    assert response.json()["background_mode"] == "smart_multi"
    assert response.json()["background_note"] == note
    assert response.json()["background_effective_mode"] == "smart_multi"

    assert await _claim_and_run_share_video_job(test_db, job_id) is True
    detail_response = await client.get(f"/api/mobile/memoir/share-video/jobs/{job_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()

    assert detail["status"] == "succeeded"
    assert detail["background_mode"] == "smart_multi"
    assert detail["background_effective_mode"] == "smart_multi"
    assert detail["background_scene_count"] == 3
    assert detail["background_status"] == "ready"
    assert detail["background_provider"] == "volc_ark"
    assert detail["background_model"] == "doubao-seedream-5-0-lite-260128"
    assert [style for _prompt, style in image_calls] == ["old_album", "old_album", "old_album"]
    assert [prompt for prompt, _style in image_calls] == [
        "小时候一家人住在老房子里。",
        "后来去县城读书。",
        "再后来大家各自成家。",
    ]
    assert image_contexts == [(text, "父亲四十来岁，老县城")] * 3
    assert set_calls == [["小时候一家人住在老房子里。", "后来去县城读书。", "再后来大家各自成家。"]]
    assert len(render_requests) == 1
    render_request = render_requests[0]
    assert render_request.background_image_path is None
    assert render_request.background_segments is not None
    assert len(render_request.background_segments) == 3
    assert [(segment.start_ms, segment.end_ms) for segment in render_request.background_segments] == [
        (0, 12_000),
        (12_000, 24_000),
        (24_000, 36_000),
    ]

    stored_job = await test_db.scalar(
        select(MemoirMaterialShareVideo).where(MemoirMaterialShareVideo.id == job_id)
    )
    assert stored_job is not None
    assert stored_job.metadata_json["background_scene_plan"]["provider"] == "fake_llm"
    assert stored_job.metadata_json["background_generated_count"] == 3


async def test_share_video_worker_reclaims_expired_lease_without_get_side_effects(client, test_db):
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    end_char = text.index("。") + 1

    create_response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={"start_char": 0, "end_char": end_char},
    )
    assert create_response.status_code == 201
    job_id = create_response.json()["id"]
    claimed_at = utcnow_naive() + timedelta(seconds=1)
    first_lease = await share_video_jobs.claim_share_video_job(
        test_db,
        job_id,
        now=claimed_at,
        lease_seconds=30,
        max_attempts=3,
    )
    assert first_lease is not None

    stored_job = await test_db.scalar(
        select(MemoirMaterialShareVideo).where(MemoirMaterialShareVideo.id == job_id)
    )
    assert stored_job is not None
    original_lease_token = stored_job.lease_token
    detail_response = await client.get(f"/api/mobile/memoir/share-video/jobs/{job_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["status"] == "running"
    await test_db.refresh(stored_job)
    assert stored_job.lease_token == original_lease_token

    before_expiry = await share_video_jobs.claim_share_video_job(
        test_db,
        job_id,
        now=claimed_at + timedelta(seconds=29),
        lease_seconds=30,
        max_attempts=3,
    )
    reclaimed = await share_video_jobs.claim_share_video_job(
        test_db,
        job_id,
        now=claimed_at + timedelta(seconds=31),
        lease_seconds=30,
        max_attempts=3,
    )

    assert before_expiry is None
    assert reclaimed is not None
    assert reclaimed.job_id == job_id
    assert reclaimed.token != first_lease.token
    await test_db.refresh(stored_job)
    assert stored_job.attempts == 2
    assert stored_job.lease_token == reclaimed.token


@pytest.mark.db_independent
async def test_postgres_share_video_claim_skips_locked_queue_head(client, test_db):
    if test_db.get_bind().dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL row-level locking")
    text = "我小时候住在外婆家。后来我去县城读书。"
    created = await create_test_voice_transcript(client, test_db, text=text)
    first_end = text.index("。") + 1
    first_response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={"start_char": 0, "end_char": first_end},
    )
    second_response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/share-video/jobs",
        json={"start_char": first_end, "end_char": len(text)},
    )
    assert first_response.status_code == 201
    assert second_response.status_code == 201
    first_id = first_response.json()["id"]
    second_id = second_response.json()["id"]
    session_maker = async_sessionmaker(
        test_db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_maker() as locking_db:
        await locking_db.execute(
            select(MemoirMaterialShareVideo)
            .where(MemoirMaterialShareVideo.id == first_id)
            .with_for_update(of=MemoirMaterialShareVideo)
        )
        async with session_maker() as claiming_db:
            leases = await asyncio.wait_for(
                share_video_jobs.claim_due_share_video_jobs(
                    claiming_db,
                    now=utcnow_naive() + timedelta(seconds=1),
                    limit=1,
                    lease_seconds=30,
                    max_attempts=3,
                ),
                timeout=2,
            )
        await locking_db.rollback()

    assert [lease.job_id for lease in leases] == [second_id]


async def test_mobile_memoir_lists_unfinalized_audio_for_transcription_recovery(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    user = (await client.get("/api/auth/me")).json()
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration", "provider": "volc_streaming_asr"},
    )
    assert create_response.status_code == 201
    session = create_response.json()["session"]
    persistence = VoiceGatewayPersistence(
        db=test_db,
        voice_session_id=session["id"],
        user_id=user["id"],
    )

    await persistence.record_audio_frame(b"lost-audio-pcm")
    persisted = await persistence.flush_unfinalized_audio()
    assert persisted is not None

    response = await client.get("/api/mobile/memoir/voice-transcripts")
    detail_response = await client.get(f"/api/mobile/memoir/voice-transcripts/{persisted['utterance_id']}")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["id"] == persisted["utterance_id"]
    assert item["audio_file_id"] == persisted["audio_file_id"]
    assert item["audio_size_bytes"] == len(b"lost-audio-pcm")
    assert item["transcript_raw"] == ""
    assert item["display_text"] == ""
    assert item["display_char_count"] == 0
    assert item["summary_title"] == "音频已保存，等待转写"
    assert item["summary_text"] == "这段录音已经保存，但还没有识别成文字。"
    assert item["summary_source"] == "pending_transcription"
    assert item["transcription_status"] == "pending_transcription"
    assert item["can_recover_transcription"] is True

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == persisted["utterance_id"]
    assert detail["transcription_status"] == "pending_transcription"
    assert detail["can_recover_transcription"] is True


@pytest.mark.parametrize("speech_detected,expected,visible", [(False, "empty", False), (True, "unrecognized", True), (None, "unrecognized", True)])
async def test_normal_empty_turn_has_terminal_outcome_and_cannot_replace_home_story(client, test_db, tmp_path, monkeypatch, speech_detected, expected, visible):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    story = await create_test_voice_transcript(client, test_db, "小时候母亲背我上山。")
    user = (await client.get("/api/auth/me")).json()
    persistence = VoiceGatewayPersistence(db=test_db, voice_session_id=story["session"]["id"], user_id=user["id"])
    await persistence.record_audio_frame(b"\0" * 32000)
    await persistence.finish_audio(speech_detected)
    assert persistence.pending_audio_size_bytes == 0
    utterance = await test_db.scalar(select(MemoirVoiceUtterance).where(
        MemoirVoiceUtterance.voice_session_id == story["session"]["id"], MemoirVoiceUtterance.transcript_raw == "",
    ))
    assert utterance.audio_file_id  # Preserve the original audio even for a silent turn.
    detail = (await client.get(f"/api/mobile/memoir/voice-transcripts/{utterance.id}")).json()
    assert detail["transcription_status"] == expected
    assert detail["can_recover_transcription"] == visible
    listing = (await client.get("/api/mobile/memoir/voice-transcripts")).json()
    assert (utterance.id in {item["id"] for item in listing["items"]}) == visible
    ready = (await client.get("/api/mobile/memoir/voice-transcripts?ready_only=true")).json()
    assert [item["id"] for item in ready["items"]] == [story["utterance_id"]]
    for endpoint in ("home-snapshot", "story-snapshot"):
        snapshot = (await client.get(f"/api/mobile/memoir/{endpoint}")).json()
        assert snapshot["latest_transcript"]["id"] == story["utterance_id"]


@pytest.mark.parametrize("provider_failure", [False, True])
async def test_recovery_empty_result_is_resolved_but_provider_failure_remains_recoverable(client, test_db, tmp_path, monkeypatch, provider_failure):
    from fastapi import HTTPException
    from app.services.memoir.voice_transcript_recovery import ASRNoTextError
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr("app.services.memoir.voice_transcript_recovery.assert_ai_credit_available", AsyncMock())
    monkeypatch.setattr("app.services.memoir.voice_transcript_recovery.record_volc_streaming_asr_usage", AsyncMock())
    monkeypatch.setattr("app.services.memoir.voice_transcript_recovery.transcribe_audio_path_for_recovery", AsyncMock(
        side_effect=HTTPException(status_code=504, detail="timeout") if provider_failure else ASRNoTextError(),
    ))
    user = (await client.get("/api/auth/me")).json()
    session = (await post_mobile_memoir_voice_session(client, json={"guidance_mode": "free_narration"})).json()["session"]
    persistence = VoiceGatewayPersistence(db=test_db, voice_session_id=session["id"], user_id=user["id"])
    await persistence.record_audio_frame(b"\0" * 32000)
    saved = await persistence.flush_unfinalized_audio()
    result = await client.post(f"/api/mobile/memoir/voice-transcripts/{saved['utterance_id']}/recover-transcription", json={})
    assert result.status_code == (504 if provider_failure else 200)
    current = (await client.get(f"/api/mobile/memoir/voice-transcripts/{saved['utterance_id']}")).json()
    assert current["transcription_status"] == ("pending_transcription" if provider_failure else "unrecognized")
    assert current["audio_file_id"] == saved["audio_file_id"] and current["can_recover_transcription"]


async def test_mobile_memoir_recovers_unfinalized_audio_transcription(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(
        "app.services.memoir.voice_transcript_recovery.assert_ai_credit_available",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.memoir.voice_transcript_recovery.record_volc_streaming_asr_usage",
        AsyncMock(),
    )
    user = (await client.get("/api/auth/me")).json()
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration", "provider": "volc_streaming_asr"},
    )
    assert create_response.status_code == 201
    session = create_response.json()["session"]
    persistence = VoiceGatewayPersistence(
        db=test_db,
        voice_session_id=session["id"],
        user_id=user["id"],
    )
    await persistence.record_audio_frame(b"lost-audio-pcm")
    persisted = await persistence.flush_unfinalized_audio()
    assert persisted is not None

    asr_client = FakeRecoveryASRClient([
        StreamingASREvent(
            type="final",
            text="爷爷退休后还在帮公司做事。",
            start_ms=0,
            end_ms=8000,
            raw={"provider_event_type": "final"},
        )
    ])
    monkeypatch.setattr(
        "app.routers.memoir.voice.create_volc_streaming_asr_client",
        lambda _user_id, _voice_session_id: asr_client,
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{persisted['utterance_id']}/recover-transcription",
        json={"transcript_polish_level": "medium"},
    )

    assert response.status_code == 200
    item = response.json()
    assert item["id"] == persisted["utterance_id"]
    assert item["chat_message_id"]
    assert item["transcript_version_id"]
    assert item["transcript_raw"] == "爷爷退休后还在帮公司做事。"
    assert item["display_text"] == "爷爷退休后还在帮公司做事。"
    assert item["transcription_status"] == "ready"
    assert item["can_recover_transcription"] is False
    assert asr_client.started
    assert asr_client.sent_audio == [b"lost-audio-pcm"]
    assert asr_client.finished
    assert asr_client.closed

    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == persisted["utterance_id"])
    )
    assert utterance is not None
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == utterance.chat_message_id))
    version = await test_db.scalar(
        select(MemoirTranscriptVersion).where(MemoirTranscriptVersion.id == message.transcript_version_id)
    )
    correction_job = await test_db.scalar(
        select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == f"correct_transcript:{utterance.id}")
    )
    assert message is not None
    assert message.role == MemoirChatMessageRole.USER.value
    assert message.content_text == "爷爷退休后还在帮公司做事。"
    assert message.audio_file_id == persisted["audio_file_id"]
    assert message.metadata_json["source"] == "transcript_recovery"
    assert version is not None
    assert version.content == "爷爷退休后还在帮公司做事。"
    assert version.version_type == MemoirTranscriptVersionType.ASR_RAW.value
    assert version.source_artifact_id == utterance.id
    assert correction_job is not None
    assert correction_job.payload_json["raw_transcript_version_id"] == version.id
    assert correction_job.payload_json["transcript_polish_level"] == "medium"


async def test_mobile_memoir_recovers_long_unfinalized_audio_in_segments(
    client,
    test_db,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(
        "app.services.memoir.voice_transcript_recovery.assert_ai_credit_available",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.memoir.voice_transcript_recovery.record_volc_streaming_asr_usage",
        AsyncMock(),
    )
    monkeypatch.setattr("app.services.memoir.voice_transcript_recovery.ASR_RECOVERY_PCM_SEGMENT_BYTES", 32000)
    user = (await client.get("/api/auth/me")).json()
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "free_narration", "provider": "volc_streaming_asr"},
    )
    assert create_response.status_code == 201
    session = create_response.json()["session"]
    persistence = VoiceGatewayPersistence(
        db=test_db,
        voice_session_id=session["id"],
        user_id=user["id"],
    )
    segment_audio = [b"a" * 32000, b"b" * 32000, b"c" * 16000]
    for audio in segment_audio:
        await persistence.record_audio_frame(audio)
    persisted = await persistence.flush_unfinalized_audio()
    assert persisted is not None

    asr_clients = [
        FakeRecoveryASRClient([
            StreamingASREvent(type="final", text="第一段。", start_ms=0, end_ms=800, raw={"segment": 1})
        ]),
        FakeRecoveryASRClient([
            StreamingASREvent(type="final", text="第二段。", start_ms=0, end_ms=900, raw={"segment": 2})
        ]),
        FakeRecoveryASRClient([
            StreamingASREvent(type="final", text="第三段。", start_ms=0, end_ms=500, raw={"segment": 3})
        ]),
    ]
    remaining_clients = list(asr_clients)
    monkeypatch.setattr(
        "app.routers.memoir.voice.create_volc_streaming_asr_client",
        lambda _user_id, _voice_session_id: remaining_clients.pop(0),
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{persisted['utterance_id']}/recover-transcription"
    )

    assert response.status_code == 200
    item = response.json()
    assert item["transcript_raw"] == "第一段。\n第二段。\n第三段。"
    assert item["display_text"] == "第一段。\n第二段。\n第三段。"
    assert item["transcription_status"] == "ready"
    assert item["can_recover_transcription"] is False
    assert [b"".join(client.sent_audio) for client in asr_clients] == segment_audio
    assert all(client.closed for client in asr_clients)

    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == persisted["utterance_id"])
    )
    assert utterance is not None
    payload = utterance.provider_payload_json["provider_payload"]
    assert payload["source"] == "segmented_transcript_recovery"
    assert payload["segment_count"] == 3
    assert [segment["provider_payload"]["segment"] for segment in payload["segments"]] == [1, 2, 3]
    assert utterance.start_ms == 0
    assert utterance.end_ms == 2500


async def test_mobile_memoir_groups_paired_human_interview_transcripts(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={
            "guidance_mode": "free_narration",
            "interview_type": "human_interview",
            "provider": "volc_streaming_asr",
        },
    )
    assert create_response.status_code == 201
    session = create_response.json()["session"]
    created = await _append_human_interview_turn(
        test_db,
        user_id=user["id"],
        session=session,
        interviewer_text="你小时候家里有几口人？",
        narrator_text="我们家那时候有六口人。",
    )

    response = await client.get("/api/mobile/memoir/voice-transcripts")
    question_search_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "几口人"})

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == created["narrator_utterance_id"]
    assert data["items"][0]["chat_message_role"] == "user"
    assert data["items"][0]["question_message_id"] == created["interviewer_message_id"]
    assert data["items"][0]["question_text"] == "你小时候家里有几口人？"
    assert data["items"][0]["display_text"] == "我们家那时候有六口人。"
    assert question_search_response.status_code == 200
    assert question_search_response.json()["total"] == 1
    assert question_search_response.json()["items"][0]["id"] == created["narrator_utterance_id"]


async def test_mobile_memoir_keeps_unpaired_human_interviewer_transcript(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={
            "guidance_mode": "free_narration",
            "interview_type": "human_interview",
            "provider": "volc_streaming_asr",
        },
    )
    assert create_response.status_code == 201
    session = create_response.json()["session"]
    created = await _append_human_interview_turn(
        test_db,
        user_id=user["id"],
        session=session,
        interviewer_text="你后来是什么时候去县城读书的？",
    )

    response = await client.get("/api/mobile/memoir/voice-transcripts")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == created["interviewer_utterance_id"]
    assert data["items"][0]["chat_message_role"] == "interviewer"
    assert data["items"][0]["question_message_id"] is None
    assert data["items"][0]["question_text"] == ""
    assert data["items"][0]["display_text"] == "你后来是什么时候去县城读书的？"


async def test_mobile_memoir_searches_explicit_parent_question_for_human_pair(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={
            "guidance_mode": "free_narration",
            "interview_type": "human_interview",
            "provider": "volc_streaming_asr",
        },
    )
    assert create_response.status_code == 201
    session = create_response.json()["session"]
    created = await _append_human_interview_turn(
        test_db,
        user_id=user["id"],
        session=session,
        interviewer_text="你小时候家里有几口人？",
        later_interviewer_text="你后来是什么时候去县城读书的？",
        narrator_text="我们家那时候有六口人。",
    )

    parent_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "几口人"})
    later_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "县城读书"})

    assert parent_response.status_code == 200
    assert parent_response.json()["total"] == 1
    assert parent_response.json()["items"][0]["id"] == created["narrator_utterance_id"]
    assert parent_response.json()["items"][0]["question_message_id"] == created["interviewer_message_id"]
    assert later_response.status_code == 200
    assert later_response.json()["total"] == 1
    assert later_response.json()["items"][0]["id"] == created["later_interviewer_utterance_id"]
    assert later_response.json()["items"][0]["question_text"] == ""


async def test_mobile_memoir_searches_voice_transcripts_by_text_and_question(client, test_db):
    first = await create_test_voice_transcript(
        client,
        test_db,
        text="我小时候常在外婆家的院子里晒太阳。",
        question_text="你小时候住在哪里？",
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        text="后来我第一次离家去县城读书。",
        question_text="你在学校最想念谁？",
    )

    text_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "外婆"})
    fts_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "外婆家"})
    question_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "学校"})
    multi_term_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "县城 读书"})
    empty_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "纺织厂"})

    assert text_response.status_code == 200
    assert text_response.json()["total"] == 1
    assert text_response.json()["items"][0]["id"] == first["utterance_id"]

    assert fts_response.status_code == 200
    assert fts_response.json()["total"] == 1
    assert fts_response.json()["items"][0]["id"] == first["utterance_id"]

    assert question_response.status_code == 200
    assert question_response.json()["total"] == 1
    assert question_response.json()["items"][0]["id"] == second["utterance_id"]

    assert multi_term_response.status_code == 200
    assert multi_term_response.json()["total"] == 1
    assert multi_term_response.json()["items"][0]["id"] == second["utterance_id"]

    assert empty_response.status_code == 200
    assert empty_response.json()["total"] == 0
    assert empty_response.json()["items"] == []


async def test_mobile_memoir_searches_only_the_displayed_question_for_each_transcript(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    create_response = await post_mobile_memoir_voice_session(
        client,
        json={"guidance_mode": "guided_interview", "provider": "volc_streaming_asr"},
    )
    session = create_response.json()["session"]
    first = await _append_voice_turn(
        test_db,
        user_id=user["id"],
        session=session,
        question_text="你小时候最喜欢的老师是谁？",
        text="那时候我经常在老家院子里玩。",
        question_turn_index=0,
        user_turn_index=1,
    )
    second = await _append_voice_turn(
        test_db,
        user_id=user["id"],
        session=session,
        question_text="后来去县城读书时住在哪里？",
        text="我住在学校旁边的一间小屋里。",
        question_turn_index=2,
        user_turn_index=3,
    )

    teacher_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "老师"})
    county_response = await client.get("/api/mobile/memoir/voice-transcripts", params={"q": "县城"})

    assert teacher_response.status_code == 200
    assert teacher_response.json()["total"] == 1
    assert teacher_response.json()["items"][0]["id"] == first["utterance_id"]

    assert county_response.status_code == 200
    assert county_response.json()["total"] == 1
    assert county_response.json()["items"][0]["id"] == second["utterance_id"]


async def test_mobile_memoir_filters_voice_transcripts_by_mode_and_module(client, test_db):
    await create_test_voice_transcript(
        client,
        test_db,
        text="我想自由讲讲小时候的村子。",
        guidance_mode="free_narration",
    )
    guided = await create_test_voice_transcript(
        client,
        test_db,
        text="母亲每天很早就去田里干活。",
        guidance_mode="guided_interview",
        current_guidance_module="person",
    )

    response = await client.get(
        "/api/mobile/memoir/voice-transcripts",
        params={"guidance_mode": "guided_interview", "module": "person"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == guided["utterance_id"]
    assert data["items"][0]["guidance_mode"] == "guided_interview"
    assert data["items"][0]["current_guidance_module"] == "person"


async def test_mobile_memoir_audio_playback_wraps_pcm_as_wav(client, test_db, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    created = await create_test_voice_transcript(client, test_db)
    pcm = b"\x01\x00\x02\x00"
    audio_path = tmp_path / "audio" / created["audio_relative_path"]
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(pcm)

    response = await client.get(f"/api/mobile/memoir/audio-files/{created['audio_file_id']}/playback")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content.startswith(b"RIFF")
    assert response.content[8:12] == b"WAVE"
    assert response.content[-len(pcm):] == pcm


async def test_mobile_memoir_updates_voice_transcript_without_overwriting_raw(client, test_db):
    created = await create_test_voice_transcript(client, test_db)

    response = await client.patch(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
        json={"transcript_edited": "我小时候住在外婆家。"},
    )

    assert response.status_code == 200
    item = response.json()
    assert item["transcript_raw"] == "嗯，我小时候就是住在外婆家。"
    assert item["transcript_edited"] == "我小时候住在外婆家。"
    assert item["display_text"] == "我小时候住在外婆家。"

    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion)
            .where(MemoirTranscriptVersion.voice_session_id == created["session"]["id"])
            .order_by(MemoirTranscriptVersion.created_at)
        )
    ).scalars().all()

    assert utterance.transcript_raw == "嗯，我小时候就是住在外婆家。"
    assert utterance.transcript_edited == "我小时候住在外婆家。"
    assert message.content_text == "我小时候住在外婆家。"
    assert message.content_status == "corrected"
    assert len(versions) == 2
    assert versions[-1].version_type == "user_edited"
    assert versions[-1].created_by == "user"
    assert message.transcript_version_id == versions[-1].id

    organization_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.dedupe_key == f"organize_material:{created['utterance_id']}"
        )
    )
    assert organization_job is None


async def test_mobile_memoir_editing_included_transcript_marks_chapter_for_revision(client, test_db):
    created = await create_test_voice_transcript(client, test_db)
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(created),
    )
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add(draft)
    await test_db.flush()
    draft_version = MemoirBookChapterDraftVersion(
        draft_id=draft.id,
        project_id=project_id,
        chapter_id=chapter.id,
        version=1,
        content="我小时候住在外婆家。",
        created_by=MemoirCreatedBy.AI.value,
        used_material_ids_json=[created["utterance_id"]],
    )
    test_db.add(draft_version)
    await test_db.commit()

    response = await client.patch(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
        json={"transcript_edited": "我小时候住在外婆家的院子里。"},
    )

    assert response.status_code == 200
    version = await test_db.scalar(
        select(MemoirTranscriptVersion)
        .where(
            MemoirTranscriptVersion.source_artifact_id == created["utterance_id"],
            MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
        )
        .order_by(MemoirTranscriptVersion.created_at.desc(), MemoirTranscriptVersion.id.desc())
    )
    await test_db.refresh(draft)
    material = await test_db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.project_id == project_id,
            MemoirSectionMaterial.utterance_id == created["utterance_id"],
        )
    )
    assert material is not None
    assert material.transcript_version_id == version.id
    assert draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert draft.metadata_json["status_reason"] == "materials_changed"


async def test_mobile_memoir_deletes_voice_transcript_and_user_message(client, test_db):
    created = await create_test_voice_transcript(client, test_db)

    response = await client.delete(f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}")

    assert response.status_code == 204

    list_response = await client.get("/api/mobile/memoir/voice-transcripts")
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 0

    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    audio_file = await test_db.scalar(select(AudioFile).where(AudioFile.id == created["audio_file_id"]))
    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion).where(
                MemoirTranscriptVersion.voice_session_id == created["session"]["id"]
            )
        )
    ).scalars().all()

    assert utterance is None
    assert message is None
    assert audio_file is not None
    assert len(versions) == 1
    assert versions[0].content == "嗯，我小时候就是住在外婆家。"

    progress_response = await client.get("/api/mobile/memoir/voice-progress")
    assert progress_response.status_code == 200
    progress = progress_response.json()
    assert progress["session_count"] == 0
    assert progress["utterance_count"] == 0
    assert progress["user_message_count"] == 0
    assert progress["total_user_char_count"] == 0
    assert progress["total_audio_seconds"] == 0
    assert progress["modules"] == []


async def test_mobile_memoir_lists_book_inclusion_for_voice_transcripts(client, test_db):
    created = await create_test_voice_transcript(client, test_db, text="我小时候常在外婆家的院子里晒太阳。")
    project_id = created["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(created),
        chapter_title="院子里的童年",
    )
    section = await test_db.scalar(
        select(MemoirSection).where(MemoirSection.chapter_id == chapter.id)
    )
    assert section is not None

    response = await client.get(f"/api/mobile/memoir/voice-transcripts?project_id={project_id}")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == created["utterance_id"]
    assert item["is_included_in_book"] is True
    assert len(item["included_chapters"]) == 1
    assert item["included_chapters"][0]["title"] == "院子里的童年"
    assert item["included_chapters"][0]["sort_order"] == 0
    assert item["section_assignment"] == {
        "chapter_id": chapter.id,
        "chapter_title": "院子里的童年",
        "chapter_sort_order": 0,
        "section_id": section.id,
        "section_title": section.title,
        "section_sort_order": 0,
        "requires_user_assignment": False,
    }

    detail_response = await client.get(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}"
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["section_assignment"] == item["section_assignment"]


async def test_mobile_memoir_filters_voice_transcripts_by_book_inclusion(client, test_db):
    included = await create_test_voice_transcript(client, test_db, text="我小时候常在外婆家的院子里晒太阳。")
    not_included = await create_test_voice_transcript(client, test_db, text="后来我第一次离家去县城读书。")
    project_id = included["session"]["project_id"]
    await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(included),
        chapter_title="院子里的童年",
    )

    included_response = await client.get(
        "/api/mobile/memoir/voice-transcripts",
        params={"project_id": project_id, "included": "included"},
    )
    not_included_response = await client.get(
        "/api/mobile/memoir/voice-transcripts",
        params={"project_id": project_id, "included": "not_included"},
    )

    assert included_response.status_code == 200
    assert included_response.json()["total"] == 1
    assert included_response.json()["items"][0]["id"] == included["utterance_id"]

    assert not_included_response.status_code == 200
    assert not_included_response.json()["total"] == 1
    assert not_included_response.json()["items"][0]["id"] == not_included["utterance_id"]


async def test_mobile_memoir_delete_transcript_removes_section_material_and_marks_draft_stale(client, test_db):
    first = await create_test_voice_transcript(client, test_db, text="我小时候常在外婆家的院子里晒太阳。")
    second = await create_test_voice_transcript(client, test_db, text="后来我第一次离家去县城读书。")
    project_id = first["session"]["project_id"]
    _, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_refs=[memoir_source_ref(first), memoir_source_ref(second)],
        chapter_title="童年和离家",
    )
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter.id,
        status=MemoirBookChapterDraftStatus.CONFIRMED.value,
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter.id,
            version=1,
            content="我小时候常在外婆家的院子里晒太阳。后来我第一次离家去县城读书。",
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
            used_material_ids_json=[first["utterance_id"], second["utterance_id"]],
        )
    )
    await test_db.commit()
    response = await client.delete(f"/api/mobile/memoir/voice-transcripts/{second['utterance_id']}")

    assert response.status_code == 204
    remaining_material_ids = set(
        (
            await test_db.scalars(
                select(MemoirSectionMaterial.utterance_id)
                .join(MemoirSection, MemoirSection.id == MemoirSectionMaterial.section_id)
                .where(MemoirSection.chapter_id == chapter.id)
            )
        ).all()
    )
    assert remaining_material_ids == {first["utterance_id"]}

    refreshed_draft = await test_db.scalar(
        select(MemoirBookChapterDraft).where(MemoirBookChapterDraft.id == draft.id)
    )
    assert refreshed_draft.status == MemoirBookChapterDraftStatus.DRAFT.value
    assert refreshed_draft.metadata_json["status_reason"] == "materials_deleted"

async def test_mobile_memoir_ai_cleanup_creates_corrected_version(client, test_db, monkeypatch):
    created = await create_test_voice_transcript(client, test_db)
    cleanup_ai = CapturingAIClient("我小时候住在外婆家。")
    monkeypatch.setattr(
        "app.services.memoir.voice_transcripts.get_ai_service",
        lambda model=None: cleanup_ai,
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/cleanup",
        json={"ai_model": DEFAULT_AI_TEXT_MODEL, "transcript_polish_level": "high"},
    )

    assert response.status_code == 200
    item = response.json()
    assert item["transcript_raw"] == "嗯，我小时候就是住在外婆家。"
    assert item["transcript_edited"] == "我小时候住在外婆家。"
    assert "重组句式与表达层次" in cleanup_ai.prompt

    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    version = await test_db.scalar(
        select(MemoirTranscriptVersion).where(MemoirTranscriptVersion.id == message.transcript_version_id)
    )
    assert version.version_type == "dialect_corrected"
    assert version.created_by == "ai"
    assert version.provider == "fake-ai"
    assert version.content == "我小时候住在外婆家。"

    organization_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.dedupe_key == f"organize_material:{created['utterance_id']}"
        )
    )
    assert organization_job is None
    topic_map_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.job_type == MemoirEditingJobType.UPDATE_TOPIC_MAP.value
        )
    )
    assert topic_map_job is not None
    assert topic_map_job.payload_json["material_ids"] == [created["utterance_id"]]
    assert topic_map_job.payload_json["ai_model"] == DEFAULT_AI_TEXT_MODEL


async def test_mobile_memoir_voice_edit_preview_uses_ai_without_saving(client, test_db, monkeypatch):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="他是我的儿子，名字叫刘凯峰，今年10岁。",
    )
    project = await test_db.get(Project, created["session"]["project_id"])
    project.metadata_json = {"ai_model": DEFAULT_AI_TEXT_MODEL}
    await test_db.commit()
    ai_client = CapturingAIClient(
        json.dumps(
            {
                "action": "revise_transcript",
                "changes": [
                    {
                        "scope": "term",
                        "before": "刘凯峰",
                        "after": "刘凯风",
                    }
                ],
                "confidence": 0.97,
            },
            ensure_ascii=False,
        )
    )
    requested_models: list[str | None] = []

    def capture_ai_service(model=None):
        requested_models.append(model)
        return ai_client

    monkeypatch.setattr(
        "app.services.memoir.transcript_voice_edit.get_ai_service",
        capture_ai_service,
    )
    utterance_before = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    message_before = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    transcript_edited_before = utterance_before.transcript_edited
    message_text_before = message_before.content_text

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/voice-edit/preview",
        json={
            "instruction_text": "这里边的风是那个刮风的风，不是山峰的峰。",
            "draft_text": "他是我的儿子，名字叫刘凯峰，今年已经10岁了。",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["utterance_id"] == created["utterance_id"]
    assert data["action"] == "revise_transcript"
    assert data["original_text"] == "他是我的儿子，名字叫刘凯峰，今年10岁。"
    assert data["current_text"] == "他是我的儿子，名字叫刘凯峰，今年已经10岁了。"
    assert data["edited_text"] == "他是我的儿子，名字叫刘凯风，今年已经10岁了。"
    assert data["changes"] == [
        {
            "scope": "term",
            "before": "刘凯峰",
            "after": "刘凯风",
        }
    ]
    assert data["confidence"] == 0.97
    assert data["requires_confirmation"] is True
    assert data["provider"] == "fake-ai"
    assert data["model"] == "fake-cleaner"
    assert requested_models == [DEFAULT_AI_TEXT_MODEL]
    assert ai_client.call_kwargs["timeout"] == 180.0
    assert "只能改用户修正指令明确指出" in ai_client.prompt
    assert "passage 表示指定片段改写" in ai_client.prompt
    assert "changes 之外的文字和标点必须逐字保持不变" in ai_client.prompt
    assert '"edited_text":' not in ai_client.prompt
    assert '"reason":' not in ai_client.prompt
    assert "当前整理文字：他是我的儿子，名字叫刘凯峰，今年已经10岁了。" in ai_client.prompt
    assert "用户语音修正指令：\n这里边的风是那个刮风的风，不是山峰的峰。" in ai_client.prompt

    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == created["utterance_id"])
    )
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))
    user_versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion).where(
                MemoirTranscriptVersion.source_artifact_id == created["utterance_id"],
                MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
            )
        )
    ).scalars().all()
    assert utterance.transcript_edited == transcript_edited_before
    assert message.content_text == message_text_before
    assert user_versions == []


async def test_mobile_memoir_voice_edit_preview_rewrites_a_located_passage(client, test_db, monkeypatch):
    original = "爷爷退休后还在公司做事。后来他每天去公园散步。"
    revised_passage = "爷爷退休后经常去社区帮忙。"
    created = await create_test_voice_transcript(client, test_db, text=original)
    monkeypatch.setattr(
        "app.services.memoir.transcript_voice_edit.get_ai_service",
        lambda model=None: CapturingAIClient(
            json.dumps(
                {
                    "action": "revise_transcript",
                    "changes": [
                        {
                            "scope": "passage",
                            "before": "爷爷退休后还在公司做事。",
                            "after": revised_passage,
                        }
                    ],
                    "confidence": 0.91,
                },
                ensure_ascii=False,
            )
        ),
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/voice-edit/preview",
        json={"instruction_text": "把讲爷爷退休后的那段改成爷爷退休后经常去社区帮忙"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "revise_transcript"
    assert data["edited_text"] == f"{revised_passage}后来他每天去公园散步。"
    assert data["changes"] == [
        {
            "scope": "passage",
            "before": "爷爷退休后还在公司做事。",
            "after": revised_passage,
        }
    ]


async def test_mobile_memoir_voice_edit_preview_reconstructs_only_declared_changes(client, test_db, monkeypatch):
    original = "我小时候住在外婆家。"
    created = await create_test_voice_transcript(client, test_db, text=original)
    monkeypatch.setattr(
        "app.services.memoir.transcript_voice_edit.get_ai_service",
        lambda model=None: CapturingAIClient(
            json.dumps(
                {
                    "action": "revise_transcript",
                    "edited_text": "我小时候一直住在奶奶家。",
                    "changes": [
                        {
                            "scope": "term",
                            "before": "外婆",
                            "after": "奶奶",
                        }
                    ],
                    "confidence": 0.92,
                    "reply_text": "已完成修改。",
                },
                ensure_ascii=False,
            )
        ),
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/voice-edit/preview",
        json={"instruction_text": "把外婆改成奶奶"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "revise_transcript"
    assert data["edited_text"] == "我小时候住在奶奶家。"
    assert data["changes"] == [
        {
            "scope": "term",
            "before": "外婆",
            "after": "奶奶",
        }
    ]
    assert data["confidence"] == 0.92
    assert data["reply_text"] == "我按你的说明生成了一版修正，请确认后保存。"


async def test_mobile_memoir_voice_edit_preview_rejects_invalid_ai_payload(client, test_db, monkeypatch):
    created = await create_test_voice_transcript(client, test_db)
    monkeypatch.setattr(
        "app.services.memoir.transcript_voice_edit.get_ai_service",
        lambda model=None: CapturingAIClient("不是 JSON"),
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/voice-edit/preview",
        json={"instruction_text": "把外婆改成奶奶"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "AI 未返回可用修正预览"


async def test_mobile_memoir_voice_edit_preview_rejects_overlong_ai_result(client, test_db, monkeypatch):
    created = await create_test_voice_transcript(client, test_db, text="外婆和奶奶")
    monkeypatch.setattr(
        "app.services.memoir.transcript_voice_edit.get_ai_service",
        lambda model=None: CapturingAIClient(
            json.dumps(
                {
                    "action": "revise_transcript",
                    "changes": [
                        {"scope": "term", "before": "外婆", "after": "甲" * 800},
                        {"scope": "term", "before": "奶奶", "after": "乙" * 800},
                    ],
                    "confidence": 0.4,
                },
                ensure_ascii=False,
            )
        ),
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/voice-edit/preview",
        json={"instruction_text": "这里不是外婆，是奶奶"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "AI 修正结果过长，请重新说明要改哪一处"


async def test_mobile_memoir_transcript_edits_keep_only_latest_three_versions(client, test_db):
    created = await create_test_voice_transcript(client, test_db)

    for index in range(5):
        response = await client.patch(
            f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
            json={"transcript_edited": f"第 {index + 1} 次人工整理"},
        )
        assert response.status_code == 200

    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion)
            .where(
                MemoirTranscriptVersion.source_artifact_id == created["utterance_id"],
                MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
            )
            .order_by(MemoirTranscriptVersion.created_at, MemoirTranscriptVersion.id)
        )
    ).scalars().all()
    message = await test_db.scalar(select(MemoirChatMessage).where(MemoirChatMessage.id == created["message_id"]))

    assert [version.content for version in versions] == [
        "第 3 次人工整理",
        "第 4 次人工整理",
        "第 5 次人工整理",
    ]
    assert message.transcript_version_id == versions[-1].id


async def test_mobile_memoir_ai_cleanup_removes_repeated_fillers_when_model_is_too_conservative(
    client,
    test_db,
    monkeypatch,
):
    created = await create_test_voice_transcript(
        client,
        test_db,
        text="那个就是今天下雨了，那个那个那个雨下的很大。",
    )
    monkeypatch.setattr(
        "app.services.memoir.voice_transcripts.get_ai_service",
        lambda model=None: EchoFillerAIClient(),
    )

    response = await client.post(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}/cleanup",
        json={},
    )

    assert response.status_code == 200
    item = response.json()
    assert item["transcript_raw"] == "那个就是今天下雨了，那个那个那个雨下的很大。"
    assert item["transcript_edited"] == "今天下雨了，雨下的很大。"


def test_remove_obvious_oral_fillers_preserves_meaningful_deictic_phrase():
    assert _remove_obvious_oral_fillers("那个年代生活很不一样。") == "那个年代生活很不一样。"
    assert _remove_obvious_oral_fillers("那个那个那个雨下得很大。") == "雨下得很大。"


def test_normalize_memoir_asr_text_corrects_chapter_homophones_in_book_context():
    assert normalize_memoir_asr_text("现在有几个张杰啊？") == "现在有几个章节啊？"
    assert normalize_memoir_asr_text("这条还没收入张杰。") == "这条还没收入章节。"
    assert normalize_memoir_asr_text("你不是一个人名的张杰，是书的张杰。") == "你不是一个人名的章节，是书的章节。"
    assert normalize_memoir_asr_text("把第一张改名为童年往事。") == "把第一章改名为童年往事。"
    assert normalize_memoir_asr_text("第一张和第二张换一下。") == "第一章和第二章换一下。"
    assert normalize_memoir_asr_text("现在有几张啊？") == "现在有几章啊？"
    assert normalize_memoir_asr_text("张杰这个人，在书里单独占了一章。") == "张杰这个人，在书里单独占了一章。"


def test_memoir_asr_correction_rule_files_stay_in_sync():
    repo_root = Path(__file__).resolve().parents[2]
    backend_rules = json.loads(
        (repo_root / "backend/app/services/memoir/asr_correction_rules.json").read_text(encoding="utf-8")
    )
    miniprogram_rules = json.loads(
        (repo_root / "miniprogram/services/asr-correction-rules.json").read_text(encoding="utf-8")
    )

    assert backend_rules == miniprogram_rules


async def test_generate_cleaned_transcript_text_uses_fallback_when_not_strict():
    result = await generate_cleaned_transcript_text(
        "那个就是今天下雨了，那个那个那个雨下的很大。",
        ai_client=FailingAIClient(),
        strict=False,
    )

    assert result.content == "今天下雨了，雨下的很大。"
    assert result.provider is None
    assert result.created_by == MemoirCreatedBy.SYSTEM


async def test_generate_cleaned_transcript_text_skips_model_for_filler_only_text():
    ai_client = CapturingAIClient()
    result = await generate_cleaned_transcript_text("那个", ai_client=ai_client)

    assert result.content == "那个"
    assert result.provider is None
    assert result.created_by == MemoirCreatedBy.SYSTEM
    assert ai_client.prompt == ""


@pytest.mark.parametrize(
    ("level", "requirement"),
    [
        (MemoirTranscriptPolishLevel.LOW, "说话磕绊、赘词和没有信息增量的重复"),
        (MemoirTranscriptPolishLevel.MEDIUM, "自然、连贯、偏书面"),
        (MemoirTranscriptPolishLevel.HIGH, "重组句式与表达层次"),
    ],
)
def test_cleanup_prompt_applies_selected_polish_level(level, requirement):
    prompt = _build_cleanup_prompt("嗯，我我小时候住在那里。", transcript_polish_level=level)

    assert requirement in prompt
    assert "不新增事实、人物、关系、时间、地点、事件、因果或情绪" in prompt


async def test_generate_cleaned_transcript_text_sends_recent_context_to_llm():
    ai_client = CapturingAIClient()
    result = await generate_cleaned_transcript_text(
        "现在有几个张杰啊？",
        ai_client=ai_client,
        context=MemoirTranscriptCleanupContext(
            scene="voice_chat",
            guidance_mode="guided_interview",
            current_guidance_module="family",
            recent_user_utterances=("我刚才说到第一章。",),
            recent_assistant_messages=("我们继续聊这本书的章节安排。",),
        ),
    )

    assert result.content == "现在有几个章节啊？"
    assert "上下文只用于校对判断" in ai_client.prompt
    assert "场景：voice_chat" in ai_client.prompt
    assert "引导模式：guided_interview" in ai_client.prompt
    assert "当前引导模块：family" in ai_client.prompt
    assert "我刚才说到第一章。" in ai_client.prompt
    assert "我们继续聊这本书的章节安排。" in ai_client.prompt


async def test_generate_cleaned_transcript_text_normalizes_book_domain_asr_terms_when_not_strict():
    result = await generate_cleaned_transcript_text(
        "现在有几个张杰啊？",
        ai_client=FailingAIClient(),
        strict=False,
    )

    assert result.content == "现在有几个章节啊？"
    assert result.provider is None
    assert result.created_by == MemoirCreatedBy.SYSTEM


async def test_mobile_memoir_voice_transcripts_are_user_scoped(anonymous_client, test_db):
    first_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "voice-transcript-first@example.com",
            "password": "password123",
            "display_name": "第一位作者",
        },
    )
    assert first_register.status_code == 201
    created = await create_test_voice_transcript(anonymous_client, test_db)
    await anonymous_client.post("/api/auth/logout")

    second_register = await anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "voice-transcript-second@example.com",
            "password": "password123",
            "display_name": "第二位作者",
        },
    )
    assert second_register.status_code == 201

    list_response = await anonymous_client.get("/api/mobile/memoir/voice-transcripts")
    update_response = await anonymous_client.patch(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
        json={"transcript_edited": "跨用户编辑"},
    )
    delete_response = await anonymous_client.delete(
        f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}"
    )

    assert list_response.status_code == 200
    assert list_response.json()["total"] == 0
    assert update_response.status_code == 404
    assert delete_response.status_code == 404
