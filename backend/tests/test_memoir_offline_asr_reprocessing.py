from __future__ import annotations

from datetime import datetime

import pytest

from sqlalchemy import select

from app.services.ai_base import AITextServiceBase
from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirGuidanceMode,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    AudioFile,
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.projects import Project
from app.models.users import User
from app.services.memoir.multi_asr_verification import (
    MultiASRVerificationRequest,
    build_multi_asr_verification_prompt,
)
from app.services.memoir.offline_asr import (
    OfflineASRResult,
    STEPFUN_OFFLINE_ASR_PROVIDER,
    VOLC_OFFLINE_ASR_PROVIDER,
    memoir_offline_asr_hotwords,
)
from app.services.memoir.offline_asr_reprocessing import reprocess_offline_asr
from app.services.memoir.transcript_projection import cleanup_source_text
from app.services.memoir.voice_transcripts import prune_transcript_versions_for_utterance


class FakeCleanupAI(AITextServiceBase):
    provider_name = "fake-ai"
    model_name = "fake-model"

    def __init__(self, text: str | list[str] = "爷爷退休后还在帮公司跑事情。") -> None:
        self.responses = [text] if isinstance(text, str) else text
        self.calls: list[dict[str, object]] = []

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, "kwargs": kwargs})
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def test_multi_asr_verification_prompt_uses_narrator_origin():
    prompt = build_multi_asr_verification_prompt(
        MultiASRVerificationRequest(
            asr_results=(OfflineASRResult(VOLC_OFFLINE_ASR_PROVIDER, "小时候住在河边。"),),
            narrator_origin="浙江宁波",
        )
    )

    assert "来自浙江宁波" in prompt
    assert "四川及重庆方言" not in prompt


def test_offline_asr_hotwords_omit_default_project_title():
    project = Project(title="我的回忆录", type="memoir", metadata_json={})

    assert memoir_offline_asr_hotwords(project) == ()


async def _create_project(test_db):
    test_db.add(
        User(
            id="user-1",
            email="offline-asr@example.com",
            password_hash="test-only",
            display_name="离线 ASR 测试用户",
        )
    )
    project = Project(
        title="余仲尤的往事",
        type="memoir",
        metadata_json={
            "memoir_profile": {
                "narrator_name": "余仲尤",
                "narrator_origin": "四川成都",
            },
            "memoir_dialect_glossary_entries": [
                {"source": "新凡", "target": "新繁"},
            ],
        },
    )
    test_db.add(project)
    await test_db.flush()
    voice_session = MemoirVoiceSession(
        project_id=project.id,
        status="created",
        guidance_mode=MemoirGuidanceMode.FREE_NARRATION.value,
    )
    test_db.add(voice_session)
    await test_db.flush()
    return project, voice_session


async def _add_audio(test_db, *, user_id: str = "user-1", size_bytes: int = 32000):
    audio_file = AudioFile(
        user_id=user_id,
        storage_path=f"memoir/audio/{user_id}/audio.pcm",
        mime_type="audio/pcm",
        size_bytes=size_bytes,
    )
    test_db.add(audio_file)
    await test_db.flush()
    return audio_file


