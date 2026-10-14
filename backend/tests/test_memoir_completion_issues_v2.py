from hashlib import sha256

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.domain.memoir.types import (
    MemoirCreatedBy,
    MemoirCompletionIssueOutcome,
    MemoirCompletionIssueStatus,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    MemoirCompletionIssue,
    MemoirEditingJob,
    MemoirSectionMaterial,
    MemoirTranscriptVersion,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.services.memoir import (
    sections,
    voice_transcript_correction,
)
from app.services.asr.streaming import StreamingASREvent
from app.services.memoir.voice_final_persistence import (
    VoiceFinalPersistenceService,
)
from app.services.memoir.voice_transcripts import CleanedTranscriptText
from tests.memoir_helpers import (
    create_test_book_plan_with_chapters,
    create_test_voice_transcript,
    ensure_mobile_memoir_project,
    memoir_source_ref,
    post_mobile_memoir_voice_session,
)


async def _section(client, test_db):
    project_id = await ensure_mobile_memoir_project(client)
    first = await create_test_voice_transcript(
        client,
        test_db,
        "朋友发来一张山桃花照片，后来邀请我一起爬山。",
        project_id=project_id,
    )
    second = await create_test_voice_transcript(
        client,
        test_db,
        "那天六个人一起爬了西山，正赶上花开。",
        project_id=project_id,
    )
    await create_test_book_plan_with_chapters(
        test_db,
        project_id=project_id,
        chapter_titles=["山桃花开的心动"],
        source_refs_by_chapter=[
            [memoir_source_ref(first), memoir_source_ref(second)]
        ],
    )
    response = await client.post(
        f"/api/memoir/projects/{project_id}/sections/bootstrap"
    )
    assert response.status_code == 200
    return project_id, response.json()["sections"][0], (first, second)


async def _completion_issue(
    test_db,
    *,
    project_id: str,
    chapter_id: str,
    material_id: str,
) -> MemoirCompletionIssue:
    issue = MemoirCompletionIssue(
        project_id=project_id,
        scope="chapter",
        chapter_id=chapter_id,
        issue_type="meaning",
        channel="interview",
        question="这次重新联系对你来说，最特别的地方是什么？",
        reason="个人意义会让重点更鲜明。",
        priority="high",
        fingerprint=sha256(
            f"{project_id}:{chapter_id}:meaning".encode()
        ).hexdigest(),
        related_material_ids_json=[material_id],
        input_signature="test-input",
    )
    test_db.add(issue)
    await test_db.flush()
    return issue






async def test_completion_voice_session_appends_every_answer_segment_to_section(
    client,
    test_db,
):
    project_id, section, materials = await _section(client, test_db)
    issue = await _completion_issue(
        test_db,
        project_id=project_id,
        chapter_id=section["chapter_id"],
        material_id=materials[0]["utterance_id"],
    )
    await test_db.commit()

    created = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_completion_issue_id": issue.id,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session"]["id"]
    assert created.json()["session"]["current_completion_issue_id"] == issue.id
    listed = await client.get(
        f"/api/memoir/projects/{project_id}/completion-issues"
    )
    assert listed.json()["issues"][0]["active_session_id"] == session_id
    persistence = VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=session_id,
        user_id=(await client.get("/api/auth/me")).json()["id"],
    )

    first = await persistence.persist_final(
        StreamingASREvent(
            type="final",
            text="那时我们已经两年没有见面了。",
            start_ms=0,
            end_ms=1600,
            raw={"segment": 1},
        )
    )
    second = await persistence.persist_final(
        StreamingASREvent(
            type="final",
            text="这次爬山让我们重新熟悉起来。",
            start_ms=1600,
            end_ms=3200,
            raw={"segment": 2},
        )
    )

    assignments = list(
        (
            await test_db.scalars(
                select(MemoirSectionMaterial)
                .where(
                    MemoirSectionMaterial.utterance_id.in_(
                        [first.utterance_id, second.utterance_id]
                    )
                )
                .order_by(MemoirSectionMaterial.sort_order)
            )
        ).all()
    )
    await test_db.refresh(issue)
    session = await test_db.get(MemoirVoiceSession, session_id)

    assert [item.utterance_id for item in assignments] == [
        first.utterance_id,
        second.utterance_id,
    ]
    assert {item.section_id for item in assignments} == {section["id"]}
    assert all(
        item.assignment_reason == f"completion_issue:{issue.id}"
        for item in assignments
    )
    assert issue.status == MemoirCompletionIssueStatus.CLOSED.value
    assert issue.outcome == MemoirCompletionIssueOutcome.ANSWERED.value
    assert issue.answered_material_id == first.utterance_id
    assert session is not None
    assert session.current_completion_issue_id == issue.id

    stopped = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session_id}/stop",
        json={},
    )
    assert stopped.status_code == 200
    assert stopped.json()["session"]["current_completion_issue_id"] is None


