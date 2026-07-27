from __future__ import annotations

from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.domain.memoir.narrative_types import (
    MemoirInterviewAction,
    MemoirInterviewPolicyFailureCode,
    MemoirInterviewPolicyExecutionMode,
)
from app.domain.memoir.types import MemoirEditingJobType
from app.models.memoir import (
    MemoirChatMessage,
    MemoirCompletionIssue,
    MemoirEditingJob,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirInterviewPolicyDecision,
    MemoirInterviewPolicyEvaluation,
)
from app.models.projects import Project
from app.services.memoir.narrative import frameworks, interview_policy
from app.services.memoir.narrative.interview_policy_outcomes import (
    PolicyOutcomeSelectionError,
    run_policy_outcome_evaluation_job,
)
from app.services.memoir.narrative.interview_policy_runtime import (
    ACTIVE_POLICY_VERSION,
    SHADOW_POLICY_VERSION,
    InterviewPolicySelectionError,
    _active_rollout_selected,
    active_reply_instruction,
    anchored_opening_reply_instruction,
    build_current_policy_context,
    persist_policy_turn_decision,
    prepare_active_policy_turn,
    run_shadow_policy_evaluation_job,
    schedule_shadow_policy_evaluation,
)
from app.services.memoir.voice_final_persistence import _assign_interview_target


class FakePolicyAI:
    provider_name = "fake-policy"
    model_name = "fake-policy-model"

    def __init__(
        self,
        selected_action: str,
        *,
        framework_item_id: str | None = None,
    ) -> None:
        self.selected_action = selected_action
        self.framework_item_id = framework_item_id
        self.calls: list[dict[str, object]] = []

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        framework_item = (
            f',"framework_item_id":"{self.framework_item_id}"'
            if self.framework_item_id
            else ""
        )
        return (
            f'{{"selected_action":"{self.selected_action}"'
            f"{framework_item}}}"
        )


