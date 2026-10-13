from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.framework_templates import (
    FrameworkTemplateItemDefinition,
)
from app.domain.memoir.interview_policy import (
    InterviewBrief,
    brief_context_snapshot,
)
from app.domain.memoir.narrative_types import (
    MemoirConversationMomentum,
    MemoirInterviewAction,
    MemoirInterviewBriefSourceKind,
    MemoirInterviewPolicyFailureCode,
    MemoirInterviewPolicyExecutionMode,
    MemoirInterviewResponseOutcome,
    MemoirInterviewStoryState,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirCompletionIssue,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirFrameworkItem,
    MemoirInterviewPolicyDecision,
    MemoirInterviewPolicyEvaluation,
    MemoirProjectFramework,
)


DEFAULT_POLICY_VERSION = "interview-policy-1"


def open_narration_brief(
    *,
    current_story_state: MemoirInterviewStoryState,
    conversation_momentum: MemoirConversationMomentum,
    existing_material_refs: tuple[str, ...] = (),
    unresolved_issue_ids: tuple[str, ...] = (),
    candidate_topic_ids: tuple[str, ...] = (),
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
        unresolved_issue_ids=unresolved_issue_ids,
        candidate_topic_ids=candidate_topic_ids,
        answer_hint="",
        completion_channel="",
        template_version=None,
        policy_version=policy_version,
    ).with_candidates()


def completion_issue_brief(
    *,
    issue: MemoirCompletionIssue,
    current_story_state: MemoirInterviewStoryState,
    conversation_momentum: MemoirConversationMomentum,
    recent_material_refs: tuple[str, ...] = (),
    related_material_refs: tuple[str, ...] | None = None,
    user_preferences: Mapping[str, object] | None = None,
    safety_constraints: tuple[str, ...] = (),
    policy_version: str = DEFAULT_POLICY_VERSION,
) -> InterviewBrief:
    return InterviewBrief(
        source_kind=MemoirInterviewBriefSourceKind.BOOK_COMPLETION,
        target_id=issue.id,
        purpose=issue.reason,
        opening_question=issue.question,
        candidate_actions=(),
        current_story_state=current_story_state,
        conversation_momentum=conversation_momentum,
        user_preferences=user_preferences or {},
        safety_constraints=safety_constraints,
        existing_material=tuple(
            sorted(
                set(
                    (
                        issue.related_material_ids_json
                        if issue.scope == "chapter"
                        else []
                    )
                    if related_material_refs is None
                    else related_material_refs
                )
                | set(recent_material_refs)
            )
        ),
        unresolved_issue_ids=(issue.id,),
        candidate_topic_ids=(),
        answer_hint=issue.issue_type,
        completion_channel=issue.channel,
        template_version=None,
        policy_version=policy_version,
    ).with_candidates()


def framework_item_brief(
    *,
    item: MemoirFrameworkItem,
    definition: FrameworkTemplateItemDefinition,
    template_version: str,
    current_story_state: MemoirInterviewStoryState,
    conversation_momentum: MemoirConversationMomentum,
    existing_material_refs: tuple[str, ...] = (),
    user_preferences: Mapping[str, object] | None = None,
    safety_constraints: tuple[str, ...] = (),
    policy_version: str = DEFAULT_POLICY_VERSION,
) -> InterviewBrief:
    return InterviewBrief(
        source_kind=MemoirInterviewBriefSourceKind.FRAMEWORK,
        target_id=item.id,
        purpose=definition.intent,
        opening_question=(
            f"如果从“{definition.title}”聊起，您最先想到的是哪一段？"
        ),
        candidate_actions=(),
        current_story_state=current_story_state,
        conversation_momentum=conversation_momentum,
        user_preferences=user_preferences or {},
        safety_constraints=safety_constraints,
        existing_material=tuple(sorted(set(existing_material_refs))),
        unresolved_issue_ids=(),
        candidate_topic_ids=(),
        answer_hint="",
        completion_channel="",
        template_version=template_version,
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


async def policy_decision_for_utterance(
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
    completion_issue: MemoirCompletionIssue | None,
    framework_item: MemoirFrameworkItem | None = None,
    policy_decision: MemoirInterviewPolicyDecision | None = None,
) -> MemoirVoiceUtterance:
    session = await db.get(MemoirVoiceSession, utterance.voice_session_id)
    if session is None:
        raise ValueError("讲述记录对应的访谈会话不存在")
    if completion_issue is not None and framework_item is not None:
        raise ValueError("讲述记录只能归属一个访谈目标")
    if policy_decision is None:
        policy_decision = await policy_decision_for_utterance(
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
        snapshot_issue_id = (
            policy_decision.state_snapshot_json or {}
        ).get("completion_issue_id")
        snapshot_framework_item_id = (
            policy_decision.state_snapshot_json or {}
        ).get("framework_item_id")
        if snapshot_issue_id and snapshot_framework_item_id:
            raise ValueError("策略决策同时包含多个访谈目标")
        if completion_issue is None and framework_item is None:
            if snapshot_issue_id:
                completion_issue = await db.get(
                    MemoirCompletionIssue,
                    snapshot_issue_id,
                )
                if completion_issue is None:
                    raise ValueError("策略决策引用的补全问题不存在")
            elif snapshot_framework_item_id:
                framework_item = await db.get(
                    MemoirFrameworkItem,
                    snapshot_framework_item_id,
                )
                if framework_item is None:
                    raise ValueError("策略决策引用的话题方向不存在")
        expected_issue_id = completion_issue.id if completion_issue else None
        expected_framework_item_id = framework_item.id if framework_item else None
        if (
            snapshot_issue_id != expected_issue_id
            or snapshot_framework_item_id != expected_framework_item_id
        ):
            raise ValueError("策略决策与讲述记录的补访目标不一致")

    if (
        completion_issue is not None
        and completion_issue.project_id != session.project_id
    ):
        raise ValueError("补全问题不属于讲述记录所在项目")
    if framework_item is not None:
        framework_project_id = await db.scalar(
            select(MemoirProjectFramework.project_id)
            .join(
                MemoirFrameworkItem,
                MemoirFrameworkItem.project_framework_id
                == MemoirProjectFramework.id,
            )
            .where(MemoirFrameworkItem.id == framework_item.id)
        )
        if framework_project_id != session.project_id:
            raise ValueError("话题地图方向不属于讲述记录所在项目")

    expected_framework_id = framework_item.id if framework_item else None
    expected_issue_id = completion_issue.id if completion_issue else None
    expected_policy_id = policy_decision.id if policy_decision else None
    if any(
        (
            utterance.framework_item_id,
            utterance.completion_issue_id,
            utterance.policy_decision_id,
        )
    ):
        if (
            utterance.framework_item_id != expected_framework_id
            or utterance.completion_issue_id != expected_issue_id
            or utterance.policy_decision_id != expected_policy_id
        ):
            raise ValueError("讲述记录已经归属另一访谈目标")
        return utterance

    utterance.framework_item_id = expected_framework_id
    utterance.completion_issue_id = expected_issue_id
    utterance.policy_decision_id = expected_policy_id
    db.add(utterance)
    await db.flush()
    return utterance
