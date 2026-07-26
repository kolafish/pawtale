from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.domain.memoir.interview_policy import InterviewBrief, brief_context_snapshot
from app.domain.memoir.narrative_types import (
    MemoirConversationMomentum,
    MemoirInterviewAction,
    MemoirInterviewPolicyExecutionMode,
    MemoirInterviewStoryState,
)
from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirGuidanceMode,
    MemoirInterviewType,
    MemoirSessionGoal,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirCompletionIssue,
    MemoirEditingJob,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirInterviewPolicyDecision,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import AITextTimeoutProfile, ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.editing_jobs import EditingJobResult, enqueue_editing_job
from app.services.memoir.narrative import (
    interview_policy,
    interview_topics,
)


SHADOW_POLICY_VERSION = "interview-policy-shadow-1"
SHADOW_PROMPT_VERSION = "memoir_interview_policy_shadow_v1"
ACTIVE_POLICY_VERSION = "interview-policy-active-1"
MAX_POLICY_CONTEXT_MESSAGES = 8
LOW_RISK_ACTIVE_ACTIONS = frozenset(
    {
        MemoirInterviewAction.CONTINUE_LISTENING,
        MemoirInterviewAction.FOLLOW_CURRENT_STORY,
        MemoirInterviewAction.SUMMARIZE_AND_CONFIRM,
    }
)

_ACTION_GUIDANCE: Mapping[MemoirInterviewAction, str] = {
    MemoirInterviewAction.CONTINUE_LISTENING: "少问或不问，让叙述者把正在说的内容继续说完",
    MemoirInterviewAction.FOLLOW_CURRENT_STORY: "顺着当前故事只追一个最自然的方向",
    MemoirInterviewAction.ASK_PERSON_RELATIONSHIP: "澄清当前故事中关键人物与叙述者或彼此的关系",
    MemoirInterviewAction.ASK_CAUSE_OR_RESULT: "只在因果或结果确实影响理解时询问",
    MemoirInterviewAction.SUMMARIZE_AND_CONFIRM: "用简短总结确认理解，不添加新事实",
    MemoirInterviewAction.RETURN_TO_PRIOR_STORY: "当前话题已停滞时，征得同意后回到旧故事",
    MemoirInterviewAction.OFFER_NEW_TOPIC: "提供一个可拒绝的新话题选择",
    MemoirInterviewAction.PAUSE_OR_END: "尊重暂停、结束或不愿继续讨论",
}
_REASON_SUMMARIES: Mapping[MemoirInterviewAction, str] = {
    MemoirInterviewAction.CONTINUE_LISTENING: "当前更适合给叙述者继续表达的空间。",
    MemoirInterviewAction.FOLLOW_CURRENT_STORY: "当前故事仍有自然延续的势能。",
    MemoirInterviewAction.ASK_PERSON_RELATIONSHIP: "人物关系是理解当前内容的关键缺口。",
    MemoirInterviewAction.ASK_CAUSE_OR_RESULT: "原因或结果是理解当前内容的关键缺口。",
    MemoirInterviewAction.SUMMARIZE_AND_CONFIRM: "当前更适合先确认理解再继续。",
    MemoirInterviewAction.RETURN_TO_PRIOR_STORY: "当前话题势能较低，可考虑回到已有故事。",
    MemoirInterviewAction.OFFER_NEW_TOPIC: "当前可以提供一个可拒绝的新方向。",
    MemoirInterviewAction.PAUSE_OR_END: "当前应优先尊重暂停或结束信号。",
}
_PAUSE_PHRASES = (
    "不想再说",
    "不想说了",
    "别再问",
    "暂停一下",
    "先到这里",
    "到此为止",
    "结束吧",
    "不聊了",
)
_PRIVACY_PHRASES = (
    "这是隐私",
    "这个不想说",
    "别写进书",
    "不要写进书",
    "不要记录",
)


class InterviewPolicySelectionError(ValueError):
    pass


