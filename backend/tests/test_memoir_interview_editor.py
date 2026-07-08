from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    AudioFile,
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from tests.memoir_helpers import (
    create_test_book_plan,
    memoir_source_ref,
    post_mobile_memoir_voice_session,
)


async def _create_human_session(client) -> dict:
    response = await post_mobile_memoir_voice_session(
        client,
        json={
            "guidance_mode": "free_narration",
            "interview_type": "human_interview",
            "provider": "volc_streaming_asr",
        },
    )
    assert response.status_code == 201
    return response.json()["session"]


async def _add_turn(
    test_db,
    *,
    user_id: str,
    session: dict,
    role: str,
    text: str,
    turn_index: int,
    parent_message_id: str | None = None,
    start_ms: int = 0,
    end_ms: int = 3000,
) -> dict:
    audio_file = AudioFile(
        user_id=user_id,
        storage_path=f"memoir/audio/{user_id}/interview-editor-{turn_index}.pcm",
        mime_type="audio/pcm",
        size_bytes=3200,
        duration_seconds=max(1, round((end_ms - start_ms) / 1000)),
    )
    version = MemoirTranscriptVersion(
        voice_session_id=session["id"],
        version_type=MemoirTranscriptVersionType.ASR_RAW.value,
        content=text,
        provider="volc_streaming_asr",
        created_by=MemoirCreatedBy.ASR.value,
    )
    test_db.add_all([audio_file, version])
    await test_db.flush()
    message = MemoirChatMessage(
        project_id=session["project_id"],
        voice_session_id=session["id"],
        role=role,
        content_text=text,
        content_status=MemoirChatContentStatus.FINAL.value,
        audio_file_id=audio_file.id,
        transcript_version_id=version.id,
        parent_message_id=parent_message_id,
        turn_index=turn_index,
        metadata_json={
            "source": "test",
            "speaker_role": "interviewer" if role == MemoirChatMessageRole.INTERVIEWER.value else "narrator",
            "interview_type": "human_interview",
        },
    )
    test_db.add(message)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=session["id"],
        chat_message_id=message.id,
        audio_file_id=audio_file.id,
        transcript_raw=text,
        transcript_edited="" if role == MemoirChatMessageRole.INTERVIEWER.value else text,
        start_ms=start_ms,
        end_ms=end_ms,
        provider="volc_streaming_asr",
        provider_payload_json={"source": "test"},
        source_material_type="interviewer_prompt" if role == MemoirChatMessageRole.INTERVIEWER.value else "",
        source_route_action="ignore" if role == MemoirChatMessageRole.INTERVIEWER.value else "",
        source_route_reason=(
            "真人采访者问题只作为上下文，不作为回忆录正文素材。"
            if role == MemoirChatMessageRole.INTERVIEWER.value
            else ""
        ),
    )
    test_db.add(utterance)
    await test_db.flush()
    version.source_artifact_id = utterance.id
    await test_db.commit()
    return {
        "message_id": message.id,
        "utterance_id": utterance.id,
        "audio_file_id": audio_file.id,
        "transcript_version_id": version.id,
        "session": session,
    }


async def test_interview_editor_groups_multiple_answers_under_human_question(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    session = await _create_human_session(client)
    question = await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.INTERVIEWER.value,
        text="你小时候家里有几口人？",
        turn_index=0,
    )
    first_answer = await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.USER.value,
        text="我们家那时候有六口人。",
        turn_index=1,
        parent_message_id=question["message_id"],
        start_ms=3100,
        end_ms=6500,
    )
    second_answer = await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.USER.value,
        text="后来哥哥去县城读书，家里就少一个人。",
        turn_index=2,
        parent_message_id=question["message_id"],
        start_ms=6600,
        end_ms=12000,
    )
    _plan, chapter = await create_test_book_plan(
        test_db,
        project_id=session["project_id"],
        source_ref=memoir_source_ref(first_answer, quote="我们家那时候有六口人。"),
        chapter_title="家里人",
    )

    detail_response = await client.get(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/sessions/{session['id']}"
    )
    list_response = await client.get(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/sessions"
    )

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["session"]["question_count"] == 1
    assert detail["session"]["answer_count"] == 2
    assert detail["session"]["unassigned_answer_count"] == 0
    assert detail["questions"][0]["id"] == question["message_id"]
    assert [item["id"] for item in detail["questions"][0]["answers"]] == [
        first_answer["utterance_id"],
        second_answer["utterance_id"],
    ]
    assert detail["questions"][0]["answers"][0]["is_included_in_book"] is True
    assert detail["questions"][0]["answers"][0]["included_chapters"][0] == {
        "chapter_id": chapter.id,
        "title": "家里人",
        "sort_order": 0,
    }
    assert detail["unassigned_answers"] == []
    assert [item["role"] for item in detail["raw_timeline"]] == ["interviewer", "user", "user"]

    assert list_response.status_code == 200
    listed = list_response.json()["items"][0]
    assert listed["id"] == session["id"]
    assert listed["question_count"] == 1
    assert listed["answer_count"] == 2
    assert listed["unassigned_answer_count"] == 0