class FakePolicyOutcomeAI:
    provider_name = "fake-outcome"
    model_name = "fake-outcome-model"

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    async def _call_llm(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "kwargs": kwargs,
            }
        )
        return f'{{"outcome":"{self.outcome}"}}'


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
    completion_issue_id: str | None = None,
) -> MemoirEditingJob:
    job = await schedule_shadow_policy_evaluation(
        test_db,
        session=session,
        generated_message=generated_message,
        completion_issue_id=completion_issue_id,
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

    ai_client = FakePolicyAI("follow_current_story")
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


def test_active_policy_rollout_is_stable_and_respects_boundaries():
    session_id = "stable-session"

    assert not _active_rollout_selected(
        session_id=session_id,
        rollout_percent=0,
    )
    assert _active_rollout_selected(
        session_id=session_id,
        rollout_percent=100,
    )
    first = _active_rollout_selected(
        session_id=session_id,
        rollout_percent=37,
    )
    second = _active_rollout_selected(
        session_id=session_id,
        rollout_percent=37,
    )
    assert first is second


async def test_active_policy_executes_low_risk_action_and_suggests_medium_action(
    test_db,
):
    _, session, _, _ = await _free_narration_turn(test_db)
    await test_db.refresh(session, ["messages"])

    active = await prepare_active_policy_turn(
        test_db,
        session=session,
        completion_issue_id=None,
        requested_ai_model=None,
        ai_client_factory=lambda model=None: FakePolicyAI(
            "follow_current_story"
        ),
        enabled=True,
        rollout_percent=100,
    )
    suggest = await prepare_active_policy_turn(
        test_db,
        session=session,
        completion_issue_id=None,
        requested_ai_model=None,
        ai_client_factory=lambda model=None: FakePolicyAI(
            "return_to_prior_story"
        ),
        enabled=True,
        rollout_percent=100,
    )

    assert active is not None
    assert active.context.brief.policy_version == ACTIVE_POLICY_VERSION
    assert active.execution_mode == MemoirInterviewPolicyExecutionMode.ACTIVE
    assert "顺着当前故事追问" in active_reply_instruction(active)
    assert suggest is not None
    assert suggest.execution_mode == MemoirInterviewPolicyExecutionMode.SUGGEST
    assert active_reply_instruction(suggest) == ""


async def test_guided_policy_offers_controlled_topic_and_freezes_acceptance(
    test_db,
):
    project = Project(type="memoir", title="话题策略")
    test_db.add(project)
    await test_db.flush()
    project_framework = await frameworks.initialize_project_framework(
        test_db,
        project_id=project.id,
    )
    session = MemoirVoiceSession(
        project_id=project.id,
        guidance_mode="guided_interview",
        session_goal="open_narration",
    )
    test_db.add(session)
    await test_db.flush()
    start = int.from_bytes(
        sha256(f"{session.id}:opening".encode("utf-8")).digest()[:4],
        "big",
    ) % len(project_framework.items)
    selected_item = project_framework.items[start]
    await test_db.commit()
    await test_db.refresh(session, ["messages"])
    fake_ai = FakePolicyAI(
        "offer_new_topic",
        framework_item_id="F1",
    )

    policy_turn = await prepare_active_policy_turn(
        test_db,
        session=session,
        completion_issue_id=None,
        requested_ai_model=None,
        ai_client_factory=lambda model=None: fake_ai,
        enabled=True,
        rollout_percent=100,
    )

    assert policy_turn is not None
    assert policy_turn.execution_mode == MemoirInterviewPolicyExecutionMode.ACTIVE
    assert policy_turn.context.brief.source_kind.value == "framework"
    assert policy_turn.context.brief.target_id == selected_item.id
    assert selected_item.id in str(
        policy_turn.context.brief.target_id
    )
    assert selected_item.id not in fake_ai.calls[0]["user_prompt"]
    assert '"framework_item_id": "F1"' in fake_ai.calls[0]["user_prompt"]
    assert "可拒绝的问题" in active_reply_instruction(policy_turn)

    generated = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="assistant",
        content_text=policy_turn.context.brief.opening_question,
        turn_index=0,
    )
    test_db.add(generated)
    await test_db.flush()
    decision = await persist_policy_turn_decision(
        test_db,
        session=session,
        generated_message=generated,
        policy_turn=policy_turn,
    )
    response_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="user",
        content_text="我先讲一件小时候在河边发生的事。",
        parent_message_id=generated.id,
        turn_index=1,
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

    await _assign_interview_target(
        test_db,
        voice_session=session,
        utterance=utterance,
    )
    await test_db.commit()

    await test_db.refresh(utterance)
    assert utterance.framework_item_id == selected_item.id
    assert utterance.policy_decision_id == decision.id
    await test_db.refresh(session, ["messages"])
    assert session.anchor_framework_item_id == selected_item.id

    continued = await prepare_active_policy_turn(
        test_db,
        session=session,
        completion_issue_id=None,
        requested_ai_model=None,
        ai_client_factory=lambda model=None: FakePolicyAI(
            "follow_current_story"
        ),
        enabled=True,
        rollout_percent=100,
    )
    assert continued is not None
    assert continued.context.brief.source_kind.value == "open_narration"
    assert MemoirInterviewAction.OFFER_NEW_TOPIC not in {
        candidate.action
        for candidate in continued.context.brief.candidate_actions
    }
    assert continued.context.brief.target_id is None

    switch_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="user",
        content_text="这一段先讲到这里，换个话题吧。",
        turn_index=2,
    )
    test_db.add(switch_message)
    await test_db.commit()
    await test_db.refresh(session, ["messages"])
    switch_context = await build_current_policy_context(
        test_db,
        session=session,
        completion_issue_id=None,
        policy_version=ACTIVE_POLICY_VERSION,
    )
    assert switch_context is not None
    assert switch_context.topic_candidates
    assert selected_item.id not in {
        candidate.item_id for candidate in switch_context.topic_candidates
    }
    assert MemoirInterviewAction.OFFER_NEW_TOPIC in {
        candidate.action
        for candidate in switch_context.brief.candidate_actions
    }

    anchored_session = MemoirVoiceSession(
        project_id=project.id,
        guidance_mode="guided_interview",
        session_goal="open_narration",
        anchor_framework_item_id=selected_item.id,
    )
    test_db.add(anchored_session)
    await test_db.commit()
    await test_db.refresh(anchored_session, ["messages"])
    fallback_instruction = await anchored_opening_reply_instruction(
        test_db,
        session=anchored_session,
    )
    selected_definition = next(
        definition
        for definition in frameworks.template_for_framework(
            project_framework
        ).items
        if definition.item_key == selected_item.item_key
    )
    assert selected_definition.title in fallback_instruction
    assert "必须完成" in fallback_instruction