@dataclass(frozen=True)
class InterviewPolicyContext:
    project_id: str
    brief: InterviewBrief
    messages: tuple[MemoirChatMessage, ...]
    completion_context: str
    trigger_message_id: str | None
    topic_candidates: tuple[
        interview_topics.FrameworkTopicCandidate,
        ...,
    ] = ()
    topic_offer_authorized: bool = False


@dataclass(frozen=True)
class InterviewPolicySelection:
    action: MemoirInterviewAction
    framework_item_id: str | None = None


@dataclass(frozen=True)
class ActivePolicyTurn:
    context: InterviewPolicyContext
    selected_action: MemoirInterviewAction
    execution_mode: MemoirInterviewPolicyExecutionMode
    model_provider: str
    model_name: str

    @property
    def applies_to_reply(self) -> bool:
        return self.execution_mode == MemoirInterviewPolicyExecutionMode.ACTIVE


def _eligible_session(session: MemoirVoiceSession) -> bool:
    return session.interview_type == MemoirInterviewType.AI_INTERVIEW.value and (
        session.session_goal == MemoirSessionGoal.BOOK_COMPLETION.value
        or (
            session.session_goal == MemoirSessionGoal.OPEN_NARRATION.value
            and session.guidance_mode
            in {
                MemoirGuidanceMode.FREE_NARRATION.value,
                MemoirGuidanceMode.GUIDED_INTERVIEW.value,
            }
        )
    )


def _ordered_messages_before(
    session: MemoirVoiceSession,
    generated_message: MemoirChatMessage,
) -> list[MemoirChatMessage]:
    return [
        message
        for message in sorted(
            session.messages,
            key=lambda item: (item.turn_index, item.created_at, item.id),
        )
        if message.id != generated_message.id
        and message.turn_index < generated_message.turn_index
        and message.content_status == MemoirChatContentStatus.FINAL.value
        and message.content_text.strip()
    ]


def _latest_user_message(
    messages: list[MemoirChatMessage],
) -> MemoirChatMessage | None:
    return next(
        (
            message
            for message in reversed(messages)
            if message.role == MemoirChatMessageRole.USER.value
        ),
        None,
    )


def _safety_constraints(latest_user_text: str) -> tuple[str, ...]:
    normalized = "".join(latest_user_text.split())
    constraints: list[str] = []
    if any(phrase in normalized for phrase in _PRIVACY_PHRASES):
        constraints.extend(("avoid_sensitive_topics", "privacy_restricted"))
    if any(phrase in normalized for phrase in _PAUSE_PHRASES):
        constraints.extend(("user_requested_pause", "pause_required"))
    return tuple(dict.fromkeys(constraints))


def _story_state(
    latest_user_message: MemoirChatMessage | None,
    *,
    completion_session: bool,
    safety_constraints: tuple[str, ...],
) -> MemoirInterviewStoryState:
    if latest_user_message is None:
        return MemoirInterviewStoryState.OPENING
    if "pause_required" in safety_constraints:
        return MemoirInterviewStoryState.CLOSING
    if completion_session:
        return MemoirInterviewStoryState.NEEDS_CONFIRMATION
    return MemoirInterviewStoryState.ACTIVE


def _conversation_momentum(
    latest_user_message: MemoirChatMessage | None,
    *,
    safety_constraints: tuple[str, ...],
) -> MemoirConversationMomentum:
    if latest_user_message is None or "pause_required" in safety_constraints:
        return MemoirConversationMomentum.LOW
    text_length = len("".join(latest_user_message.content_text.split()))
    if text_length < 12:
        return MemoirConversationMomentum.LOW
    if text_length >= 80:
        return MemoirConversationMomentum.HIGH
    return MemoirConversationMomentum.STEADY


def _user_preferences(session: MemoirVoiceSession) -> dict[str, object]:
    metadata = session.metadata_json if isinstance(session.metadata_json, dict) else {}
    nested = metadata.get("interview_preferences")
    source = nested if isinstance(nested, dict) else metadata
    return {
        key: source[key]
        for key in ("detail_level", "pace", "question_style", "topic_switching")
        if key in source
    }