@pytest.mark.parametrize("manual_type", ["user_edited", "dialect_corrected"])
async def test_offline_asr_reprocess_dry_run_skips_writes_and_user_edits(test_db, manual_type):
    project, voice_session = await _create_project(test_db)
    audio_file = await _add_audio(test_db)
    candidate = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        audio_file_id=audio_file.id,
        transcript_raw="旧的实时识别",
        transcript_edited="旧的自动整理",
        created_at=datetime(2026, 7, 8, 8, 1, 45),
    )
    edited = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        audio_file_id=audio_file.id,
        transcript_raw="人工已经校过",
        transcript_edited="人工已经校过",
        created_at=datetime(2026, 7, 8, 8, 0, 0),
    )
    interviewer = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        audio_file_id=audio_file.id,
        transcript_raw="采访者问题",
        provider_payload_json={"speaker_role": "interviewer"},
        created_at=datetime(2026, 7, 8, 8, 0, 0),
    )
    test_db.add_all([candidate, edited, interviewer])
    await test_db.flush()
    candidate_id = candidate.id
    project_id = project.id
    test_db.add(
        MemoirTranscriptVersion(
            voice_session_id=voice_session.id,
            source_artifact_id=edited.id,
            version_type=manual_type,
            content="人工已经校过",
            created_by=MemoirCreatedBy.USER.value,
        )
    )
    await test_db.commit()

    calls: list[str] = []

    async def fake_runner(request):
        calls.append(request.utterance.id)
        return []

    result = await reprocess_offline_asr(
        test_db,
        project_id=project_id,
        cutoff_created_at_utc=datetime(2026, 7, 8, 8, 2, 59),
        dry_run=True,
        asr_runner=fake_runner,
        ai_client=FakeCleanupAI(),
    )

    assert result["candidate_count"] == 1
    assert result["candidates"][0]["utterance_id"] == candidate_id
    assert calls == []
    assert await test_db.scalar(
        select(MemoirTranscriptVersion).where(
            MemoirTranscriptVersion.source_artifact_id == candidate_id,
            MemoirTranscriptVersion.provider == VOLC_OFFLINE_ASR_PROVIDER,
        )
    ) is None