async def test_active_policy_only_executes_pause_when_safety_makes_it_mandatory(
    test_db,
):
    _, session, _, _ = await _free_narration_turn(
        test_db,
        user_text="这件事我不想再说了，先到这里吧。",
    )
    await test_db.refresh(session, ["messages"])

    policy_turn = await prepare_active_policy_turn(
        test_db,
        session=session,
        completion_issue_id=None,
        requested_ai_model=None,
        ai_client_factory=lambda model=None: FakePolicyAI("pause_or_end"),
        enabled=True,
        rollout_percent=100,
    )

    assert policy_turn is not None
    assert policy_turn.execution_mode == MemoirInterviewPolicyExecutionMode.ACTIVE
    assert [item.action for item in policy_turn.context.brief.candidate_actions] == [
        MemoirInterviewAction.PAUSE_OR_END
    ]
    assert "自然收尾" in active_reply_instruction(policy_turn)


def test_active_reply_instruction_is_empty_without_an_active_turn():
    assert active_reply_instruction(None) == ""


async def test_active_policy_response_outcome_is_scheduled_and_recorded(
    test_db,
    monkeypatch,
):
    project, session, generated_message, _ = await _free_narration_turn(test_db)
    project.metadata_json = {"ai_model": "deepseek-v4-pro"}
    await test_db.refresh(session, ["messages"])
    policy_turn = await prepare_active_policy_turn(
        test_db,
        session=session,
        completion_issue_id=None,
        requested_ai_model=None,
        ai_client_factory=lambda model=None: FakePolicyAI(
            "follow_current_story"
        ),
        enabled=True,
        rollout_percent=100,
    )
    assert policy_turn is not None
    decision = await persist_policy_turn_decision(
        test_db,
        session=session,
        generated_message=generated_message,
        policy_turn=policy_turn,
    )
    first_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="user",
        content_text="后来我们又沿着河走了很远。",
        parent_message_id=generated_message.id,
        turn_index=2,
    )
    test_db.add(first_message)
    await test_db.flush()
    first_utterance = MemoirVoiceUtterance(
        voice_session_id=session.id,
        chat_message_id=first_message.id,
        transcript_raw=first_message.content_text,
    )
    test_db.add(first_utterance)
    await test_db.flush()
    await _assign_interview_target(
        test_db,
        voice_session=session,
        utterance=first_utterance,
    )
    second_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="user",
        content_text="走到天黑才回家。",
        parent_message_id=generated_message.id,
        turn_index=3,
    )
    test_db.add(second_message)
    await test_db.flush()
    second_utterance = MemoirVoiceUtterance(
        voice_session_id=session.id,
        chat_message_id=second_message.id,
        transcript_raw=second_message.content_text,
    )
    test_db.add(second_utterance)
    await test_db.flush()
    await _assign_interview_target(
        test_db,
        voice_session=session,
        utterance=second_utterance,
    )
    await test_db.commit()

    jobs = (
        await test_db.scalars(
            select(MemoirEditingJob).where(
                MemoirEditingJob.job_type
                == MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_OUTCOME.value
            )
        )
    ).all()
    assert len(jobs) == 1
    assert "河" not in str(jobs[0].payload_json)

    outcome_ai = FakePolicyOutcomeAI("continued_story")
    requested_models: list[str | None] = []
    monkeypatch.setattr(
        "app.services.memoir.narrative.interview_policy_outcomes.get_ai_service",
        lambda model=None: requested_models.append(model) or outcome_ai,
    )
    result = await run_policy_outcome_evaluation_job(
        test_db,
        jobs[0],
    )
    await test_db.commit()
    await test_db.refresh(decision)

    assert result.result_json["outcome"] == "continued_story"
    assert requested_models == ["deepseek-v4-pro"]
    assert decision.user_response_outcome == "continued_story"
    assert first_message.content_text in str(outcome_ai.calls[0]["user_prompt"])
    assert second_message.content_text in str(outcome_ai.calls[0]["user_prompt"])
    assert first_message.content_text not in str(result.result_json)


