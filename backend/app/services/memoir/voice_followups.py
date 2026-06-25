from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import (
    MemoirChatContentStatus,
    MemoirChatMessageRole,
)
from app.models.memoir import MemoirChatMessage
from app.schemas.memoir_voice import (
    MemoirVoiceNoAnswerPromptRequest,
    MemoirVoiceFollowUpResponse,
)
from app.services.memoir.voice_chat_context import (
    load_voice_session,
    next_turn_index,
)


NO_ANSWER_PROMPT_VERSION = "memoir_voice_no_answer_prompt_v1"
NO_ANSWER_RETRY_TEXT = "我这边没有听到声音，您可以接着刚才的问题慢慢说。"
NO_ANSWER_PAUSE_TEXT = "没关系，我先暂停一下。您准备好了，再点继续说话。"


async def create_no_answer_prompt(
    db: AsyncSession,
    voice_session_id: str,
    user_id: str,
    req: MemoirVoiceNoAnswerPromptRequest,
) -> MemoirVoiceFollowUpResponse:
    voice_session = await load_voice_session(db, voice_session_id, user_id)
    prompt_text = NO_ANSWER_RETRY_TEXT if req.attempt <= 1 else NO_ANSWER_PAUSE_TEXT
    expects_answer = req.attempt <= 1
    message = MemoirChatMessage(
        project_id=voice_session.project_id,
        voice_session_id=voice_session.id,
        role=MemoirChatMessageRole.ASSISTANT.value,
        content_text=prompt_text,
        content_status=MemoirChatContentStatus.FINAL.value,
        guidance_intent="no_answer_prompt",
        turn_index=await next_turn_index(db, voice_session.id),
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