@pytest.mark.parametrize("level", ["low", "medium", "high"])
async def test_offline_asr_reprocess_writes_multi_asr_versions_and_corrected_text(test_db, level):
    project, voice_session = await _create_project(test_db)
    project.metadata_json = {
        **(project.metadata_json or {}),
        "transcript_polish_level": level,
    }
    project_id = project.id
    audio_file = await _add_audio(test_db)
    question_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.INTERVIEWER.value,
        content_text="爷爷退休以后还在忙什么？",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=1,
    )
    answer_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.USER.value,
        content_text="旧的自动整理",
        content_status=MemoirChatContentStatus.FINAL.value,
        turn_index=2,
    )
    test_db.add_all([question_message, answer_message])
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        chat_message_id=answer_message.id,
        audio_file_id=audio_file.id,
        transcript_raw="旧的实时识别",
        transcript_edited="旧的自动整理",
        created_at=datetime(2026, 7, 8, 8, 1, 45),
    )
    test_db.add(utterance)
    await test_db.commit()

    async def fake_runner(request):
        assert request.providers == ("volc", "stepfun")
        assert request.hotwords == ("余仲尤", "四川成都", "余仲尤的往事", "新凡", "新繁")
        return [
            OfflineASRResult(
                VOLC_OFFLINE_ASR_PROVIDER,
                "爷爷退休后还在帮公司跑事情。",
                {"segment_count": 1},
            ),
            OfflineASRResult(
                STEPFUN_OFFLINE_ASR_PROVIDER,
                "爷爷退休以后还帮公司跑事情。",
                {"segment_count": 1},
            ),
        ]

    fake_ai = FakeCleanupAI([
        "爷爷退休以后还在帮公司跑事情。",
        "爷爷退休以后仍在为公司奔波。",
    ])
    result = await reprocess_offline_asr(
        test_db,
        project_id=project_id,
        cutoff_created_at_utc=datetime(2026, 7, 8, 8, 2, 59),
        dry_run=False,
        asr_runner=fake_runner,
        ai_client=fake_ai,
    )

    assert result["processed_count"] == 1
    assert result["failed_count"] == 0
    refreshed = await test_db.get(MemoirVoiceUtterance, utterance.id)
    refreshed_message = await test_db.get(MemoirChatMessage, answer_message.id)
    assert refreshed.transcript_raw == "爷爷退休后还在帮公司跑事情。"
    assert refreshed.transcript_edited == "爷爷退休以后仍在为公司奔波。"
    assert refreshed.provider == VOLC_OFFLINE_ASR_PROVIDER
    recognition_metadata = refreshed.provider_payload_json["material_offline_asr"]
    assert recognition_metadata["narrator_origin"] == "四川成都"
    assert recognition_metadata["dialect_code"] == "zh-CN-sichuanese"
    assert recognition_metadata["dialect_label"] == "四川话"
    assert recognition_metadata["dialect_enhancement_enabled"] is True
    assert recognition_metadata["providers"] == ["volc", "stepfun"]
    assert recognition_metadata["transcript_polish_level"] == level
    assert recognition_metadata["polish_provider"] == "fake-ai"
    assert refreshed_message.content_text == "爷爷退休以后仍在为公司奔波。"
    assert refreshed_message.content_status == MemoirChatContentStatus.CORRECTED.value
    assert "来自四川成都" in fake_ai.calls[0]["user_prompt"]
    assert "四川及重庆方言常见词" in fake_ai.calls[0]["user_prompt"]
    assert "新凡 => 新繁" in fake_ai.calls[0]["user_prompt"]
    assert "爷爷退休以后还在忙什么？" in fake_ai.calls[0]["user_prompt"]
    assert f"“{level}”" in fake_ai.calls[1]["user_prompt"]
    assert "新凡 => 新繁" in fake_ai.calls[1]["user_prompt"]

    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion).where(MemoirTranscriptVersion.source_artifact_id == utterance.id)
        )
    ).scalars().all()
    by_provider = {version.provider: version for version in versions}
    assert by_provider[VOLC_OFFLINE_ASR_PROVIDER].version_type == MemoirTranscriptVersionType.ASR_RAW.value
    assert by_provider[STEPFUN_OFFLINE_ASR_PROVIDER].version_type == MemoirTranscriptVersionType.ASR_RAW.value
    assert by_provider["fake-ai"].version_type == MemoirTranscriptVersionType.DIALECT_CORRECTED.value
    for index in range(5):
        assert await cleanup_source_text(test_db, refreshed) == "爷爷退休以后还在帮公司跑事情。"
        next_version = MemoirTranscriptVersion(
            voice_session_id=refreshed.voice_session_id, source_artifact_id=refreshed.id,
            version_type="dialect_corrected", created_by="ai", content=f"后续第 {index} 次润色。",
        )
        test_db.add(next_version)
        await test_db.flush()
        refreshed_message.transcript_version_id = next_version.id
        await test_db.flush()
        await prune_transcript_versions_for_utterance(
            test_db, utterance_id=refreshed.id, version_type="dialect_corrected",
        )
    assert await cleanup_source_text(test_db, refreshed) == "爷爷退休以后还在帮公司跑事情。"

    repeat_dry_run = await reprocess_offline_asr(
        test_db,
        project_id=project_id,
        cutoff_created_at_utc=datetime(2026, 7, 8, 8, 2, 59),
        dry_run=True,
    )
    assert repeat_dry_run["candidate_count"] == 0

    forced_dry_run = await reprocess_offline_asr(
        test_db,
        project_id=project_id,
        cutoff_created_at_utc=datetime(2026, 7, 8, 8, 2, 59),
        dry_run=True,
        force_reprocess=True,
    )
    assert forced_dry_run["candidate_count"] == 1


@pytest.mark.parametrize("manual_type", ["user_edited", "dialect_corrected"])
async def test_offline_asr_reprocess_does_not_overwrite_an_edit_saved_while_asr_is_running(test_db, manual_type):
    project, voice_session = await _create_project(test_db)
    audio_file = await _add_audio(test_db)
    utterance = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        audio_file_id=audio_file.id,
        transcript_raw="旧的实时识别",
        transcript_edited="旧的自动整理",
        created_at=datetime(2026, 7, 8, 8, 1, 45),
    )
    test_db.add(utterance)
    await test_db.commit()

    async def fake_runner(request):
        request.utterance.transcript_edited = "用户刚刚确认的文字"
        test_db.add(
            MemoirTranscriptVersion(
                voice_session_id=voice_session.id,
                source_artifact_id=request.utterance.id,
                version_type=manual_type,
                content="用户刚刚确认的文字",
                created_by=MemoirCreatedBy.USER.value,
            )
        )
        await test_db.commit()
        return [
            OfflineASRResult(VOLC_OFFLINE_ASR_PROVIDER, "离线识别一。"),
            OfflineASRResult(STEPFUN_OFFLINE_ASR_PROVIDER, "离线识别二。"),
        ]

    result = await reprocess_offline_asr(
        test_db,
        project_id=project.id,
        cutoff_created_at_utc=datetime(2026, 7, 8, 8, 2, 59),
        dry_run=False,
        asr_runner=fake_runner,
        ai_client=FakeCleanupAI("不应写回的离线校正文"),
    )

    test_db.expunge_all()
    refreshed = await test_db.get(MemoirVoiceUtterance, utterance.id)
    assert result["processed_count"] == 0
    assert result["failed_count"] == 1
    assert "user-edited" in result["failures"][0]["error"]
    assert refreshed is not None
    assert refreshed.transcript_edited == "用户刚刚确认的文字"