async def _recent_material_state(
    db: AsyncSession,
    *,
    user_messages: list[MemoirChatMessage],
) -> tuple[str, ...]:
    message_ids = [message.id for message in user_messages[-MAX_POLICY_CONTEXT_MESSAGES:]]
    if not message_ids:
        return ()
    utterance_ids = tuple(
        sorted(
            set(
                (
                    await db.scalars(
                        select(MemoirVoiceUtterance.id).where(
                            MemoirVoiceUtterance.chat_message_id.in_(message_ids)
                        )
                    )
                ).all()
            )
        )
    )
    return utterance_ids


async def _completion_context(
    db: AsyncSession,
    issue: MemoirCompletionIssue,
) -> tuple[str, tuple[str, ...]]:
    material_ids = [
        str(value).strip()
        for value in issue.related_material_ids_json
        if str(value).strip()
    ]
    materials = (
        list(
            (
                await db.scalars(
                    select(MemoirVoiceUtterance)
                    .join(
                        MemoirVoiceSession,
                        MemoirVoiceSession.id
                        == MemoirVoiceUtterance.voice_session_id,
                    )
                    .where(
                        MemoirVoiceSession.project_id == issue.project_id,
                        MemoirVoiceUtterance.id.in_(material_ids),
                    )
                )
            ).all()
        )
        if material_ids
        else []
    )
    known = [
        (
            (material.transcript_edited or "").strip()
            or (material.transcript_raw or "").strip()
        )[:120]
        for material in materials
        if (
            (material.transcript_edited or "").strip()
            or (material.transcript_raw or "").strip()
        )
    ]
    lines = [
        f"补访目的：{issue.reason}",
        f"当前唯一问题：{issue.question}",
    ]
    if known:
        lines.append(f"已有相关素材（不要机械重复索取）：{'；'.join(known[:4])}")
    return "\n".join(lines), tuple(
        sorted(material.id for material in materials)
    )


async def _load_shadow_context(
    db: AsyncSession,
    *,
    session: MemoirVoiceSession,
    generated_message: MemoirChatMessage,
    completion_issue_id: str | None,
) -> InterviewPolicyContext | None:
    messages = _ordered_messages_before(session, generated_message)
    return await _build_policy_context(
        db,
        session=session,
        messages=messages,
        completion_issue_id=completion_issue_id,
        policy_version=SHADOW_POLICY_VERSION,
    )


async def _build_policy_context(
    db: AsyncSession,
    *,
    session: MemoirVoiceSession,
    messages: list[MemoirChatMessage],
    completion_issue_id: str | None,
    policy_version: str,
) -> InterviewPolicyContext | None:
    latest_user = _latest_user_message(messages)
    safety_constraints = _safety_constraints(
        latest_user.content_text if latest_user else ""
    )
    user_messages = [
        message
        for message in messages
        if message.role == MemoirChatMessageRole.USER.value
    ]
    material_refs = await _recent_material_state(
        db,
        user_messages=user_messages,
    )
    completion_session = (
        session.session_goal == MemoirSessionGoal.BOOK_COMPLETION.value
    )
    current_story_state = _story_state(
        latest_user,
        completion_session=completion_session,
        safety_constraints=safety_constraints,
    )
    conversation_momentum = _conversation_momentum(
        latest_user,
        safety_constraints=safety_constraints,
    )
    completion_context = ""
    topic_candidates: tuple[
        interview_topics.FrameworkTopicCandidate,
        ...,
    ] = ()
    if completion_session:
        if not completion_issue_id:
            return None
        issue = await db.get(
            MemoirCompletionIssue,
            completion_issue_id,
        )
        if issue is None or issue.project_id != session.project_id:
            return None
        completion_context, related_material_refs = (
            await _completion_context(db, issue)
        )
        brief = interview_policy.completion_issue_brief(
            issue=issue,
            current_story_state=current_story_state,
            conversation_momentum=conversation_momentum,
            recent_material_refs=material_refs,
            related_material_refs=related_material_refs,
            user_preferences=_user_preferences(session),
            safety_constraints=safety_constraints,
            policy_version=policy_version,
        )
    else:
        topic_candidates = await interview_topics.candidate_topics(
            db,
            session=session,
            latest_user_message=latest_user,
        )
        brief = interview_policy.open_narration_brief(
            current_story_state=current_story_state,
            conversation_momentum=conversation_momentum,
            existing_material_refs=material_refs,
            candidate_topic_ids=tuple(
                candidate.item_id for candidate in topic_candidates
            ),
            user_preferences=_user_preferences(session),
            safety_constraints=safety_constraints,
            policy_version=policy_version,
        )
    return InterviewPolicyContext(
        project_id=session.project_id,
        brief=brief,
        messages=tuple(messages[-MAX_POLICY_CONTEXT_MESSAGES:]),
        completion_context=completion_context,
        trigger_message_id=latest_user.id if latest_user else None,
        topic_candidates=topic_candidates,
        topic_offer_authorized=bool(topic_candidates),
    )


