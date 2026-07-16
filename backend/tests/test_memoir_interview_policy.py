from __future__ import annotations

from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.memoir.interview_policy import (
    brief_context_snapshot,
)
from app.domain.memoir.narrative_types import (
    MemoirConversationMomentum,
    MemoirInterviewAction,
    MemoirInterviewPolicyExecutionMode,
    MemoirInterviewResponseOutcome,
    MemoirInterviewStoryState,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirFollowupTask,
    MemoirInterviewPolicyDecision,
    MemoirNarrativeGap,
    MemoirUtteranceInterviewAssignment,
)
from app.models.projects import Project
from app.services.memoir.project_deletion import delete_project
from app.services.memoir.narrative import followup_tasks, interview_policy


def test_open_narration_candidate_builder_respects_safety_constraints():
    regular = interview_policy.open_narration_brief(
        current_story_state=MemoirInterviewStoryState.ACTIVE,
        conversation_momentum=MemoirConversationMomentum.HIGH,
        existing_material_refs=("story-1",),
    )
    assert [candidate.action for candidate in regular.candidate_actions] == [
        MemoirInterviewAction.CONTINUE_LISTENING,
        MemoirInterviewAction.FOLLOW_CURRENT_STORY,
        MemoirInterviewAction.ASK_PERSON_RELATIONSHIP,
        MemoirInterviewAction.ASK_CAUSE_OR_RESULT,
        MemoirInterviewAction.SUMMARIZE_AND_CONFIRM,
        MemoirInterviewAction.RETURN_TO_PRIOR_STORY,
        MemoirInterviewAction.PAUSE_OR_END,
    ]

    paused = interview_policy.open_narration_brief(
        current_story_state=MemoirInterviewStoryState.ACTIVE,
        conversation_momentum=MemoirConversationMomentum.HIGH,
        safety_constraints=("pause_required",),
    )
    assert [candidate.action for candidate in paused.candidate_actions] == [
        MemoirInterviewAction.PAUSE_OR_END
    ]


def test_interview_brief_fingerprint_is_stable_and_contains_only_references():
    brief = interview_policy.open_narration_brief(
        current_story_state=MemoirInterviewStoryState.ACTIVE,
        conversation_momentum=MemoirConversationMomentum.STEADY,
        existing_material_refs=("story-2", "story-1"),
        unresolved_gap_ids=("gap-2", "gap-1"),
        user_preferences={
            "pace": "slow",
            "private_note": "不要把这段原话写进日志",
        },
    )
    first = brief_context_snapshot(brief)
    second = brief_context_snapshot(brief)

    assert first == second
    assert first["existing_material_refs"] == ["story-1", "story-2"]
    assert first["unresolved_gap_ids"] == ["gap-1", "gap-2"]
    assert first["user_preference_snapshot"] == {"pace": "slow"}
    assert len(str(first["brief_fingerprint"])) == 64
    assert "opening_question" not in first
    assert "purpose" not in first


