from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select

from app.models.memoir import MemoirAutomationEvent, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.memoir_narrative import (
    MemoirFollowupTask,
    MemoirFollowupTaskAnswer,
    MemoirNarrativeGap,
    MemoirUtteranceInterviewAssignment,
)
from tests.memoir_helpers import (
    add_test_chapter_source_links,
    create_test_book_plan,
    create_test_voice_transcript,
    memoir_source_ref,
)


async def _create_outcome_case(client, test_db):
    first = await create_test_voice_transcript(client, test_db, "我打开聊天窗口，最后没有发出消息。")
    project_id = first["session"]["project_id"]
    plan, chapter = await create_test_book_plan(
        test_db,
        project_id=project_id,
        source_ref=memoir_source_ref(first, "没有发出的消息"),
        chapter_title="界限与邀约",
    )
    answer = await create_test_voice_transcript(
        client,
        test_db,
        "我想用一个抽象场景作为这一章的开头。",
        project_id=project_id,
    )
    gap = MemoirNarrativeGap(
        project_id=project_id,
        scope_type="chapter",
        primary_chapter_id=chapter.id,
        gap_type="causality",
        severity="medium",
        target_slot="semantic:opening:outcome-test",
        title="章节开头还可以更清楚",
        description="需要明确章节开头。",
        fingerprint=sha256(b"completion-outcome-test").hexdigest(),
        status="open",
        detected_by="ai",
    )
    test_db.add(gap)
    await test_db.flush()
    task = MemoirFollowupTask(
        project_id=project_id,
        gap_id=gap.id,
        chapter_id=chapter.id,
        target_slot=gap.target_slot,
        answer_use="direct_narrative",
        question="这一章应该怎样开头？",
        purpose="补充章节开头。",
        priority=100,
        status="deferred",
        defer_reason="voice session ended",
        created_by="system",
    )
    test_db.add(task)
    await test_db.flush()
    session = await test_db.get(MemoirVoiceSession, answer["session"]["id"])
    utterance = await test_db.get(MemoirVoiceUtterance, answer["utterance_id"])
    assert session is not None
    assert utterance is not None
    session.session_goal = "book_completion"
    session.current_followup_task_id = task.id
    relation = MemoirFollowupTaskAnswer(
        project_id=project_id,
        task_id=task.id,
        utterance_id=utterance.id,
        result_role="related_context",
    )
    assignment = MemoirUtteranceInterviewAssignment(
        utterance_id=utterance.id,
        followup_task_id=task.id,
    )
    test_db.add_all([session, relation, assignment])
    await test_db.commit()
    return {
        "project_id": project_id,
        "plan": plan,
        "chapter": chapter,
        "session": session,
        "utterance": utterance,
        "task": task,
        "relation": relation,
    }