async def build_current_policy_context(
    db: AsyncSession,
    *,
    session: MemoirVoiceSession,
    completion_issue_id: str | None,
    policy_version: str,
) -> InterviewPolicyContext | None:
    messages = [
        message
        for message in sorted(
            session.messages,
            key=lambda item: (item.turn_index, item.created_at, item.id),
        )
        if message.content_status == MemoirChatContentStatus.FINAL.value
        and message.content_text.strip()
    ]
    return await _build_policy_context(
        db,
        session=session,
        messages=messages,
        completion_issue_id=completion_issue_id,
        policy_version=policy_version,
    )


def _conversation_prompt(messages: tuple[MemoirChatMessage, ...]) -> str:
    if not messages:
        return "暂无对话，这是本次访谈开场。"
    lines: list[str] = []
    for message in messages:
        role = (
            "叙述者"
            if message.role == MemoirChatMessageRole.USER.value
            else "访谈助手"
        )
        lines.append(f"{role}：{message.content_text.strip()}")
    return "\n".join(lines)


def _selection_prompt(context: InterviewPolicyContext) -> str:
    snapshot = brief_context_snapshot(context.brief)
    candidates = [candidate.action for candidate in context.brief.candidate_actions]
    candidate_lines = "\n".join(
        f"- {action.value}: {_ACTION_GUIDANCE[action]}"
        for action in candidates
    )
    completion_context = context.completion_context or "本轮不是成书补访。"
    topic_candidates = [
        {
            "framework_item_id": candidate.item_id,
            "title": candidate.title,
            "intent": candidate.intent,
        }
        for candidate in context.topic_candidates
    ]
    return f"""你只负责选择回忆录访谈的下一步策略动作，不生成回复正文。

必须遵守：
- 只能从 candidate_actions 选择一个动作。
- 优先避免打断仍在自然讲述的用户。
- 不要因为某件事被简略带过，就默认要求补齐事件的时间、地点、人物、起因和结果。
- 同时考虑章节/故事的背景交代、人物关系、因果、衔接和总结确认，不要只追事件细节。
- 用户表示暂停、不愿讨论或涉及隐私时，必须服从 safety_constraint_codes。
- 不得判断事实真假、隐私能否入书或覆盖用户决定。
- 仅当选择 offer_new_topic 时，从 topic_candidates 选择一个 framework_item_id。
- 话题候选只在用户选择强引导开场或明确请求方向时提供；不得以填满地图为目标。
- 只输出 JSON：{{"selected_action":"candidate action","framework_item_id":null}}。

brief（只含受控状态和对象引用）：
{json.dumps(snapshot, ensure_ascii=False, sort_keys=True)}

candidate_actions：
{candidate_lines}

topic_candidates（模板摘要，不是待完成清单）：
{json.dumps(topic_candidates, ensure_ascii=False, sort_keys=True)}

补访上下文：
{completion_context}

最近对话：
{_conversation_prompt(context.messages)}
"""


