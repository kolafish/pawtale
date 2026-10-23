from __future__ import annotations

from dataclasses import dataclass
import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.narrative_types import MemoirFrameworkUserResolution
from app.domain.memoir.types import (
    MemoirInterviewType,
    MemoirSessionGoal,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.services.memoir.narrative import frameworks


MAX_TOPIC_CANDIDATES = 6
SUPPLEMENT_OFFER_REQUEST_PHRASES = (
    "不知道讲什么了",
    "不知道说什么了",
)
DIRECTION_REQUEST_PHRASES = (
    "不知道讲什么",
    "不知道说什么",
    "给个方向",
    "给我一个方向",
    "换个话题",
    "换一个话题",
    "聊点别的",
    "接下来讲什么",
    "你来问",
    "问我吧",
)


class InterviewTopicUnavailableError(ValueError):
    pass


@dataclass(frozen=True)
class FrameworkTopicCandidate:
    item_id: str
    title: str
    intent: str


def explicitly_requests_supplement(text: str) -> bool:
    normalized = "".join(text.split())
    return any(phrase in normalized for phrase in SUPPLEMENT_OFFER_REQUEST_PHRASES)


def is_home_voice_entry(session: MemoirVoiceSession) -> bool:
    return (
        session.session_goal == MemoirSessionGoal.OPEN_NARRATION.value
        and (session.metadata_json or {}).get("entry")
        in {"miniprogram_home", "miniprogram_home_launch", "miniprogram_home_resume"}
    )


def is_new_story_entry(session: MemoirVoiceSession) -> bool:
    return is_home_voice_entry(session) and (session.metadata_json or {}).get("entry") in {
        "miniprogram_home",
        "miniprogram_home_launch",
    }


def topic_offer_requested(
    session: MemoirVoiceSession,
    latest_user_message: MemoirChatMessage | None,
) -> bool:
    if (
        session.interview_type != MemoirInterviewType.AI_INTERVIEW.value
        or session.session_goal != MemoirSessionGoal.OPEN_NARRATION.value
    ):
        return False
    if latest_user_message is None:
        return bool(session.anchor_framework_item_id) or not is_new_story_entry(session)
    normalized = "".join(latest_user_message.content_text.split())
    if explicitly_requests_supplement(latest_user_message.content_text):
        return False
    return any(phrase in normalized for phrase in DIRECTION_REQUEST_PHRASES)


async def current_framework_item_id(db: AsyncSession, session: MemoirVoiceSession) -> str | None:
    if session.anchor_framework_item_id:
        return session.anchor_framework_item_id
    if (
        session.interview_type != MemoirInterviewType.AI_INTERVIEW.value
        or session.session_goal != MemoirSessionGoal.OPEN_NARRATION.value
    ):
        return None
    # Legacy sessions cannot store an anchor under the existing DB constraint.
    # Their accepted topic already lives on the original answer material.
    return await db.scalar(
        select(MemoirVoiceUtterance.framework_item_id)
        .join(MemoirChatMessage, MemoirVoiceUtterance.chat_message_id == MemoirChatMessage.id)
        .where(
            MemoirVoiceUtterance.voice_session_id == session.id,
            MemoirVoiceUtterance.framework_item_id.is_not(None),
        )
        .order_by(MemoirChatMessage.turn_index.desc(), MemoirVoiceUtterance.created_at.desc(), MemoirVoiceUtterance.id.desc())
        .limit(1)
    )


async def candidate_topics(
    db: AsyncSession,
    *,
    session: MemoirVoiceSession,
    latest_user_message: MemoirChatMessage | None,
) -> tuple[FrameworkTopicCandidate, ...]:
    if not topic_offer_requested(session, latest_user_message):
        return ()
    project_framework = await frameworks.framework_for_project(
        db,
        project_id=session.project_id,
    )
    if project_framework is None:
        return ()
    template = frameworks.template_for_framework(project_framework)
    definitions = {
        definition.item_key: definition for definition in template.items
    }
    eligible = [
        item
        for item in project_framework.items
        if item.user_resolution == MemoirFrameworkUserResolution.NONE.value
    ]
    anchor_item_id = await current_framework_item_id(db, session)
    if anchor_item_id and latest_user_message is None:
        eligible = [
            item
            for item in eligible
            if item.id == anchor_item_id
        ]
    elif eligible:
        if anchor_item_id:
            eligible = [
                item
                for item in eligible
                if item.id != anchor_item_id
            ]
        if not eligible:
            return ()
        seed = (
            f"{session.id}:"
            f"{latest_user_message.id if latest_user_message else 'opening'}"
        )
        start = int.from_bytes(
            hashlib.sha256(seed.encode("utf-8")).digest()[:4],
            "big",
        ) % len(eligible)
        eligible = eligible[start:] + eligible[:start]
    return tuple(
        FrameworkTopicCandidate(
            item_id=item.id,
            title=definitions[item.item_key].title,
            intent=definitions[item.item_key].intent,
        )
        for item in eligible[:MAX_TOPIC_CANDIDATES]
        if item.item_key in definitions
    )
