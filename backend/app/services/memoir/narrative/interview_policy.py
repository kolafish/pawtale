from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.interview_policy import (
    InterviewBrief,
    brief_context_snapshot,
)
from app.domain.memoir.narrative_types import (
    MemoirConversationMomentum,
    MemoirFollowupTaskClaimRole,
    MemoirInterviewAction,
    MemoirInterviewBriefSourceKind,
    MemoirInterviewPolicyFailureCode,
    MemoirInterviewPolicyExecutionMode,
    MemoirInterviewResponseOutcome,
    MemoirInterviewStoryState,
)
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.memoir_narrative import (
    MemoirFollowupTask,
    MemoirInterviewPolicyDecision,
    MemoirInterviewPolicyEvaluation,
    MemoirUtteranceInterviewAssignment,
)


DEFAULT_POLICY_VERSION = "interview-policy-1"


def open_narration_brief(
    *,
    current_story_state: MemoirInterviewStoryState,
    conversation_momentum: MemoirConversationMomentum,
    existing_material_refs: tuple[str, ...] = (),
    unresolved_gap_ids: tuple[str, ...] = (),
    known_claim_ids: tuple[str, ...] = (),
    do_not_repeat_claim_ids: tuple[str, ...] = (),
    user_preferences: Mapping[str, object] | None = None,
    safety_constraints: tuple[str, ...] = (),
    policy_version: str = DEFAULT_POLICY_VERSION,
) -> InterviewBrief:
    return InterviewBrief(
        source_kind=MemoirInterviewBriefSourceKind.OPEN_NARRATION,
        target_id=None,
        purpose="顺着讲述者当前愿意分享的内容继续访谈",
        opening_question="您想从哪里继续讲起？",
        candidate_actions=(),
        current_story_state=current_story_state,
        conversation_momentum=conversation_momentum,
        user_preferences=user_preferences or {},
        safety_constraints=safety_constraints,
        existing_material=existing_material_refs,
        unresolved_gaps=unresolved_gap_ids,
        known_claims=known_claim_ids,
        do_not_repeat_claims=do_not_repeat_claim_ids,
        expected_evidence_hint="",
        completion_policy="",
        template_version=None,
        policy_version=policy_version,
    ).with_candidates()


def followup_task_brief(
    *,
    task: MemoirFollowupTask,
    current_story_state: MemoirInterviewStoryState,
    conversation_momentum: MemoirConversationMomentum,
    recent_material_refs: tuple[str, ...] = (),
    user_preferences: Mapping[str, object] | None = None,
    safety_constraints: tuple[str, ...] = (),
    policy_version: str = DEFAULT_POLICY_VERSION,
) -> InterviewBrief:
    if "claims" not in task.__dict__:
        raise ValueError("构建访谈 brief 前必须预加载补访任务的 Claim 关系")
    claims_by_role: dict[str, list[str]] = {}
    for relation in task.claims:
        claims_by_role.setdefault(relation.context_role, []).append(relation.claim_id)
    return InterviewBrief(
        source_kind=MemoirInterviewBriefSourceKind.BOOK_COMPLETION,
        target_id=task.id,
        purpose=task.purpose,
        opening_question=task.question,
        candidate_actions=(),
        current_story_state=current_story_state,
        conversation_momentum=conversation_momentum,
        user_preferences=user_preferences or {},
        safety_constraints=safety_constraints,
        existing_material=tuple(
            sorted(
                set(claims_by_role.get(MemoirFollowupTaskClaimRole.KNOWN.value, ()))
                | set(recent_material_refs)
            )
        ),
        unresolved_gaps=(task.gap_id,),
        known_claims=tuple(
            sorted(claims_by_role.get(MemoirFollowupTaskClaimRole.KNOWN.value, ()))
        ),
        do_not_repeat_claims=tuple(
            sorted(
                claims_by_role.get(
                    MemoirFollowupTaskClaimRole.DO_NOT_REPEAT.value,
                    (),
                )
            )
        ),
        expected_evidence_hint=task.target_slot,
        completion_policy=task.answer_use,
        template_version=None,
        policy_version=policy_version,
    ).with_candidates()


def _candidate_actions_payload(brief: InterviewBrief) -> list[dict[str, str]]:
    return [
        {"action": candidate.action.value, "risk": candidate.risk.value}
        for candidate in brief.candidate_actions
    ]