async def select_policy_action(
    *,
    context: InterviewPolicyContext,
    ai_client: AITextClient,
) -> InterviewPolicySelection:
    content = await ai_client._call_llm(
        (
            "你是回忆录访谈策略分类器。"
            "你只选择系统提供的受控动作，不生成问题，不修改事实或用户决定。"
        ),
        _selection_prompt(context),
        temperature=0.0,
        max_tokens=160,
        timeout=ai_text_timeout_seconds(AITextTimeoutProfile.REALTIME),
    )
    payload = extract_json_payload(str(content))
    if not isinstance(payload, dict):
        raise InterviewPolicySelectionError("interview_policy_invalid_json")
    raw_action = str(payload.get("selected_action") or "").strip()
    try:
        selected_action = MemoirInterviewAction(raw_action)
    except ValueError as exc:
        raise InterviewPolicySelectionError("interview_policy_unknown_action") from exc
    candidate_actions = {
        candidate.action for candidate in context.brief.candidate_actions
    }
    if selected_action not in candidate_actions:
        raise InterviewPolicySelectionError(
            "interview_policy_action_outside_candidates"
        )
    raw_framework_item_id = str(
        payload.get("framework_item_id") or ""
    ).strip() or None
    candidate_topic_ids = {
        candidate.item_id for candidate in context.topic_candidates
    }
    if selected_action == MemoirInterviewAction.OFFER_NEW_TOPIC:
        if (
            not raw_framework_item_id
            or raw_framework_item_id not in candidate_topic_ids
        ):
            raise InterviewPolicySelectionError(
                "interview_policy_topic_outside_candidates"
            )
    elif raw_framework_item_id is not None:
        raise InterviewPolicySelectionError(
            "interview_policy_unexpected_topic_selection"
        )
    return InterviewPolicySelection(
        action=selected_action,
        framework_item_id=raw_framework_item_id,
    )


async def _context_for_selection(
    db: AsyncSession,
    *,
    context: InterviewPolicyContext,
    selection: InterviewPolicySelection,
) -> InterviewPolicyContext:
    if selection.action != MemoirInterviewAction.OFFER_NEW_TOPIC:
        return context
    candidate = next(
        (
            item
            for item in context.topic_candidates
            if item.item_id == selection.framework_item_id
        ),
        None,
    )
    if candidate is None:
        raise InterviewPolicySelectionError(
            "interview_policy_topic_outside_candidates"
        )
    try:
        brief = await interview_topics.brief_for_candidate(
            db,
            project_id=context.project_id,
            base_brief=context.brief,
            candidate=candidate,
        )
    except interview_topics.InterviewTopicUnavailableError as exc:
        raise InterviewPolicySelectionError(str(exc)) from exc
    return replace(
        context,
        brief=replace(
            brief,
            candidate_actions=context.brief.candidate_actions,
            candidate_topic_ids=context.brief.candidate_topic_ids,
        ),
    )


def _active_rollout_selected(
    *,
    session_id: str,
    rollout_percent: int,
    policy_version: str = ACTIVE_POLICY_VERSION,
) -> bool:
    normalized_percent = max(0, min(100, rollout_percent))
    if normalized_percent <= 0:
        return False
    if normalized_percent >= 100:
        return True
    digest = hashlib.sha256(
        f"{policy_version}:{session_id}".encode("utf-8")
    ).digest()
    bucket = int.from_bytes(digest[:8], "big") % 100
    return bucket < normalized_percent


