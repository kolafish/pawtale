from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirInterviewAction,
    MemoirInterviewPolicyFailureCode,
)
from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import (
    MemoirChatMessage,
    MemoirEditingJob,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirFollowupTask,
    MemoirInterviewPolicyDecision,
    MemoirInterviewPolicyEvaluation,
    MemoirNarrativeGap,
)
from app.models.projects import Project
from app.services.memoir.narrative import interview_policy
from app.services.memoir.narrative.interview_policy_shadow import (
    SHADOW_POLICY_VERSION,
    ShadowPolicySelectionError,
    run_shadow_policy_evaluation_job,
    schedule_shadow_policy_evaluation,
)


class FakeShadowPolicyAI:
    provider_name = "fake-policy"
    model_name = "fake-policy-model"

    def __init__(self, selected_action: str) -> None:
        self.selected_action = selected_action
        self.calls: list[dict[str, object]] = []

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        return f'{{"selected_action":"{self.selected_action}"}}'


async def _free_narration_turn(
    test_db,
    *,
    user_text: str = "我想继续讲小时候在河边玩耍的事情。",
) -> tuple[Project, MemoirVoiceSession, MemoirChatMessage, MemoirVoiceUtterance]:
    project = Project(type="memoir", title="策略 Shadow")
    test_db.add(project)
    await test_db.flush()
    session = MemoirVoiceSession(
        project_id=project.id,
        guidance_mode="free_narration",
        session_goal="open_narration",
        metadata_json={"interview_preferences": {"pace": "slow"}},
    )
    test_db.add(session)
    await test_db.flush()
    user_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="user",
        content_text=user_text,
        turn_index=0,
    )
    test_db.add(user_message)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=session.id,
        chat_message_id=user_message.id,
        transcript_raw=user_text,
    )
    generated_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="assistant",
        content_text="您慢慢讲，我在听。",
        guidance_intent="encourage_continue",
        turn_index=1,
    )
    test_db.add_all([utterance, generated_message])
    await test_db.commit()
    return project, session, generated_message, utterance


async def _scheduled_shadow_job(
    test_db,
    *,
    session: MemoirVoiceSession,
    generated_message: MemoirChatMessage,
    followup_task_id: str | None = None,
) -> MemoirEditingJob:
    job = await schedule_shadow_policy_evaluation(
        test_db,
        session=session,
        generated_message=generated_message,
        followup_task_id=followup_task_id,
        requested_ai_model="fake-policy-model",
        enabled=True,
    )
    assert job is not None
    await test_db.commit()
    return job


async def test_shadow_policy_job_records_controlled_decision_without_raw_labels(
    test_db,
):
    project, session, generated_message, _ = await _free_narration_turn(test_db)
    job = await _scheduled_shadow_job(
        test_db,
        session=session,
        generated_message=generated_message,
    )
    payload_text = str(job.payload_json)
    assert "河边" not in payload_text
    assert job.job_type == MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_SHADOW.value

    ai_client = FakeShadowPolicyAI("follow_current_story")
    result = await run_shadow_policy_evaluation_job(
        test_db,
        job,
        ai_client=ai_client,
    )
    await test_db.commit()

    decision = await test_db.get(
        MemoirInterviewPolicyDecision,
        result.result_json["decision_id"],
    )
    assert decision is not None
    assert decision.execution_mode == "shadow"
    assert decision.selected_action == "follow_current_story"
    assert decision.generated_message_id == generated_message.id
    assert decision.policy_version == SHADOW_POLICY_VERSION
    assert decision.model_provider == "fake-policy"
    assert decision.state_snapshot_json["user_preference_snapshot"] == {"pace": "slow"}
    assert "河边" not in str(decision.state_snapshot_json)
    assert "河边" not in decision.reason_summary
    assert ai_client.calls[0]["kwargs"]["temperature"] == 0.0
    assert "我想继续讲小时候在河边玩耍的事情" in str(
        ai_client.calls[0]["user_prompt"]
    )
    assert project.id == job.project_id