async def test_policy_decision_message_and_user_response_are_traceable(test_db):
    project = Project(type="memoir", title="策略决策追踪")
    test_db.add(project)
    await test_db.flush()
    session = MemoirVoiceSession(
        project_id=project.id,
        guidance_mode="free_narration",
    )
    test_db.add(session)
    await test_db.flush()
    trigger = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="user",
        content_text="我想继续讲上学时的事情。",
        turn_index=1,
    )
    test_db.add(trigger)
    await test_db.flush()

    brief = interview_policy.open_narration_brief(
        current_story_state=MemoirInterviewStoryState.ACTIVE,
        conversation_momentum=MemoirConversationMomentum.HIGH,
        existing_material_refs=("story-school",),
    )
    decision = await interview_policy.create_policy_decision(
        test_db,
        session_id=session.id,
        trigger_message_id=trigger.id,
        brief=brief,
        selected_action=MemoirInterviewAction.FOLLOW_CURRENT_STORY,
        reason_code="story_has_momentum",
        reason_summary="当前故事仍在自然展开。",
        execution_mode=MemoirInterviewPolicyExecutionMode.SHADOW,
        model_provider="test",
        model_name="policy-model",
    )
    generated = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="assistant",
        content_text="那段上学经历里，您最先想到的是谁？",
        turn_index=2,
    )
    test_db.add(generated)
    await test_db.flush()
    await interview_policy.attach_generated_message(
        test_db,
        decision=decision,
        message_id=generated.id,
    )
    response_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="user",
        content_text="我最先想到的是班主任。",
        parent_message_id=generated.id,
        turn_index=3,
    )
    test_db.add(response_message)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=session.id,
        chat_message_id=response_message.id,
        transcript_raw=response_message.content_text,
    )
    test_db.add(utterance)
    await test_db.flush()
    assignment = await interview_policy.assign_utterance(
        test_db,
        utterance=utterance,
        followup_task=None,
    )
    await interview_policy.record_response_outcome(
        test_db,
        decision=decision,
        outcome=MemoirInterviewResponseOutcome.CONTINUED_STORY,
    )
    await interview_policy.record_response_outcome(
        test_db,
        decision=decision,
        outcome=MemoirInterviewResponseOutcome.CONTINUED_STORY,
        human_rating=1,
    )
    await test_db.commit()

    persisted_assignment = await test_db.scalar(
        select(MemoirUtteranceInterviewAssignment).where(
            MemoirUtteranceInterviewAssignment.utterance_id == utterance.id,
        )
    )
    assert persisted_assignment is not None
    assert persisted_assignment.id == assignment.id
    assert persisted_assignment.followup_task_id is None
    assert persisted_assignment.policy_decision_id == decision.id
    persisted_decision = await test_db.get(MemoirInterviewPolicyDecision, decision.id)
    assert persisted_decision is not None
    assert persisted_decision.generated_message_id == generated.id
    assert persisted_decision.user_response_outcome == "continued_story"
    assert persisted_decision.human_rating == 1
    with pytest.raises(ValueError, match="不能覆盖"):
        await interview_policy.record_response_outcome(
            test_db,
            decision=persisted_decision,
            outcome=MemoirInterviewResponseOutcome.CHANGED_TOPIC,
            human_rating=-1,
        )

    persisted_decision.human_rating = -1
    try:
        await test_db.commit()
        raise AssertionError("policy evaluation update should be rejected")
    except IntegrityError:
        await test_db.rollback()


async def test_policy_selection_and_assignment_target_are_immutable(test_db):
    project = Project(type="memoir", title="策略不可变约束")
    test_db.add(project)
    await test_db.flush()
    session = MemoirVoiceSession(
        project_id=project.id,
        guidance_mode="free_narration",
    )
    test_db.add(session)
    await test_db.flush()
    brief = interview_policy.open_narration_brief(
        current_story_state=MemoirInterviewStoryState.OPENING,
        conversation_momentum=MemoirConversationMomentum.LOW,
    )
    decision = await interview_policy.create_policy_decision(
        test_db,
        session_id=session.id,
        trigger_message_id=None,
        brief=brief,
        selected_action=MemoirInterviewAction.CONTINUE_LISTENING,
        reason_code="opening_turn",
        reason_summary="先让讲述者自然开始。",
        execution_mode=MemoirInterviewPolicyExecutionMode.SHADOW,
    )
    utterance = MemoirVoiceUtterance(
        voice_session_id=session.id,
        transcript_raw="我从小时候讲起。",
    )
    test_db.add(utterance)
    await test_db.flush()
    assignment = await interview_policy.assign_utterance(
        test_db,
        utterance=utterance,
        followup_task=None,
    )
    await test_db.commit()
    assignment_id = assignment.id

    decision.selected_action = MemoirInterviewAction.PAUSE_OR_END.value
    try:
        await test_db.commit()
        raise AssertionError("policy selection update should be rejected")
    except IntegrityError:
        await test_db.rollback()

    assignment = await test_db.get(
        MemoirUtteranceInterviewAssignment,
        assignment_id,
    )
    assert assignment is not None
    assignment.followup_task_id = "not-a-real-task"
    try:
        await test_db.commit()
        raise AssertionError("assignment target update should be rejected")
    except IntegrityError:
        await test_db.rollback()