async def create_policy_decision(
    db: AsyncSession,
    *,
    session_id: str,
    trigger_message_id: str | None,
    brief: InterviewBrief,
    selected_action: MemoirInterviewAction,
    reason_code: str,
    reason_summary: str,
    execution_mode: MemoirInterviewPolicyExecutionMode,
    model_provider: str = "",
    model_name: str = "",
) -> MemoirInterviewPolicyDecision:
    session = await db.get(MemoirVoiceSession, session_id)
    if session is None:
        raise ValueError("访谈会话不存在")
    if trigger_message_id:
        trigger = await db.get(MemoirChatMessage, trigger_message_id)
        if trigger is None or trigger.voice_session_id != session.id:
            raise ValueError("策略触发消息不属于当前访谈会话")
    candidate_actions = {
        candidate.action for candidate in brief.candidate_actions
    }
    if selected_action not in candidate_actions:
        raise ValueError("策略动作不属于本轮候选集合")
    normalized_reason_code = reason_code.strip()
    if not normalized_reason_code:
        raise ValueError("策略原因代码不能为空")
    normalized_reason_summary = reason_summary.strip()
    if len(normalized_reason_summary) > 240:
        raise ValueError("策略原因摘要不能超过 240 个字符")

    snapshot = brief_context_snapshot(brief)
    decision = MemoirInterviewPolicyDecision(
        session_id=session.id,
        trigger_message_id=trigger_message_id,
        brief_fingerprint=str(snapshot["brief_fingerprint"]),
        state_snapshot_json=snapshot,
        candidate_actions_json=_candidate_actions_payload(brief),
        selected_action=selected_action.value,
        reason_code=normalized_reason_code,
        reason_summary=normalized_reason_summary,
        policy_version=brief.policy_version,
        model_provider=model_provider.strip(),
        model_name=model_name.strip(),
        execution_mode=execution_mode.value,
    )
    db.add(decision)
    await db.flush()
    return decision


async def attach_generated_message(
    db: AsyncSession,
    *,
    decision: MemoirInterviewPolicyDecision,
    message_id: str,
) -> None:
    message = await db.get(MemoirChatMessage, message_id)
    if message is None or message.voice_session_id != decision.session_id:
        raise ValueError("策略生成消息不属于当前访谈会话")
    if decision.generated_message_id and decision.generated_message_id != message.id:
        raise ValueError("策略决策已经关联了另一条生成消息")
    decision.generated_message_id = message.id
    db.add(decision)
    await db.flush()


async def record_response_outcome(
    db: AsyncSession,
    *,
    decision: MemoirInterviewPolicyDecision,
    outcome: MemoirInterviewResponseOutcome,
    human_rating: int | None = None,
) -> None:
    if human_rating is not None and human_rating not in {-1, 0, 1}:
        raise ValueError("人工评分必须是 -1、0 或 1")
    if (
        human_rating is not None
        and decision.human_rating is not None
        and decision.human_rating != human_rating
    ):
        raise ValueError("策略人工评分已经记录，不能覆盖")
    if (
        decision.user_response_outcome is not None
        and decision.user_response_outcome != outcome.value
    ):
        raise ValueError("策略响应结果已经记录，不能覆盖")
    changed = False
    if decision.user_response_outcome is None:
        decision.user_response_outcome = outcome.value
        decision.evaluated_at = datetime.now(UTC).replace(tzinfo=None)
        changed = True
    if human_rating is not None and decision.human_rating is None:
        decision.human_rating = human_rating
        changed = True
    if not changed:
        return
    db.add(decision)
    await db.flush()