def _execution_mode_for_selection(
    context: InterviewPolicyContext,
    selected_action: MemoirInterviewAction,
) -> MemoirInterviewPolicyExecutionMode:
    if selected_action in LOW_RISK_ACTIVE_ACTIONS:
        return MemoirInterviewPolicyExecutionMode.ACTIVE
    if (
        selected_action == MemoirInterviewAction.OFFER_NEW_TOPIC
        and context.topic_offer_authorized
        and context.brief.target_id is not None
    ):
        return MemoirInterviewPolicyExecutionMode.ACTIVE
    if (
        selected_action == MemoirInterviewAction.PAUSE_OR_END
        and len(context.brief.candidate_actions) == 1
        and context.brief.candidate_actions[0].action
        == MemoirInterviewAction.PAUSE_OR_END
    ):
        return MemoirInterviewPolicyExecutionMode.ACTIVE
    return MemoirInterviewPolicyExecutionMode.SUGGEST


async def prepare_active_policy_turn(
    db: AsyncSession,
    *,
    session: MemoirVoiceSession,
    completion_issue_id: str | None,
    requested_ai_model: str | None,
    ai_client_factory=get_ai_service,
    enabled: bool | None = None,
    rollout_percent: int | None = None,
) -> ActivePolicyTurn | None:
    settings = get_settings()
    is_enabled = (
        settings.memoir_interview_policy_active_enabled
        if enabled is None
        else enabled
    )
    selected_percent = (
        settings.memoir_interview_policy_active_rollout_percent
        if rollout_percent is None
        else rollout_percent
    )
    if (
        not is_enabled
        or not _eligible_session(session)
        or not _active_rollout_selected(
            session_id=session.id,
            rollout_percent=selected_percent,
        )
    ):
        return None
    context = await build_current_policy_context(
        db,
        session=session,
        completion_issue_id=completion_issue_id,
        policy_version=ACTIVE_POLICY_VERSION,
    )
    if context is None:
        return None
    selected_model = (
        settings.memoir_interview_policy_active_model.strip()
        or (requested_ai_model or "").strip()
    )
    client = ai_client_factory(selected_model or None)
    selection = await select_policy_action(
        context=context,
        ai_client=client,
    )
    context = await _context_for_selection(
        db,
        context=context,
        selection=selection,
    )
    return ActivePolicyTurn(
        context=context,
        selected_action=selection.action,
        execution_mode=_execution_mode_for_selection(
            context,
            selection.action,
        ),
        model_provider=client.provider_name,
        model_name=client.model_name,
    )


def active_reply_instruction(policy_turn: ActivePolicyTurn | None) -> str:
    if policy_turn is None or not policy_turn.applies_to_reply:
        return ""
    instructions = {
        MemoirInterviewAction.CONTINUE_LISTENING: (
            "本轮策略是“继续倾听”：以简短承接和鼓励为主，不提出新的具体问题，"
            "不要换话题；可以用一句很轻的邀请让叙述者继续。"
        ),
        MemoirInterviewAction.FOLLOW_CURRENT_STORY: (
            "本轮策略是“顺着当前故事追问”：只能沿叙述者最新内容推进，"
            "一次只问一个自然问题，不切换到旧故事或新主题。"
        ),
        MemoirInterviewAction.SUMMARIZE_AND_CONFIRM: (
            "本轮策略是“总结确认”：只做简短、忠实的总结并请叙述者确认，"
            "不得添加新事实，不继续索取事件细节。"
        ),
        MemoirInterviewAction.PAUSE_OR_END: (
            "叙述者已明确要求暂停、结束或不愿讨论；本轮只尊重这一决定并自然收尾，"
            "不得继续提问或换一个话题追问。"
        ),
        MemoirInterviewAction.OFFER_NEW_TOPIC:
            topic_offer_reply_instruction(policy_turn.context.brief),
    }
    return instructions.get(policy_turn.selected_action, "")


def topic_offer_reply_instruction(brief: InterviewBrief) -> str:
    return (
        "本轮提供一个新方向，这是用户主动选择强引导或明确请求方向后的回应。"
        f"请自然地只提出这个可拒绝的问题：{brief.opening_question}"
        "不要列出多个话题，不要把它说成必须完成的内容；"
        "用户若不接这个方向，后续顺着用户讲述。"
    )


