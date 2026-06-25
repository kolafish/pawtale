from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
)
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.models.projects import Project
from app.schemas.memoir_voice import (
    MemoirVoiceNoAnswerPromptRequest,
    MemoirVoiceFollowUpRequest,
    MemoirVoiceFollowUpResponse,
    MemoirVoiceTurnPlanRequest,
    MemoirVoiceTurnPlanResponse,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.memoir.voice_followup_ai import (
    MAX_PREVIOUS_CONTEXT_MESSAGES,
    PROMPT_VERSION,
    TURN_PLAN_MAX_TOKENS,
    TURN_PLAN_PROMPT_VERSION,
    InterviewerTurn,
    _align_plan_to_requested_topic,
    _build_turn_plan_prompt,
    _build_user_prompt,
    _call_interviewer_llm,
    _extract_finish_reason,
    _has_user_message,
    _is_truncated_response,
    _llm_max_tokens,
    _parse_interviewer_turn,
    _parse_voice_turn_plan,
    _planned_question_turn,
    _question_mark_count,
    _usage_metadata,
    _voice_turn_plan_from_request,
)


NO_ANSWER_PROMPT_VERSION = "memoir_voice_no_answer_prompt_v1"
NO_ANSWER_RETRY_TEXT = "我这边没有听到声音，您可以接着刚才的问题慢慢说。"
NO_ANSWER_PAUSE_TEXT = "没关系，我先暂停一下。您准备好了，再点继续说话。"


def _acknowledgement_message(
    voice_session: MemoirVoiceSession,
    message_id: str | None,
) -> MemoirChatMessage | None:
    if not message_id:
        return None
    for message in voice_session.messages:
        if message.id != message_id:
            continue
        metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
        if (
            message.role == MemoirChatMessageRole.ASSISTANT.value
            and message.guidance_intent in {"acknowledge", "encourage_continue", "reflect_summary"}
            and metadata.get("expects_answer") is False
        ):
            return message
        raise HTTPException(status_code=400, detail="承接消息不能用于生成追问")
    raise HTTPException(status_code=400, detail="承接消息不存在")


async def _load_voice_session(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
) -> MemoirVoiceSession:
    result = await db.execute(
        select(MemoirVoiceSession)
        .join(Project, MemoirVoiceSession.project_id == Project.id)
        .where(MemoirVoiceSession.id == voice_session_id, Project.user_id == user_id)
        .options(selectinload(MemoirVoiceSession.messages))
    )
    voice_session = result.scalar_one_or_none()
    if not voice_session:
        raise HTTPException(status_code=404, detail="本次语音记录不存在")
    return voice_session


async def _load_previous_project_messages(
    db: AsyncSession,
    voice_session: MemoirVoiceSession,
) -> list[MemoirChatMessage]:
    result = await db.execute(
        select(MemoirChatMessage)
        .join(MemoirVoiceSession, MemoirChatMessage.voice_session_id == MemoirVoiceSession.id)
        .where(
            MemoirChatMessage.project_id == voice_session.project_id,
            MemoirChatMessage.voice_session_id != voice_session.id,
            MemoirVoiceSession.guidance_mode == voice_session.guidance_mode,
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
            MemoirChatMessage.content_text != "",
        )
        .order_by(
            MemoirChatMessage.created_at.desc(),
            MemoirChatMessage.turn_index.desc(),
            MemoirChatMessage.id.desc(),
        )
        .limit(MAX_PREVIOUS_CONTEXT_MESSAGES)
    )
    return list(reversed(result.scalars().all()))


async def _next_turn_index(db: AsyncSession, voice_session_id: str) -> int:
    result = await db.execute(
        select(func.max(MemoirChatMessage.turn_index)).where(
            MemoirChatMessage.voice_session_id == voice_session_id
        )
    )
    current = result.scalar_one_or_none()
    return 0 if current is None else int(current) + 1


async def _save_interviewer_turn(
    db: AsyncSession,
    *,
    voice_session: MemoirVoiceSession,
    turn: InterviewerTurn,
    client: AITextClient,
    prompt_version: str,
    max_tokens: int,
    raw_response: dict | None,
    fallback_intent: str,
    metadata_extra: dict[str, object],
) -> MemoirVoiceFollowUpResponse:
    follow_up_text = turn.speech_text
    if not follow_up_text:
        raise HTTPException(status_code=502, detail="AI 未返回可用访谈回应")
    finish_reason = _extract_finish_reason(raw_response)
    metadata = {
        "source": f"ai.{turn.primary_intent}",
        "prompt_version": prompt_version,
        "fallback_intent": fallback_intent,
        "primary_intent": turn.primary_intent,
        "intent_tags": turn.intent_tags,
        "expects_answer": turn.expects_answer,
        "parsed_json": turn.parsed_json,
        "provider": client.provider_name,
        "model": client.model_name,
        "max_tokens": max_tokens,
        "response_chars": len(follow_up_text),
        "question_mark_count": _question_mark_count(follow_up_text),
        "finish_reason": finish_reason,
        "truncated": _is_truncated_response(finish_reason),
        "usage": _usage_metadata(raw_response),
    }
    metadata.update(metadata_extra)
    message = MemoirChatMessage(
        project_id=voice_session.project_id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text=follow_up_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        guidance_intent=turn.primary_intent,
        turn_index=await _next_turn_index(db, voice_session.id),
        metadata_json=metadata,
    )
    db.add(message)
    await db.commit()
    return MemoirVoiceFollowUpResponse(
        message=message,
        follow_up_text=follow_up_text,
        primary_intent=turn.primary_intent,
        intent_tags=turn.intent_tags,
        expects_answer=turn.expects_answer,
    )


async def generate_voice_follow_up(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceFollowUpRequest,
    ai_client: AITextClient | None = None,
) -> MemoirVoiceFollowUpResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    acknowledgement_message = _acknowledgement_message(voice_session, req.acknowledgement_message_id)
    client = ai_client or get_ai_service(req.ai_model)
    previous_messages = await _load_previous_project_messages(db, voice_session)
    turn_plan = _voice_turn_plan_from_request(req.turn_plan)
    prompt = _build_user_prompt(
        voice_session,
        previous_messages=previous_messages,
        regenerate=req.regenerate,
        acknowledgement_message_id=acknowledgement_message.id if acknowledgement_message else None,
        turn_plan=turn_plan,
    )
    has_user_message = _has_user_message(list(voice_session.messages))
    fallback_intent = "follow_up" if has_user_message else "opening"
    llm_max_tokens = _llm_max_tokens(
        voice_session,
        has_user_message=has_user_message,
        regenerate=req.regenerate,
    )
    content, raw_response = await _call_interviewer_llm(
        client,
        prompt=prompt,
        max_tokens=llm_max_tokens,
    )
    turn = _parse_interviewer_turn(content, fallback_intent=fallback_intent)
    if acknowledgement_message or turn_plan:
        turn = _planned_question_turn(turn)
    return await _save_interviewer_turn(
        db,
        voice_session=voice_session,
        turn=turn,
        client=client,
        prompt_version=PROMPT_VERSION,
        max_tokens=llm_max_tokens,
        raw_response=raw_response,
        fallback_intent=fallback_intent,
        metadata_extra={
            "regenerate": req.regenerate,
            "has_user_message": has_user_message,
            "turn_pipeline_stage": "question",
            "acknowledgement_message_id": acknowledgement_message.id if acknowledgement_message else None,
            "turn_plan": turn_plan.to_context().model_dump() if turn_plan else None,
        },
    )


async def plan_voice_turn(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceTurnPlanRequest,
    ai_client: AITextClient | None = None,
) -> MemoirVoiceTurnPlanResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    messages = list(voice_session.messages)
    if not _has_user_message(messages):
        raise HTTPException(status_code=400, detail="还没有可规划的讲述内容")
    client = ai_client or get_ai_service(req.ai_model)
    previous_messages = await _load_previous_project_messages(db, voice_session)
    prompt = _build_turn_plan_prompt(voice_session, previous_messages=previous_messages)
    content, raw_response = await _call_interviewer_llm(
        client,
        prompt=prompt,
        max_tokens=TURN_PLAN_MAX_TOKENS,
    )
    plan = _align_plan_to_requested_topic(_parse_voice_turn_plan(content), messages)
    acknowledgement: MemoirVoiceFollowUpResponse | None = None
    if plan.should_play_ack:
        turn = InterviewerTurn(
            primary_intent="acknowledge",
            intent_tags=["acknowledge"],
            speech_text=plan.acknowledgement_text,
            expects_answer=False,
            parsed_json=plan.parsed_json,
        )
        acknowledgement = await _save_interviewer_turn(
            db,
            voice_session=voice_session,
            turn=turn,
            client=client,
            prompt_version=TURN_PLAN_PROMPT_VERSION,
            max_tokens=TURN_PLAN_MAX_TOKENS,
            raw_response=raw_response,
            fallback_intent="acknowledge",
            metadata_extra={
                "has_user_message": True,
                "turn_pipeline_stage": "acknowledgement",
                "turn_plan": plan.to_context().model_dump(),
            },
        )
    return MemoirVoiceTurnPlanResponse(
        plan=plan.to_context(),
        acknowledgement=acknowledgement,
    )


async def create_no_answer_prompt(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceNoAnswerPromptRequest,
) -> MemoirVoiceFollowUpResponse:
    voice_session = await _load_voice_session(db, voice_session_id, user_id)
    prompt_text = NO_ANSWER_RETRY_TEXT if req.attempt <= 1 else NO_ANSWER_PAUSE_TEXT
    expects_answer = req.attempt <= 1
    message = MemoirChatMessage(
        project_id=voice_session.project_id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text=prompt_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        guidance_intent="no_answer_prompt",
        turn_index=await _next_turn_index(db, voice_session.id),
        metadata_json={
            "source": "ai.no_answer_prompt",
            "prompt_version": NO_ANSWER_PROMPT_VERSION,
            "primary_intent": "repair",
            "intent_tags": ["repair"],
            "expects_answer": expects_answer,
            "attempt": req.attempt,
        },
    )
    db.add(message)
    await db.commit()
    return MemoirVoiceFollowUpResponse(
        message=message,
        follow_up_text=prompt_text,
        primary_intent="repair",
        intent_tags=["repair"],
        expects_answer=expects_answer,
    )
