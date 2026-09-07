from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.memoir.types import (
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from app.services.memoir.voice_transcript_correction import run_transcript_correction_job
from app.services.memoir.voice_transcripts import CleanedTranscriptText
from tests.memoir_helpers import create_test_voice_transcript


@pytest.mark.parametrize("manual_text", ["小时候我住在奶奶家。", "我小时候住在外婆家。"])
async def test_manual_edit_during_ai_correction_keeps_user_version(
    client, test_db, monkeypatch, manual_text,
):
    raw_text = "我小时候住在外婆家。"
    created = await create_test_voice_transcript(client, test_db, text=raw_text)
    utterance = await test_db.get(MemoirVoiceUtterance, created["utterance_id"])
    utterance.transcript_edited = ""
    await test_db.commit()
    manual_version_id = None

    async def generate(text, **kwargs):
        nonlocal manual_version_id
        assert text == raw_text
        # The API writes through its own transaction while the worker awaits AI.
        response = await client.patch(
            f"/api/mobile/memoir/voice-transcripts/{created['utterance_id']}",
            json={"transcript_edited": manual_text},
        )
        assert response.status_code == 200
        message = await test_db.get(MemoirChatMessage, created["message_id"])
        manual_version_id = message.transcript_version_id
        assert manual_version_id != created["transcript_version_id"]
        return CleanedTranscriptText(
            content="AI 根据旧版本生成的整理文字。",
            provider="test-ai",
            created_by=MemoirCreatedBy.AI,
        )

    monkeypatch.setattr(
        "app.services.memoir.voice_transcript_correction.generate_cleaned_transcript_text",
        generate,
    )
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)
    async with sessions() as worker:
        result = await run_transcript_correction_job(
            worker,
            SimpleNamespace(
                project_id=created["session"]["project_id"],
                payload_json={
                    "utterance_id": created["utterance_id"],
                    "raw_transcript_version_id": created["transcript_version_id"],
                },
            ),
        )
        await worker.commit()

    assert result.status == MemoirEditingJobStatus.SKIPPED
    async with sessions() as verify:
        utterance = await verify.get(MemoirVoiceUtterance, created["utterance_id"])
        message = await verify.get(MemoirChatMessage, created["message_id"])
        assert utterance.transcript_raw == raw_text
        assert utterance.transcript_edited == message.content_text == manual_text
        assert message.transcript_version_id == manual_version_id
        versions = (await verify.scalars(
            select(MemoirTranscriptVersion).where(
                MemoirTranscriptVersion.source_artifact_id == utterance.id,
            )
        )).all()
        assert {version.version_type for version in versions} == {
            MemoirTranscriptVersionType.ASR_RAW.value,
            MemoirTranscriptVersionType.USER_EDITED.value,
        }