async def test_shadow_decision_is_not_attributed_to_later_user_answer(test_db):
    project, session, generated_message, _ = await _free_narration_turn(test_db)
    job = await _scheduled_shadow_job(
        test_db,
        session=session,
        generated_message=generated_message,
    )
    result = await run_shadow_policy_evaluation_job(
        test_db,
        job,
        ai_client=FakeShadowPolicyAI("continue_listening"),
    )
    decision = await test_db.get(
        MemoirInterviewPolicyDecision,
        result.result_json["decision_id"],
    )
    response_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="user",
        content_text="后来我们一直玩到天黑。",
        parent_message_id=generated_message.id,
        turn_index=2,
    )
    test_db.add(response_message)
    await test_db.flush()
    response_utterance = MemoirVoiceUtterance(
        voice_session_id=session.id,
        chat_message_id=response_message.id,
        transcript_raw=response_message.content_text,
    )
    test_db.add(response_utterance)
    await test_db.flush()

    assignment = await interview_policy.assign_utterance(
        test_db,
        utterance=response_utterance,
        followup_task=None,
    )
    await test_db.commit()

    assert decision is not None
    assert assignment.policy_decision_id is None


async def test_shadow_policy_rejects_action_outside_candidates(test_db):
    _, session, generated_message, _ = await _free_narration_turn(test_db)
    job = await _scheduled_shadow_job(
        test_db,
        session=session,
        generated_message=generated_message,
    )

    with pytest.raises(
        ShadowPolicySelectionError,
        match="shadow_policy_action_outside_candidates",
    ):
        await run_shadow_policy_evaluation_job(
            test_db,
            job,
            ai_client=FakeShadowPolicyAI("offer_new_topic"),
        )
    assert (
        await test_db.scalar(select(MemoirInterviewPolicyDecision))
    ) is None


async def test_shadow_policy_respects_explicit_pause_constraint(test_db):
    _, session, generated_message, _ = await _free_narration_turn(
        test_db,
        user_text="这件事我不想再说了，先到这里吧。",
    )
    job = await _scheduled_shadow_job(
        test_db,
        session=session,
        generated_message=generated_message,
    )
    result = await run_shadow_policy_evaluation_job(
        test_db,
        job,
        ai_client=FakeShadowPolicyAI("pause_or_end"),
    )
    await test_db.commit()

    decision = await test_db.get(
        MemoirInterviewPolicyDecision,
        result.result_json["decision_id"],
    )
    assert decision is not None
    assert decision.candidate_actions_json == [
        {"action": "pause_or_end", "risk": "terminal"}
    ]
    assert decision.state_snapshot_json["safety_constraint_codes"] == [
        "pause_required",
        "user_requested_pause",
    ]


async def test_book_completion_shadow_brief_keeps_task_and_recent_answer_refs(
    test_db,
):
    project = Project(type="memoir", title="补访 Shadow")
    test_db.add(project)
    await test_db.flush()
    session = MemoirVoiceSession(
        project_id=project.id,
        guidance_mode="guided_interview",
        session_goal="book_completion",
    )
    test_db.add(session)
    gap = MemoirNarrativeGap(
        project_id=project.id,
        scope_type="book",
        gap_type="relationship",
        severity="medium",
        title="人物关系",
        fingerprint=sha256(b"completion-shadow").hexdigest(),
        status="open",
        detected_by="rules",
    )
    test_db.add(gap)
    await test_db.flush()
    task = MemoirFollowupTask(
        project_id=project.id,
        gap_id=gap.id,
        question="康惠和您是什么关系？",
        purpose="补清人物关系",
        priority=1,
        status="in_progress",
        assigned_session_id=session.id,
        started_at=datetime.now(UTC).replace(tzinfo=None),
        created_by="system",
    )
    test_db.add(task)
    await test_db.flush()
    session.current_followup_task_id = task.id
    user_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="user",
        content_text="康惠是我的姐姐。",
        turn_index=0,
    )
    test_db.add(user_message)
    await test_db.flush()
    utterance = MemoirVoiceUtterance(
        voice_session_id=session.id,
        chat_message_id=user_message.id,
        transcript_raw=user_message.content_text,
    )
    generated_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="assistant",
        content_text="好的，这一段已经保存了。",
        turn_index=1,
    )
    test_db.add_all([utterance, generated_message])
    await test_db.commit()

    job = await _scheduled_shadow_job(
        test_db,
        session=session,
        generated_message=generated_message,
        followup_task_id=task.id,
    )
    result = await run_shadow_policy_evaluation_job(
        test_db,
        job,
        ai_client=FakeShadowPolicyAI("summarize_and_confirm"),
    )
    await test_db.commit()
    decision = await test_db.get(
        MemoirInterviewPolicyDecision,
        result.result_json["decision_id"],
    )

    assert decision is not None
    assert decision.state_snapshot_json["source_kind"] == "book_completion"
    assert decision.state_snapshot_json["followup_task_id"] == task.id
    assert decision.state_snapshot_json["unresolved_gap_ids"] == [gap.id]
    assert utterance.id in decision.state_snapshot_json["existing_material_refs"]
    assert "康惠是我的姐姐" not in str(decision.state_snapshot_json)