async def test_policy_decision_rejects_action_outside_persisted_candidates(test_db):
    project = Project(type="memoir", title="策略候选约束")
    test_db.add(project)
    await test_db.flush()
    session = MemoirVoiceSession(
        project_id=project.id,
        guidance_mode="free_narration",
    )
    test_db.add(session)
    await test_db.flush()
    invalid = MemoirInterviewPolicyDecision(
        session_id=session.id,
        brief_fingerprint="a" * 64,
        state_snapshot_json={
            "schema_version": "1",
            "brief_fingerprint": "a" * 64,
        },
        candidate_actions_json=[
            {"action": "continue_listening", "risk": "low"}
        ],
        selected_action="pause_or_end",
        reason_code="invalid_test",
        reason_summary="",
        policy_version="test",
        execution_mode="shadow",
    )
    test_db.add(invalid)
    try:
        await test_db.commit()
        raise AssertionError("out-of-candidate action should be rejected")
    except IntegrityError:
        await test_db.rollback()


async def test_project_deletion_removes_policy_and_assignment_before_followup_task(
    test_db,
):
    project = Project(type="memoir", title="删除策略数据")
    test_db.add(project)
    await test_db.flush()
    session = MemoirVoiceSession(
        project_id=project.id,
        guidance_mode="guided_interview",
        session_goal="book_completion",
    )
    test_db.add(session)
    await test_db.flush()
    gap = MemoirNarrativeGap(
        project_id=project.id,
        scope_type="book",
        gap_type="relationship",
        severity="medium",
        title="人物关系",
        fingerprint=sha256(b"project-delete-policy").hexdigest(),
        status="open",
        detected_by="rules",
    )
    test_db.add(gap)
    await test_db.flush()
    task = MemoirFollowupTask(
        project_id=project.id,
        gap_id=gap.id,
        question="当时是什么关系？",
        purpose="补充人物关系",
        priority=1,
        status="in_progress",
        assigned_session_id=session.id,
        started_at=session.created_at,
        created_by="system",
    )
    test_db.add(task)
    await test_db.flush()
    session.current_followup_task_id = task.id
    task = await followup_tasks.get_task(
        test_db,
        project_id=project.id,
        task_id=task.id,
    )
    assert task is not None
    brief = interview_policy.followup_task_brief(
        task=task,
        current_story_state=MemoirInterviewStoryState.ACTIVE,
        conversation_momentum=MemoirConversationMomentum.STEADY,
    )
    decision = await interview_policy.create_policy_decision(
        test_db,
        session_id=session.id,
        trigger_message_id=None,
        brief=brief,
        selected_action=MemoirInterviewAction.FOLLOW_CURRENT_STORY,
        reason_code="followup_target",
        reason_summary="继续当前补访目标。",
        execution_mode=MemoirInterviewPolicyExecutionMode.SHADOW,
    )
    utterance = MemoirVoiceUtterance(
        voice_session_id=session.id,
        transcript_raw="她是我的小学老师。",
    )
    test_db.add(utterance)
    await test_db.flush()
    assignment = await interview_policy.assign_utterance(
        test_db,
        utterance=utterance,
        followup_task=task,
        policy_decision=decision,
    )
    await test_db.commit()
    decision_id = decision.id
    assignment_id = assignment.id
    project_id = project.id

    await delete_project(test_db, project)
    await test_db.commit()

    assert await test_db.get(Project, project_id) is None
    assert await test_db.get(MemoirInterviewPolicyDecision, decision_id) is None
    assert await test_db.get(MemoirUtteranceInterviewAssignment, assignment_id) is None