async def test_interview_editor_keeps_answer_without_parent_unassigned(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    session = await _create_human_session(client)
    question = await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.INTERVIEWER.value,
        text="你后来什么时候去县城读书？",
        turn_index=0,
    )
    answer = await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.USER.value,
        text="那时候已经十多岁了。",
        turn_index=1,
        parent_message_id=None,
    )

    response = await client.get(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/sessions/{session['id']}"
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail["questions"][0]["id"] == question["message_id"]
    assert detail["questions"][0]["answer_count"] == 0
    assert detail["questions"][0]["answers"] == []
    assert [item["id"] for item in detail["unassigned_answers"]] == [answer["utterance_id"]]
    assert detail["session"]["unassigned_answer_count"] == 1


async def test_interview_editor_updates_interviewer_question_without_promoting_it_to_source(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    session = await _create_human_session(client)
    question = await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.INTERVIEWER.value,
        text="你小时候家里有几口人？",
        turn_index=0,
    )
    await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.USER.value,
        text="我们家那时候有六口人。",
        turn_index=1,
        parent_message_id=question["message_id"],
    )

    response = await client.patch(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/questions/{question['message_id']}",
        json={"text": "你小时候家里一共有几口人？"},
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail["questions"][0]["text"] == "你小时候家里一共有几口人？"
    message = await test_db.scalar(
        select(MemoirChatMessage).where(MemoirChatMessage.id == question["message_id"])
    )
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == question["utterance_id"])
    )
    versions = (
        await test_db.execute(
            select(MemoirTranscriptVersion)
            .where(
                MemoirTranscriptVersion.source_artifact_id == question["utterance_id"],
                MemoirTranscriptVersion.version_type == MemoirTranscriptVersionType.USER_EDITED.value,
            )
        )
    ).scalars().all()
    assert message is not None
    assert message.content_status == MemoirChatContentStatus.CORRECTED.value
    assert message.metadata_json["text_original"] == "你小时候家里有几口人？"
    assert message.metadata_json["text_updated_from"] == "web_interview_editor"
    assert utterance is not None
    assert utterance.transcript_edited == "你小时候家里一共有几口人？"
    assert utterance.source_material_type == "interviewer_prompt"
    assert utterance.source_route_action == "ignore"
    assert len(versions) == 1
    assert versions[0].content == "你小时候家里一共有几口人？"


async def test_interview_editor_creates_manual_question_and_moves_answer(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    session = await _create_human_session(client)
    answer = await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.USER.value,
        text="那时候已经十多岁了。",
        turn_index=0,
        parent_message_id=None,
    )

    create_response = await client.post(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/questions",
        json={"voice_session_id": session["id"], "text": "你是什么时候去县城读书的？"},
    )
    assert create_response.status_code == 201
    created_question = create_response.json()["questions"][0]

    move_response = await client.patch(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/answers/{answer['message_id']}/parent",
        json={"parent_question_message_id": created_question["id"], "reason": "人工整理问答关系"},
    )

    assert move_response.status_code == 200
    detail = move_response.json()
    assert detail["unassigned_answers"] == []
    assert detail["questions"][0]["answers"][0]["id"] == answer["utterance_id"]
    message = await test_db.scalar(
        select(MemoirChatMessage).where(MemoirChatMessage.id == answer["message_id"])
    )
    assert message is not None
    assert message.parent_message_id == created_question["id"]
    assert message.metadata_json["captured_parent_message_id"] is None
    assert message.metadata_json["relation_updated_from"] == "web_interview_editor"
    assert message.metadata_json["relation_update_reason"] == "人工整理问答关系"


async def test_interview_editor_rejects_answer_parent_from_another_session(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    session = await _create_human_session(client)
    other_session = await _create_human_session(client)
    answer = await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.USER.value,
        text="那时候已经十多岁了。",
        turn_index=0,
        parent_message_id=None,
    )
    other_question = await _add_turn(
        test_db,
        user_id=user["id"],
        session=other_session,
        role=MemoirChatMessageRole.INTERVIEWER.value,
        text="你在另一场访谈里说了什么？",
        turn_index=0,
    )

    response = await client.patch(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/answers/{answer['message_id']}/parent",
        json={"parent_question_message_id": other_question["message_id"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "问题和回答必须属于同一次访谈"