async def record_human_evaluation(
    db: AsyncSession,
    *,
    decision: MemoirInterviewPolicyDecision,
    reviewer_user_id: str | None,
    expected_action: MemoirInterviewAction | None = None,
    failure_codes: tuple[MemoirInterviewPolicyFailureCode, ...] = (),
    human_rating: int | None = None,
) -> MemoirInterviewPolicyEvaluation:
    if expected_action is None and not failure_codes and human_rating is None:
        raise ValueError("人工评估至少需要动作标签、失败标签或评分")
    if human_rating is not None and human_rating not in {-1, 0, 1}:
        raise ValueError("人工评分必须是 -1、0 或 1")
    candidate_actions = {
        str(item.get("action"))
        for item in decision.candidate_actions_json
        if isinstance(item, dict) and item.get("action")
    }
    if expected_action is not None and expected_action.value not in candidate_actions:
        raise ValueError("人工动作标签不属于本轮候选集合")
    normalized_failure_codes = sorted(
        {failure_code.value for failure_code in failure_codes}
    )
    evaluation = MemoirInterviewPolicyEvaluation(
        decision_id=decision.id,
        reviewer_user_id=reviewer_user_id,
        expected_action=expected_action.value if expected_action else None,
        failure_codes_json=normalized_failure_codes,
    )
    if human_rating is not None and decision.human_rating is None:
        decision.human_rating = human_rating
        decision.evaluated_at = decision.evaluated_at or datetime.now(UTC).replace(
            tzinfo=None
        )
        db.add(decision)
    db.add(evaluation)
    await db.flush()
    return evaluation


async def _policy_decision_for_parent_message(
    db: AsyncSession,
    *,
    utterance: MemoirVoiceUtterance,
) -> MemoirInterviewPolicyDecision | None:
    if not utterance.chat_message_id:
        return None
    message = await db.get(MemoirChatMessage, utterance.chat_message_id)
    if message is None or not message.parent_message_id:
        return None
    return await db.scalar(
        select(MemoirInterviewPolicyDecision).where(
            MemoirInterviewPolicyDecision.session_id == utterance.voice_session_id,
            MemoirInterviewPolicyDecision.generated_message_id
            == message.parent_message_id,
            MemoirInterviewPolicyDecision.execution_mode
            == MemoirInterviewPolicyExecutionMode.ACTIVE.value,
        )
    )


async def assign_utterance(
    db: AsyncSession,
    *,
    utterance: MemoirVoiceUtterance,
    followup_task: MemoirFollowupTask | None,
    policy_decision: MemoirInterviewPolicyDecision | None = None,
) -> MemoirUtteranceInterviewAssignment:
    session = await db.get(MemoirVoiceSession, utterance.voice_session_id)
    if session is None:
        raise ValueError("讲述记录对应的访谈会话不存在")
    if followup_task is not None and followup_task.project_id != session.project_id:
        raise ValueError("补访任务不属于讲述记录所在项目")
    if policy_decision is None:
        policy_decision = await _policy_decision_for_parent_message(
            db,
            utterance=utterance,
        )
    if policy_decision is not None:
        if policy_decision.session_id != session.id:
            raise ValueError("策略决策不属于讲述记录所在会话")
        if (
            policy_decision.execution_mode
            != MemoirInterviewPolicyExecutionMode.ACTIVE.value
        ):
            raise ValueError("只有实际执行的策略决策才能归因用户回答")
        snapshot_task_id = (
            policy_decision.state_snapshot_json or {}
        ).get("followup_task_id")
        expected_task_id = followup_task.id if followup_task else None
        if snapshot_task_id != expected_task_id:
            raise ValueError("策略决策与讲述记录的补访目标不一致")

    existing = await db.scalar(
        select(MemoirUtteranceInterviewAssignment).where(
            MemoirUtteranceInterviewAssignment.utterance_id == utterance.id,
        )
    )
    if existing is not None:
        if (
            existing.followup_task_id
            != (followup_task.id if followup_task else None)
            or existing.policy_decision_id
            != (policy_decision.id if policy_decision else None)
        ):
            raise ValueError("讲述记录已经归属另一访谈目标")
        return existing

    assignment = MemoirUtteranceInterviewAssignment(
        utterance_id=utterance.id,
        followup_task_id=followup_task.id if followup_task else None,
        policy_decision_id=policy_decision.id if policy_decision else None,
    )
    db.add(assignment)
    await db.flush()
    return assignment


async def answer_belongs_to_creation_assignment(
    db: AsyncSession,
    *,
    utterance_id: str,
    followup_task_id: str,
) -> bool:
    return (
        await db.scalar(
            select(MemoirUtteranceInterviewAssignment.id).where(
                MemoirUtteranceInterviewAssignment.utterance_id == utterance_id,
                MemoirUtteranceInterviewAssignment.followup_task_id
                == followup_task_id,
            )
        )
        is not None
    )
