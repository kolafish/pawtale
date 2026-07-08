from sqlalchemy import select

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirInterviewType,
    MemoirTranscriptVersionType,
)
from app.models.memoir import (
    AudioFile,
    MemoirChatMessage,
    MemoirTranscriptVersion,
    MemoirVoiceUtterance,
)
from app.models.users import User
from tests.memoir_helpers import (
    create_test_book_plan,
    memoir_source_ref,
    post_mobile_memoir_voice_session,
)


async def _create_human_session(client) -> dict:
    return await _create_voice_session(client, interview_type=MemoirInterviewType.HUMAN_INTERVIEW.value)


async def _create_voice_session(
    client,
    *,
    interview_type: str,
    guidance_mode: str = "free_narration",
) -> dict:
    response = await post_mobile_memoir_voice_session(
        client,
        json={
            "guidance_mode": guidance_mode,
            "interview_type": interview_type,
            "provider": "volc_streaming_asr",
        },
    )
    assert response.status_code == 201
    return response.json()["session"]


async def _login_admin(client, test_db) -> dict:
    await client.post("/api/auth/logout")
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "admin-interview@example.com",
            "password": "password123",
            "display_name": "素材管理员",
        },
    )
    assert response.status_code == 201
    admin = await test_db.scalar(select(User).where(User.email == "admin-interview@example.com"))
    assert admin is not None
    admin.role = "admin"
    await test_db.commit()
    return response.json()["user"]


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
            "interview_type": session["interview_type"],
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
        source_material_type=(
            "interviewer_prompt"
            if role == MemoirChatMessageRole.INTERVIEWER.value
            else "ai_interaction"
            if role == MemoirChatMessageRole.ASSISTANT.value
            else ""
        ),
        source_route_action="ignore" if role in {
            MemoirChatMessageRole.INTERVIEWER.value,
            MemoirChatMessageRole.ASSISTANT.value,
        } else "",
        source_route_reason=(
            "访谈问题只作为上下文，不作为回忆录正文素材。"
            if role in {MemoirChatMessageRole.INTERVIEWER.value, MemoirChatMessageRole.ASSISTANT.value}
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


async def test_interview_editor_lists_effective_material_sessions_across_interview_types(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    ai_session = await _create_voice_session(
        client,
        interview_type=MemoirInterviewType.AI_INTERVIEW.value,
        guidance_mode="guided_interview",
    )
    human_session = await _create_human_session(client)
    empty_session = await _create_voice_session(
        client,
        interview_type=MemoirInterviewType.HUMAN_INTERVIEW.value,
    )
    ai_question = await _add_turn(
        test_db,
        user_id=user["id"],
        session=ai_session,
        role=MemoirChatMessageRole.ASSISTANT.value,
        text="你还记得温婆婆吗？",
        turn_index=0,
    )
    await _add_turn(
        test_db,
        user_id=user["id"],
        session=ai_session,
        role=MemoirChatMessageRole.USER.value,
        text="温婆婆那时候经常到我们屋头来。",
        turn_index=1,
        parent_message_id=ai_question["message_id"],
    )
    await _add_turn(
        test_db,
        user_id=user["id"],
        session=human_session,
        role=MemoirChatMessageRole.USER.value,
        text="我小时候在院坝里摆龙门阵。",
        turn_index=0,
    )

    response = await client.get(
        f"/api/memoir/projects/{ai_session['project_id']}/interview-editor/sessions"
    )

    assert response.status_code == 200
    items = response.json()["items"]
    listed_ids = {item["id"] for item in items}
    assert ai_session["id"] in listed_ids
    assert human_session["id"] in listed_ids
    assert empty_session["id"] not in listed_ids
    ai_item = next(item for item in items if item["id"] == ai_session["id"])
    assert ai_item["interview_type"] == "ai_interview"
    assert ai_item["question_count"] == 1
    assert ai_item["answer_count"] == 1
    assert ai_item["summary_text"] == "温婆婆那时候经常到我们屋头来。"

    human_only_response = await client.get(
        f"/api/memoir/projects/{ai_session['project_id']}/interview-editor/sessions",
        params={"interview_type": "human_interview", "effective_only": False},
    )
    assert human_only_response.status_code == 200
    human_only_ids = {item["id"] for item in human_only_response.json()["items"]}
    assert ai_session["id"] not in human_only_ids
    assert empty_session["id"] in human_only_ids


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


async def test_interview_editor_review_exports_grouped_markdown_and_checks(client, test_db):
    user = (await client.get("/api/auth/me")).json()
    session = await _create_human_session(client)
    question = await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.INTERVIEWER.value,
        text="你小时候最常去哪里？",
        turn_index=0,
    )
    await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.USER.value,
        text="我小时候最常去外婆家。",
        turn_index=1,
        parent_message_id=question["message_id"],
    )
    await _add_turn(
        test_db,
        user_id=user["id"],
        session=session,
        role=MemoirChatMessageRole.USER.value,
        text="后来我还经常去镇上赶场。",
        turn_index=2,
        parent_message_id=None,
    )

    response = await client.get(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/sessions/{session['id']}/review"
    )

    assert response.status_code == 200
    review = response.json()
    assert review["question_count"] == 1
    assert review["answer_count"] == 2
    assert review["unassigned_answer_count"] == 1
    assert review["not_included_answer_count"] == 2
    assert any(issue["code"] == "unassigned_answers" and issue["count"] == 1 for issue in review["issues"])
    assert "# 测试回忆录 - 真人访谈整理稿" in review["markdown"]
    assert "## Q1. 你小时候最常去哪里？" in review["markdown"]
    assert "我小时候最常去外婆家。" in review["markdown"]
    assert "## 未归属回答" in review["markdown"]
    assert "后来我还经常去镇上赶场。" in review["markdown"]


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