async def test_human_shadow_evaluation_is_controlled_and_append_only(test_db):
    _, session, generated_message, _ = await _free_narration_turn(test_db)
    job = await _scheduled_shadow_job(
        test_db,
        session=session,
        generated_message=generated_message,
    )
    result = await run_shadow_policy_evaluation_job(
        test_db,
        job,
        ai_client=FakeShadowPolicyAI("follow_current_story"),
    )
    decision = await test_db.get(
        MemoirInterviewPolicyDecision,
        result.result_json["decision_id"],
    )
    assert decision is not None
    decision_id = decision.id
    evaluation = await interview_policy.record_human_evaluation(
        test_db,
        decision=decision,
        reviewer_user_id=None,
        expected_action=MemoirInterviewAction.CONTINUE_LISTENING,
        failure_codes=(
            MemoirInterviewPolicyFailureCode.INTERRUPTED_USER,
            MemoirInterviewPolicyFailureCode.REPEATED_QUESTION,
        ),
        human_rating=-1,
    )
    await test_db.commit()

    persisted = await test_db.get(MemoirInterviewPolicyEvaluation, evaluation.id)
    assert persisted is not None
    assert persisted.expected_action == "continue_listening"
    assert persisted.failure_codes_json == [
        "interrupted_user",
        "repeated_question",
    ]
    await test_db.refresh(decision)
    assert decision.human_rating == -1
    persisted.expected_action = "follow_current_story"
    with pytest.raises(IntegrityError):
        await test_db.commit()
    await test_db.rollback()

    invalid = MemoirInterviewPolicyEvaluation(
        decision_id=decision_id,
        failure_codes_json=["用户不想被记录的原话"],
    )
    test_db.add(invalid)
    with pytest.raises(IntegrityError):
        await test_db.commit()
    await test_db.rollback()


async def test_shadow_job_is_replayable_without_duplicate_decision(test_db):
    _, session, generated_message, _ = await _free_narration_turn(test_db)
    job = await _scheduled_shadow_job(
        test_db,
        session=session,
        generated_message=generated_message,
    )
    first = await run_shadow_policy_evaluation_job(
        test_db,
        job,
        ai_client=FakeShadowPolicyAI("continue_listening"),
    )
    await test_db.commit()
    loaded_job = await test_db.scalar(
        select(MemoirEditingJob)
        .where(MemoirEditingJob.id == job.id)
        .options(selectinload(MemoirEditingJob.project))
    )
    assert loaded_job is not None
    second = await run_shadow_policy_evaluation_job(
        test_db,
        loaded_job,
        ai_client=FakeShadowPolicyAI("pause_or_end"),
    )

    decisions = (
        await test_db.scalars(select(MemoirInterviewPolicyDecision))
    ).all()
    assert second.result_json["reused"] is True
    assert second.result_json["decision_id"] == first.result_json["decision_id"]
    assert len(decisions) == 1