async def test_stopping_completion_session_without_answer_reopens_issue(
    client,
    test_db,
):
    project_id, section, materials = await _section(client, test_db)
    issue = await _completion_issue(
        test_db,
        project_id=project_id,
        chapter_id=section["chapter_id"],
        material_id=materials[0]["utterance_id"],
    )
    await test_db.commit()
    created = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_completion_issue_id": issue.id,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session"]["id"]
    test_db.add(
        MemoirVoiceUtterance(
            voice_session_id=session_id,
            completion_issue_id=issue.id,
            transcript_raw="   ",
        )
    )
    await test_db.commit()

    stopped = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session_id}/stop",
        json={},
    )
    await test_db.refresh(issue)

    assert stopped.status_code == 200
    assert issue.status == MemoirCompletionIssueStatus.OPEN.value
    assert issue.started_at is None
    assert stopped.json()["session"]["current_completion_issue_id"] is None


async def test_completion_answer_correction_refreshes_layered_material_version(
    client,
    test_db,
    monkeypatch,
):
    project_id, section, materials = await _section(client, test_db)
    issue = await _completion_issue(
        test_db,
        project_id=project_id,
        chapter_id=section["chapter_id"],
        material_id=materials[0]["utterance_id"],
    )
    await test_db.commit()
    created = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_completion_issue_id": issue.id,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session"]["id"]
    persistence = VoiceFinalPersistenceService(
        db=test_db,
        voice_session_id=session_id,
        user_id=(await client.get("/api/auth/me")).json()["id"],
    )
    persisted = await persistence.persist_final(
        StreamingASREvent(
            type="final",
            text="嗯，那次爬山让我们又熟悉了。",
            raw={"segment": 1},
        )
    )
    correction_job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.project_id == project_id,
            MemoirEditingJob.job_type
            == MemoirEditingJobType.CORRECT_TRANSCRIPT.value,
            MemoirEditingJob.dedupe_key
            == f"correct_transcript:{persisted.utterance_id}",
        )
    )
    assert correction_job is not None

    async def fake_clean(*_args, **_kwargs):
        return CleanedTranscriptText(
            content="那次爬山让我们重新熟悉起来。",
            provider="test",
            created_by=MemoirCreatedBy.AI,
        )

    monkeypatch.setattr(
        voice_transcript_correction,
        "generate_cleaned_transcript_text",
        fake_clean,
    )
    result = await voice_transcript_correction.run_transcript_correction_job(
        test_db,
        correction_job,
    )
    material = await test_db.scalar(
        select(MemoirSectionMaterial).where(
            MemoirSectionMaterial.utterance_id == persisted.utterance_id
        )
    )
    corrected = await test_db.scalar(
        select(MemoirTranscriptVersion).where(
            MemoirTranscriptVersion.source_artifact_id
            == persisted.utterance_id,
            MemoirTranscriptVersion.version_type
            == MemoirTranscriptVersionType.DIALECT_CORRECTED.value,
        )
    )

    assert result.status == MemoirEditingJobStatus.SUCCEEDED
    assert material is not None
    assert material.section_id == section["id"]
    assert corrected is not None
    assert material.transcript_version_id == corrected.id
    assert "organization_job_id" not in result.result_json
    with pytest.raises(
        HTTPException,
        match="正文文本版本不属于当前讲述素材",
    ):
        await sections.refresh_material_transcript_version(
            test_db,
            project_id=project_id,
            utterance_id=persisted.utterance_id,
            transcript_version_id=materials[0]["transcript_version_id"],
        )