async def test_admin_can_edit_other_users_interview_materials(client, test_db):
    owner = (await client.get("/api/auth/me")).json()
    session = await _create_human_session(client)
    answer = await _add_turn(
        test_db,
        user_id=owner["id"],
        session=session,
        role=MemoirChatMessageRole.USER.value,
        text="我小时候在四川老家摆龙门阵。",
        turn_index=0,
        parent_message_id=None,
    )

    admin = await _login_admin(client, test_db)

    detail_response = await client.get(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/sessions/{session['id']}"
    )
    assert detail_response.status_code == 200
    assert [item["id"] for item in detail_response.json()["unassigned_answers"]] == [answer["utterance_id"]]

    create_response = await client.post(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/questions",
        json={"voice_session_id": session["id"], "text": "你小时候在老家主要做什么？"},
    )
    assert create_response.status_code == 201
    created_question = create_response.json()["questions"][0]

    move_response = await client.patch(
        f"/api/memoir/projects/{session['project_id']}/interview-editor/answers/{answer['message_id']}/parent",
        json={"parent_question_message_id": created_question["id"], "reason": "admin_manual_material_edit"},
    )
    assert move_response.status_code == 200
    assert move_response.json()["questions"][0]["answers"][0]["id"] == answer["utterance_id"]

    transcript_response = await client.patch(
        f"/api/mobile/memoir/voice-transcripts/{answer['utterance_id']}",
        json={"transcript_edited": "我小时候在四川老家摆龙门阵，讲了很多往事。"},
    )
    assert transcript_response.status_code == 200
    assert transcript_response.json()["display_text"] == "我小时候在四川老家摆龙门阵，讲了很多往事。"

    created_message = await test_db.scalar(
        select(MemoirChatMessage).where(MemoirChatMessage.id == created_question["id"])
    )
    answer_message = await test_db.scalar(
        select(MemoirChatMessage).where(MemoirChatMessage.id == answer["message_id"])
    )
    utterance = await test_db.scalar(
        select(MemoirVoiceUtterance).where(MemoirVoiceUtterance.id == answer["utterance_id"])
    )
    assert created_message is not None
    assert created_message.metadata_json["created_by"] == "admin"
    assert created_message.metadata_json["created_user_id"] == admin["id"]
    assert answer_message is not None
    assert answer_message.parent_message_id == created_question["id"]
    assert answer_message.metadata_json["relation_updated_by"] == "admin"
    assert answer_message.metadata_json["relation_updated_user_id"] == admin["id"]
    assert utterance is not None
    assert utterance.transcript_edited == "我小时候在四川老家摆龙门阵，讲了很多往事。"