async def anchored_opening_reply_instruction(
    db: AsyncSession,
    *,
    session: MemoirVoiceSession,
) -> str:
    if (
        not session.anchor_framework_item_id
        or session.guidance_mode
        != MemoirGuidanceMode.GUIDED_INTERVIEW.value
        or session.session_goal != MemoirSessionGoal.OPEN_NARRATION.value
        or any(
            message.role == MemoirChatMessageRole.USER.value
            and message.content_status == MemoirChatContentStatus.FINAL.value
            and message.content_text.strip()
            for message in session.messages
        )
    ):
        return ""
    context = await build_current_policy_context(
        db,
        session=session,
        completion_issue_id=None,
        policy_version=ACTIVE_POLICY_VERSION,
    )
    if context is None or len(context.topic_candidates) != 1:
        return ""
    try:
        brief = await interview_topics.brief_for_candidate(
            db,
            project_id=context.project_id,
            base_brief=context.brief,
            candidate=context.topic_candidates[0],
        )
    except interview_topics.InterviewTopicUnavailableError:
        return ""
    return topic_offer_reply_instruction(brief)


async def persist_policy_turn_decision(
    db: AsyncSession,
    *,
    session: MemoirVoiceSession,
    generated_message: MemoirChatMessage,
    policy_turn: ActivePolicyTurn,
) -> MemoirInterviewPolicyDecision:
    decision = await interview_policy.create_policy_decision(
        db,
        session_id=session.id,
        trigger_message_id=policy_turn.context.trigger_message_id,
        brief=policy_turn.context.brief,
        selected_action=policy_turn.selected_action,
        reason_code=(
            f"{policy_turn.execution_mode.value}_"
            f"{policy_turn.selected_action.value}"
        ),
        reason_summary=_REASON_SUMMARIES[policy_turn.selected_action],
        execution_mode=policy_turn.execution_mode,
        model_provider=policy_turn.model_provider,
        model_name=policy_turn.model_name,
    )
    await interview_policy.attach_generated_message(
        db,
        decision=decision,
        message_id=generated_message.id,
    )
    return decision


async def schedule_shadow_policy_evaluation(
    db: AsyncSession,
    *,
    session: MemoirVoiceSession,
    generated_message: MemoirChatMessage,
    completion_issue_id: str | None,
    requested_ai_model: str | None,
    enabled: bool | None = None,
) -> MemoirEditingJob | None:
    settings = get_settings()
    is_enabled = (
        settings.memoir_interview_policy_shadow_enabled
        if enabled is None
        else enabled
    )
    if not is_enabled or not _eligible_session(session):
        return None
    trigger = await db.scalar(
        select(MemoirChatMessage)
        .where(
            MemoirChatMessage.voice_session_id == session.id,
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
            MemoirChatMessage.content_status == MemoirChatContentStatus.FINAL.value,
            func.trim(MemoirChatMessage.content_text) != "",
            MemoirChatMessage.turn_index < generated_message.turn_index,
        )
        .order_by(
            MemoirChatMessage.turn_index.desc(),
            MemoirChatMessage.created_at.desc(),
            MemoirChatMessage.id.desc(),
        )
        .limit(1)
    )
    selected_model = (
        settings.memoir_interview_policy_shadow_model.strip()
        or (requested_ai_model or "").strip()
    )
    payload: dict[str, object] = {
        "session_id": session.id,
        "generated_message_id": generated_message.id,
        "trigger_message_id": trigger.id if trigger else None,
        "completion_issue_id": completion_issue_id,
        "policy_version": SHADOW_POLICY_VERSION,
        "prompt_version": SHADOW_PROMPT_VERSION,
    }
    if selected_model:
        payload["ai_model"] = selected_model
    return await enqueue_editing_job(
        db,
        project_id=session.project_id,
        job_type=MemoirEditingJobType.EVALUATE_INTERVIEW_POLICY_SHADOW,
        payload_json=payload,
        dedupe_key=(
            f"interview_policy_shadow:{generated_message.id}:"
            f"{SHADOW_POLICY_VERSION}"
        ),
        retry_failed=True,
    )