@pytest.mark.parametrize("restore_target", [True, False])
async def test_empty_audio_recovery_keeps_original_question_after_session_moves(client, test_db, tmp_path, monkeypatch, restore_target):
    from app.domain.memoir.types import MemoirTranscriptionStatus
    from app.services.memoir import completion_interviews, voice_transcript_recovery
    monkeypatch.setattr("app.services.memoir.audio_files.MEMOIR_AUDIO_DIR", tmp_path / "audio")
    project_id, section, materials = await _section(client, test_db)
    issue = await _completion_issue(test_db, project_id=project_id, chapter_id=section["chapter_id"], material_id=materials[0]["utterance_id"])
    await test_db.commit()
    response = await post_mobile_memoir_voice_session(client, project_id=project_id, json={
        "guidance_mode": "guided_interview", "session_goal": "book_completion", "current_completion_issue_id": issue.id,
    })
    session_id = response.json()["session"]["id"]
    user_id = (await client.get("/api/auth/me")).json()["id"]
    persistence = VoiceFinalPersistenceService(db=test_db, voice_session_id=session_id, user_id=user_id)
    saved = await persistence.persist_unfinalized_audio(audio=b"\0" * 32000, transcription_status=MemoirTranscriptionStatus.EMPTY)
    utterance = await test_db.get(MemoirVoiceUtterance, saved.utterance_id)
    assert utterance.completion_issue_id == issue.id
    await test_db.refresh(issue)
    assert issue.status == "in_progress" and issue.answered_material_id is None
    session = await test_db.get(MemoirVoiceSession, session_id)
    await completion_interviews.release_session_issue(test_db, voice_session=session)
    other = MemoirCompletionIssue(project_id=project_id, scope="chapter", chapter_id=section["chapter_id"],
        issue_type="meaning", channel="interview", question="后来去了哪里？", reason="补充背景", priority="high",
        fingerprint=sha256(b"other-question").hexdigest(), input_signature="test-input", related_material_ids_json=[materials[0]["utterance_id"]])
    test_db.add(other)
    await test_db.flush()
    session.current_completion_issue_id = other.id
    if not restore_target:
        # Historical EMPTY recordings with no durable target must not attach to today's question.
        utterance.completion_issue_id = None
    await test_db.commit()
    utterance = await voice_transcript_recovery._load_recoverable_utterance(test_db, utterance_id=utterance.id, user_id=user_id, allow_admin=False)
    await voice_transcript_recovery._apply_recovered_transcript(test_db, utterance,
        StreamingASREvent(type="final", text="我们已经两年没有见面，那次爬山让大家重新熟悉起来。", raw={}),
        provider="offline-replay", transcript_polish_level="medium")
    await test_db.refresh(issue)
    await test_db.refresh(other)
    assert other.answered_material_id is None and other.status == "open"
    assignment = await test_db.scalar(select(MemoirSectionMaterial).where(MemoirSectionMaterial.utterance_id == utterance.id))
    if restore_target:
        assert issue.status == "closed" and issue.outcome == "answered"
        assert issue.answered_material_id == utterance.id
        assert assignment.section_id == section["id"]
    else:
        assert issue.status == "open" and assignment is None
    assert utterance.audio_file_id == saved.audio_file_id
