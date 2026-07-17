import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.domain.memoir.narrative_types import (
    MemoirNarrativeGapActionReasonCode,
    MemoirNarrativeGapDetectedBy,
    MemoirNarrativeGapScopeType,
    MemoirNarrativeGapSeverity,
    MemoirNarrativeGapType,
)
from app.models.memoir import (
    MemoirBookPlan,
    MemoirBookPlanChapter,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import MemoirCompletionItem
from app.models.projects import Project
from app.schemas.memoir_narrative import MemoirNarrativeGapCandidate
from app.services.memoir.narrative import completion_items, interview_policy
from tests.memoir_helpers import (
    ensure_mobile_memoir_project,
    post_mobile_memoir_voice_session,
)


async def _chapter(test_db, *, project_id: str) -> MemoirBookPlanChapter:
    plan = MemoirBookPlan(
        project_id=project_id,
        title="测试书稿",
        status="confirmed",
    )
    test_db.add(plan)
    await test_db.flush()
    chapter = MemoirBookPlanChapter(
        plan_id=plan.id,
        project_id=project_id,
        title="工作这些年",
        sort_order=0,
        metadata_json={},
    )
    test_db.add(chapter)
    await test_db.flush()
    return chapter


def _candidate(
    chapter_id: str,
    *,
    description: str = "还没有讲清楚转折发生的原因。",
) -> MemoirNarrativeGapCandidate:
    return MemoirNarrativeGapCandidate(
        scope_type=MemoirNarrativeGapScopeType.CHAPTER,
        primary_chapter_id=chapter_id,
        gap_type=MemoirNarrativeGapType.CAUSALITY,
        severity=MemoirNarrativeGapSeverity.MEDIUM,
        target_slot="semantic:transition",
        title="工作转折需要补充",
        description=description,
        detected_by=MemoirNarrativeGapDetectedBy.AI,
        action_reason_signal=MemoirNarrativeGapActionReasonCode.MISSING_NEW_EVIDENCE,
    )


async def test_completion_candidate_upsert_uses_one_row_for_issue_and_question(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    chapter = await _chapter(test_db, project_id=project_id)

    first, created = await completion_items.upsert_candidates(
        test_db,
        project_id=project_id,
        candidates=[_candidate(chapter.id)],
    )
    second, created_again = await completion_items.upsert_candidates(
        test_db,
        project_id=project_id,
        candidates=[
            _candidate(
                chapter.id,
                description="转折原因和前后变化仍然需要补充。",
            )
        ],
    )
    await test_db.commit()

    assert created == 1
    assert created_again == 0
    assert first[0].id == second[0].id
    assert second[0].status == "open"
    assert second[0].question
    assert second[0].description == "转折原因和前后变化仍然需要补充。"
    assert second[0].detection_count == 2
    rows = list(
        (
            await test_db.scalars(
                select(MemoirCompletionItem).where(
                    MemoirCompletionItem.project_id == project_id
                )
            )
        ).all()
    )
    assert [row.id for row in rows] == [first[0].id]


async def test_completion_session_binds_answer_directly_and_reports_outcome(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    chapter = await _chapter(test_db, project_id=project_id)
    items, _ = await completion_items.upsert_candidates(
        test_db,
        project_id=project_id,
        candidates=[_candidate(chapter.id)],
    )
    await test_db.commit()
    item = items[0]

    created = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_completion_item_id": item.id,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session"]["id"]
    session = await test_db.get(MemoirVoiceSession, session_id)
    assert session is not None
    utterance = MemoirVoiceUtterance(
        voice_session_id=session.id,
        transcript_raw="后来公司转型，我才决定离职自己创业。",
    )
    test_db.add(utterance)
    await test_db.flush()

    await interview_policy.assign_utterance(
        test_db,
        utterance=utterance,
        completion_item=item,
    )
    await test_db.commit()

    assert utterance.completion_item_id == item.id
    stopped = await client.post(
        f"/api/mobile/memoir/voice-sessions/{session_id}/stop",
        json={},
    )
    assert stopped.status_code == 200
    assert stopped.json()["session"]["current_completion_item_id"] is None
    await test_db.refresh(item)
    assert item.status == "deferred"
    resolved = await completion_items.link_utterance_answer(
        test_db,
        project_id=project_id,
        utterance=utterance,
    )
    await test_db.commit()
    assert resolved is not None
    assert resolved.status == "resolved"
    outcome = await client.get(
        f"/api/memoir/projects/{project_id}/voice-sessions/"
        f"{session_id}/completion-outcome"
    )
    assert outcome.status_code == 200
    assert outcome.json() == {
        "status": "incorporated",
        "voice_session_id": session_id,
        "item_id": item.id,
        "item_status": "resolved",
        "chapter_id": chapter.id,
        "chapter_title": "",
        "utterance_id": utterance.id,
        "message": "回答已保存为本章素材。",
    }


async def test_completion_item_actions_and_current_schema_have_no_legacy_tables(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    chapter = await _chapter(test_db, project_id=project_id)
    items, _ = await completion_items.upsert_candidates(
        test_db,
        project_id=project_id,
        candidates=[_candidate(chapter.id)],
    )
    await test_db.commit()
    item = items[0]

    created = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_completion_item_id": item.id,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session"]["id"]

    deferred = await client.post(
        f"/api/memoir/projects/{project_id}/completion-items/{item.id}/defer",
        json={"note": "以后再讲"},
    )
    assert deferred.status_code == 200
    assert deferred.json()["status"] == "deferred"
    session = await test_db.get(MemoirVoiceSession, session_id)
    await test_db.refresh(session)
    assert session.current_completion_item_id is None
    unable = await client.post(
        f"/api/memoir/projects/{project_id}/completion-items/"
        f"{item.id}/unable-to-answer",
        json={"note": "确实想不起来"},
    )
    assert unable.status_code == 200
    assert unable.json()["status"] == "unable_to_answer"
    cannot_reopen = await client.post(
        f"/api/memoir/projects/{project_id}/completion-items/{item.id}/resolve",
        json={"note": "再次修改"},
    )
    assert cannot_reopen.status_code == 409

    table_names = set(
        (
            await test_db.scalars(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name LIKE 'memoir_%'"
                )
            )
        ).all()
    )
    assert "memoir_completion_items" in table_names
    assert "memoir_narrative_gaps" not in table_names
    assert "memoir_followup_tasks" not in table_names
    assert "memoir_followup_task_answers" not in table_names


async def test_switch_completion_item_never_reselects_the_current_item(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    chapter = await _chapter(test_db, project_id=project_id)
    first_candidates, _ = await completion_items.upsert_candidates(
        test_db,
        project_id=project_id,
        candidates=[_candidate(chapter.id)],
    )
    second_candidate = _candidate(
        chapter.id,
        description="人物关系还需要补充。",
    ).model_copy(
        update={
            "gap_type": MemoirNarrativeGapType.RELATIONSHIP,
            "target_slot": "semantic:relationship_clarity",
            "title": "人物关系需要补充",
        }
    )
    second_candidates, _ = await completion_items.upsert_candidates(
        test_db,
        project_id=project_id,
        candidates=[second_candidate],
    )
    await test_db.commit()
    first = first_candidates[0]
    second = second_candidates[0]

    created = await post_mobile_memoir_voice_session(
        client,
        project_id=project_id,
        json={
            "guidance_mode": "guided_interview",
            "session_goal": "book_completion",
            "current_completion_item_id": first.id,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session"]["id"]
    switched = await client.post(
        f"/api/memoir/projects/{project_id}/voice-sessions/"
        f"{session_id}/completion-item/switch",
        json={"current_item_id": first.id, "note": "换一个问题"},
    )

    assert switched.status_code == 200
    assert switched.json()["id"] == second.id
    await test_db.refresh(first)
    assert first.status == "deferred"
    assert first.defer_until is None


async def test_completion_item_rejects_chapter_from_another_project(
    client,
    test_db,
):
    project_id = await ensure_mobile_memoir_project(client)
    other_project = Project(type="memoir", title="另一本回忆录")
    test_db.add(other_project)
    await test_db.flush()
    other_chapter = await _chapter(test_db, project_id=other_project.id)
    item = MemoirCompletionItem(
        project_id=project_id,
        scope_type="chapter",
        chapter_id=other_chapter.id,
        item_type="causality",
        severity="medium",
        dimension="transition",
        title="跨项目补全项",
        description="不应允许保存。",
        fingerprint="b" * 64,
        action="interview",
        question="后来发生了什么？",
        priority=200,
        detected_by="ai",
    )
    test_db.add(item)

    with pytest.raises(IntegrityError):
        await test_db.flush()
    await test_db.rollback()