async def test_completion_outcome_waits_for_answer_assignment(client, test_db):
    case = await _create_outcome_case(client, test_db)
    assignment = await test_db.scalar(
        select(MemoirUtteranceInterviewAssignment).where(
            MemoirUtteranceInterviewAssignment.utterance_id
            == case["utterance"].id,
        )
    )
    assert assignment is not None
    await test_db.delete(assignment)
    await test_db.commit()

    response = await client.get(
        f"/api/memoir/projects/{case['project_id']}"
        f"/voice-sessions/{case['session'].id}/completion-outcome"
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == "processing"
    assert data["utterance_id"] == case["utterance"].id
    assert data["message"] == "回答已经保存，正在建立补全关联。"


async def test_completion_outcome_reports_semantic_context_recorded(client, test_db):
    case = await _create_outcome_case(client, test_db)
    case["utterance"].source_material_type = "memoir_content"
    case["utterance"].source_route_action = "record_as_context"
    case["utterance"].source_route_reason = "这是章节开头的编辑说明。"
    case["relation"].interpretation_status = "resolved"
    case["relation"].answer_kind = "clarification"
    case["relation"].clarification_text = "我想用一个抽象场景作为这一章的开头。"
    case["relation"].result_role = "target_answer"
    case["task"].status = "answered"
    case["task"].completed_at = datetime.now(UTC).replace(tzinfo=None)
    case["task"].defer_reason = ""
    test_db.add_all([case["utterance"], case["relation"], case["task"]])
    await test_db.commit()

    response = await client.get(
        f"/api/memoir/projects/{case['project_id']}"
        f"/voice-sessions/{case['session'].id}/completion-outcome"
    )

    assert response.status_code == 200, response.json()
    assert response.json() == {
        "status": "context_recorded",
        "voice_session_id": case["session"].id,
        "task_id": case["task"].id,
        "task_status": "answered",
        "chapter_id": case["chapter"].id,
        "chapter_title": "界限与邀约",
        "utterance_id": case["utterance"].id,
        "answer_kind": "clarification",
        "route_action": "record_as_context",
        "reason": "这是章节开头的编辑说明。",
        "message": "这段回答已作为《界限与邀约》的章节说明保存。",
        "review_event_id": None,
    }


async def test_completion_outcome_reports_ignored_answer_reason(client, test_db):
    case = await _create_outcome_case(client, test_db)
    case["utterance"].source_material_type = "noise"
    case["utterance"].source_route_action = "ignore"
    case["utterance"].source_route_reason = "只评价了章节写法，没有提供有效回答。"
    test_db.add(case["utterance"])
    await test_db.commit()

    response = await client.get(
        f"/api/memoir/projects/{case['project_id']}"
        f"/voice-sessions/{case['session'].id}/completion-outcome"
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == "not_used"
    assert data["chapter_title"] == "界限与邀约"
    assert data["reason"] == "只评价了章节写法，没有提供有效回答。"
    assert data["message"] == "这段话没有作为书稿素材使用。"


async def test_completion_outcome_reports_incorporated_chapter(client, test_db):
    case = await _create_outcome_case(client, test_db)
    case["utterance"].source_material_type = "memoir_content"
    case["utterance"].source_route_action = "auto_assign"
    case["utterance"].source_route_reason = "回答直接补充了章节内容。"
    await add_test_chapter_source_links(
        test_db,
        project_id=case["project_id"],
        plan_id=case["plan"].id,
        chapter_id=case["chapter"].id,
        source_refs=[memoir_source_ref({
            "session": {"id": case["session"].id},
            "message_id": case["utterance"].chat_message_id,
            "utterance_id": case["utterance"].id,
            "transcript_version_id": None,
            "audio_file_id": case["utterance"].audio_file_id,
        }, "抽象场景")],
    )
    test_db.add(case["utterance"])
    await test_db.commit()

    response = await client.get(
        f"/api/memoir/projects/{case['project_id']}"
        f"/voice-sessions/{case['session'].id}/completion-outcome"
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == "incorporated"
    assert data["chapter_title"] == "界限与邀约"
    assert data["message"] == "这段回答已收入《界限与邀约》的章节素材。"


async def test_completion_outcome_links_pending_review_event(client, test_db):
    case = await _create_outcome_case(client, test_db)
    case["utterance"].source_material_type = "memoir_content"
    case["utterance"].source_route_action = "hold_for_review"
    case["utterance"].source_route_reason = "需要确认是否为亲身经历。"
    case["relation"].interpretation_status = "needs_confirmation"
    case["relation"].confirmation_question = "这是亲身经历，还是章节写法说明？"
    event = MemoirAutomationEvent(
        project_id=case["project_id"],
        chapter_id=case["chapter"].id,
        event_type="source_held",
        status="needs_review",
        utterance_ids_json=[case["utterance"].id],
        summary_text=case["relation"].confirmation_question,
        metadata_json={
            "route": {
                "followup_answer": {
                    "task_id": case["task"].id,
                    "status": "needs_confirmation",
                }
            }
        },
    )
    test_db.add_all([case["utterance"], case["relation"], event])
    await test_db.commit()

    response = await client.get(
        f"/api/memoir/projects/{case['project_id']}"
        f"/voice-sessions/{case['session'].id}/completion-outcome"
    )

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == "needs_confirmation"
    assert data["review_event_id"] == event.id
    assert data["chapter_title"] == "界限与邀约"