async def run_shadow_policy_evaluation_job(
    db: AsyncSession,
    job: MemoirEditingJob,
    *,
    ai_client: AITextClient | None = None,
) -> EditingJobResult:
    payload = job.payload_json if isinstance(job.payload_json, dict) else {}
    session_id = str(payload.get("session_id") or "").strip()
    generated_message_id = str(payload.get("generated_message_id") or "").strip()
    completion_issue_id = (
        str(payload.get("completion_issue_id") or "").strip() or None
    )
    if not session_id or not generated_message_id:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="shadow_policy_source_unavailable",
        )
    existing = await db.scalar(
        select(MemoirInterviewPolicyDecision).where(
            MemoirInterviewPolicyDecision.generated_message_id
            == generated_message_id,
            MemoirInterviewPolicyDecision.execution_mode
            == MemoirInterviewPolicyExecutionMode.SHADOW.value,
            MemoirInterviewPolicyDecision.policy_version
            == SHADOW_POLICY_VERSION,
        )
    )
    if existing is not None:
        return EditingJobResult(
            result_json={
                "decision_id": existing.id,
                "selected_action": existing.selected_action,
                "reused": True,
            }
        )
    session = await db.scalar(
        select(MemoirVoiceSession)
        .where(
            MemoirVoiceSession.id == session_id,
            MemoirVoiceSession.project_id == job.project_id,
        )
        .options(
            selectinload(MemoirVoiceSession.messages),
            selectinload(MemoirVoiceSession.utterances),
        )
    )
    generated_message = await db.get(MemoirChatMessage, generated_message_id)
    if (
        session is None
        or generated_message is None
        or generated_message.voice_session_id != session.id
        or generated_message.role != MemoirChatMessageRole.ASSISTANT.value
        or generated_message.content_status != MemoirChatContentStatus.FINAL.value
        or not _eligible_session(session)
    ):
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="shadow_policy_source_unavailable",
        )
    context = await _load_shadow_context(
        db,
        session=session,
        generated_message=generated_message,
        completion_issue_id=completion_issue_id,
    )
    if context is None:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="shadow_policy_brief_unavailable",
        )
    expected_trigger_id = str(payload.get("trigger_message_id") or "").strip() or None
    if context.trigger_message_id != expected_trigger_id:
        return EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="shadow_policy_context_changed",
        )
    model = str(payload.get("ai_model") or "").strip() or None
    client = ai_client or get_ai_service(model)
    selection = await select_policy_action(
        context=context,
        ai_client=client,
    )
    context = await _context_for_selection(
        db,
        context=context,
        selection=selection,
    )
    decision = await interview_policy.create_policy_decision(
        db,
        session_id=session.id,
        trigger_message_id=context.trigger_message_id,
        brief=context.brief,
        selected_action=selection.action,
        reason_code=f"shadow_{selection.action.value}",
        reason_summary=_REASON_SUMMARIES[selection.action],
        execution_mode=MemoirInterviewPolicyExecutionMode.SHADOW,
        model_provider=client.provider_name,
        model_name=client.model_name,
    )
    await interview_policy.attach_generated_message(
        db,
        decision=decision,
        message_id=generated_message.id,
    )
    return EditingJobResult(
        result_json={
            "decision_id": decision.id,
            "selected_action": selection.action.value,
            "brief_fingerprint": decision.brief_fingerprint,
            "generated_message_id": generated_message.id,
            "provider": client.provider_name,
            "model": client.model_name,
            "policy_version": SHADOW_POLICY_VERSION,
            "prompt_version": SHADOW_PROMPT_VERSION,
        }
    )
