from pathlib import Path

from app.domain.memoir.types import (
    MemoirBookChapterDraftStatus,
    MemoirBookPlanStatus,
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    AudioFile,
    MemoirBookChapterDraft,
    MemoirBookChapterDraftVersion,
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)


async def ensure_mobile_memoir_project(client, title: str = "测试回忆录") -> str:
    response = await client.get("/api/mobile/memoir/projects")
    assert response.status_code == 200
    active_project_id = response.json()["active_project_id"]
    if active_project_id:
        return active_project_id

    created = await client.post("/api/mobile/memoir/projects", json={"title": title})
    assert created.status_code == 201
    return created.json()["id"]


async def post_mobile_memoir_voice_session(
    client,
    json: dict,
    *,
    project_id: str | None = None,
):
    if project_id is None:
        project_id = await ensure_mobile_memoir_project(client)
    return await client.post(
        "/api/mobile/memoir/voice-sessions",
        params={"project_id": project_id},
        json=json,
    )


async def create_test_voice_transcript(
    client,
    test_db,
    text: str = "嗯，我小时候就是住在外婆家。",
    *,
    project_id: str | None = None,
    question_text: str | None = None,
    guidance_mode: str = "free_narration",
    current_guidance_module: str | None = None,
):
    user = (await client.get("/api/auth/me")).json()
    session_payload = {"guidance_mode": guidance_mode, "provider": "volc_streaming_asr"}
    if current_guidance_module:
        session_payload["current_guidance_module"] = current_guidance_module
    create_response = await post_mobile_memoir_voice_session(client, project_id=project_id, json=session_payload)
    assert create_response.status_code == 201
    session = create_response.json()["session"]

    audio_file = AudioFile(
        user_id=user["id"],
        storage_path=f"memoir/audio/{user['id']}/{len(text)}.pcm",
        mime_type="audio/pcm",
        size_bytes=3200,
        duration_seconds=8,
    )
    test_db.add(audio_file)

    question_audio_file = None
    question_message = None
    if question_text:
        question_audio_file = AudioFile(
            user_id=user["id"],
            storage_path=f"memoir/audio/{user['id']}/question.pcm",
            mime_type="audio/pcm",
            size_bytes=1600,
            duration_seconds=4,
        )
        test_db.add(question_audio_file)
        await test_db.flush()
        question_message = MemoirChatMessage(
            project_id=session["project_id"],
            voice_session_id=session["id"],
            role=MemoirChatMessageRole.ASSISTANT.value,
            content_text=question_text,
            content_status=MemoirChatContentStatus.FINAL.value,
            audio_file_id=question_audio_file.id,
            turn_index=0,
            guidance_intent="opening",
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
        turn_index=1 if question_message else 0,
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
    await test_db.flush()
    version.source_artifact_id = utterance.id
    await test_db.commit()
    audio_relative_path = Path(audio_file.storage_path).relative_to(Path("memoir") / "audio")
    return {
        "user_id": user["id"],
        "session": session,
        "message_id": message.id,
        "utterance_id": utterance.id,
        "audio_file_id": audio_file.id,
        "audio_storage_path": audio_file.storage_path,
        "audio_relative_path": str(audio_relative_path),
        "transcript_version_id": version.id,
        "question_message_id": question_message.id if question_message else None,
        "question_audio_file_id": question_audio_file.id if question_audio_file else None,
    }


_USE_CREATED_TRANSCRIPT_VERSION = object()


def memoir_source_ref(
    created: dict,
    quote: str = "",
    *,
    transcript_version_id: str | None | object = _USE_CREATED_TRANSCRIPT_VERSION,
) -> dict:
    version_id = (
        created["transcript_version_id"]
        if transcript_version_id is _USE_CREATED_TRANSCRIPT_VERSION
        else transcript_version_id
    )
    return {
        "voice_session_id": created["session"]["id"],
        "message_id": created["message_id"],
        "utterance_id": created["utterance_id"],
        "transcript_version_id": version_id,
        "audio_file_id": created["audio_file_id"],
        "start_ms": 0,
        "end_ms": 8000,
        "quote": quote,
    }


async def create_test_book_plan(
    test_db,
    *,
    project_id: str,
    source_ref: dict | None = None,
    source_refs: list[dict] | None = None,
    status: MemoirBookPlanStatus = MemoirBookPlanStatus.CONFIRMED,
    title: str = "外婆回忆录",
    chapter_title: str = "外婆家的童年",
):
    plan = MemoirBookPlan(
        project_id=project_id,
        title=title,
        status=status.value,
        created_by=MemoirCreatedBy.USER.value,
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        plan_id=plan.id,
        project_id=project_id,
        title=chapter_title,
        sort_order=0,
        source_refs_json=source_refs if source_refs is not None else ([source_ref] if source_ref else []),
    )
    test_db.add(chapter)
    await test_db.commit()
    return plan, chapter


async def create_test_chapter_draft(
    test_db,
    *,
    project_id: str,
    chapter_id: str,
    status: MemoirBookChapterDraftStatus = MemoirBookChapterDraftStatus.CONFIRMED,
    content: str = "已确认章节稿",
) -> MemoirBookChapterDraft:
    draft = MemoirBookChapterDraft(
        project_id=project_id,
        chapter_id=chapter_id,
        status=status.value,
    )
    test_db.add(draft)
    await test_db.flush()
    test_db.add(
        MemoirBookChapterDraftVersion(
            draft_id=draft.id,
            project_id=project_id,
            chapter_id=chapter_id,
            version=1,
            content=content,
            created_by=MemoirCreatedBy.USER.value,
            prompt_snapshot="",
        )
    )
    await test_db.commit()
    return draft