async def test_offline_asr_reprocess_creates_message_for_blank_saved_audio(test_db):
    project, voice_session = await _create_project(test_db)
    audio_file = await _add_audio(test_db)
    utterance = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        audio_file_id=audio_file.id,
        transcript_raw="",
        transcript_edited="",
        created_at=datetime(2026, 7, 7, 9, 0, 45),
    )
    test_db.add(utterance)
    await test_db.commit()

    async def fake_runner(request):
        return [OfflineASRResult(VOLC_OFFLINE_ASR_PROVIDER, "今天先讲医院里的事。", {"segment_count": 1})]

    result = await reprocess_offline_asr(
        test_db,
        project_id=project.id,
        cutoff_created_at_utc=datetime(2026, 7, 8, 8, 2, 59),
        dry_run=False,
        providers=("volc",),
        asr_runner=fake_runner,
        ai_client=FakeCleanupAI("今天先讲医院里的事。"),
    )

    assert result["processed_count"] == 1
    refreshed = await test_db.get(MemoirVoiceUtterance, utterance.id)
    assert refreshed.chat_message_id is not None
    message = await test_db.get(MemoirChatMessage, refreshed.chat_message_id)
    assert message.role == MemoirChatMessageRole.USER.value
    assert message.content_status == MemoirChatContentStatus.CORRECTED.value
    assert message.content_text == "今天先讲医院里的事。"
    assert refreshed.provider_payload_json["source"] == "offline_multi_asr_reprocess"


async def test_offline_asr_reprocess_continues_after_single_candidate_rollback(test_db):
    project, voice_session = await _create_project(test_db)
    project_id = project.id
    audio_file = await _add_audio(test_db)
    failing = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        audio_file_id=audio_file.id,
        transcript_raw="第一条",
        transcript_edited="第一条",
        created_at=datetime(2026, 7, 8, 8, 1, 0),
    )
    succeeding = MemoirVoiceUtterance(
        voice_session_id=voice_session.id,
        audio_file_id=audio_file.id,
        transcript_raw="第二条",
        transcript_edited="第二条",
        created_at=datetime(2026, 7, 8, 8, 0, 0),
    )
    test_db.add_all([failing, succeeding])
    await test_db.flush()
    failing_id = failing.id
    succeeding_id = succeeding.id
    await test_db.commit()

    async def fake_runner(request):
        if request.utterance.id == failing_id:
            raise RuntimeError("provider failed")
        return [OfflineASRResult(VOLC_OFFLINE_ASR_PROVIDER, "第二条重新识别。", {"segment_count": 1})]

    result = await reprocess_offline_asr(
        test_db,
        project_id=project_id,
        cutoff_created_at_utc=datetime(2026, 7, 8, 8, 2, 59),
        dry_run=False,
        providers=("volc",),
        asr_runner=fake_runner,
        ai_client=FakeCleanupAI("第二条校对完成。"),
    )

    assert result["processed_count"] == 1, result["failures"]
    assert result["failed_count"] == 1, result["failures"]
    assert result["failures"] == [{"utterance_id": failing_id, "error": "provider failed"}]
    refreshed_success = await test_db.get(MemoirVoiceUtterance, succeeding_id)
    refreshed_failure = await test_db.get(MemoirVoiceUtterance, failing_id)
    assert refreshed_success.transcript_edited == "第二条校对完成。"
    assert refreshed_failure.transcript_edited == "第一条"