async def test_policy_outcome_rejects_uncontrolled_value(test_db):
    project, session, generated_message, _ = await _free_narration_turn(test_db)
    await test_db.refresh(session, ["messages"])
    policy_turn = await prepare_active_policy_turn(
        test_db,
        session=session,
        completion_issue_id=None,
        requested_ai_model=None,
        ai_client_factory=lambda model=None: FakePolicyAI(
            "continue_listening"
        ),
        enabled=True,
        rollout_percent=100,
    )
    assert policy_turn is not None
    await persist_policy_turn_decision(
        test_db,
        session=session,
        generated_message=generated_message,
        policy_turn=policy_turn,
    )
    response_message = MemoirChatMessage(
        project_id=project.id,
        voice_session_id=session.id,
        role="user",
        content_text="我继续讲。",
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
    await _assign_interview_target(
        test_db,
        voice_session=session,
        utterance=response_utterance,
    )
    await test_db.commit()
    job = await test_db.scalar(
        select(MemoirEditingJob).where(
            MemoirEditingJob.job_type
            == MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_OUTCOME.value
        )
    )
    assert job is not None

    with pytest.raises(
        PolicyOutcomeSelectionError,
        match="policy_outcome_unknown_value",
    ):
        await run_policy_outcome_evaluation_job(
            test_db,
            job,
            ai_client=FakePolicyOutcomeAI("回答很好"),
        )


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
        ai_client=FakePolicyAI("continue_listening"),
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
        completion_issue=None,
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
        InterviewPolicySelectionError,
        match="interview_policy_action_outside_candidates",
    ):
        await run_shadow_policy_evaluation_job(
            test_db,
            job,
            ai_client=FakePolicyAI("offer_new_topic"),
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
        ai_client=FakePolicyAI("pause_or_end"),
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
    issue = MemoirCompletionIssue(
        project_id=project.id,
        scope="book",
        issue_type="relationship",
        channel="interview",
        question="康惠和您是什么关系？",
        reason="补清人物关系",
        priority="medium",
        fingerprint=sha256(b"completion-shadow").hexdigest(),
        related_material_ids_json=[],
        input_signature="completion-shadow",
        status="in_progress",
    )
    test_db.add(issue)
    await test_db.flush()
    session.current_completion_issue_id = issue.id
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
    other_project = Project(type="memoir", title="另一个人的回忆录")
    test_db.add(other_project)
    await test_db.flush()
    other_session = MemoirVoiceSession(project_id=other_project.id)
    test_db.add(other_session)
    await test_db.flush()
    other_utterance = MemoirVoiceUtterance(
        voice_session_id=other_session.id,
        transcript_raw="这是另一个项目不能泄漏的私密内容。",
    )
    test_db.add_all([utterance, generated_message, other_utterance])
    await test_db.flush()
    issue.related_material_ids_json = [other_utterance.id]
    await test_db.commit()

    job = await _scheduled_shadow_job(
        test_db,
        session=session,
        generated_message=generated_message,
        completion_issue_id=issue.id,
    )
    ai_client = FakePolicyAI("summarize_and_confirm")
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
    assert decision.state_snapshot_json["source_kind"] == "book_completion"
    assert decision.state_snapshot_json["completion_issue_id"] == issue.id
    assert decision.state_snapshot_json["unresolved_issue_ids"] == [issue.id]
    assert utterance.id in decision.state_snapshot_json["existing_material_refs"]
    assert (
        other_utterance.id
        not in decision.state_snapshot_json["existing_material_refs"]
    )
    assert "康惠是我的姐姐" not in str(decision.state_snapshot_json)
    assert "另一个项目不能泄漏" not in str(
        ai_client.calls[0]["user_prompt"]
    )


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
        ai_client=FakePolicyAI("follow_current_story"),
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
        ai_client=FakePolicyAI("continue_listening"),
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
        ai_client=FakePolicyAI("pause_or_end"),
    )

    decisions = (
        await test_db.scalars(select(MemoirInterviewPolicyDecision))
    ).all()
    assert second.result_json["reused"] is True
    assert second.result_json["decision_id"] == first.result_json["decision_id"]
    assert len(decisions) == 1
