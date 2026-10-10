from __future__ import annotations

from dataclasses import dataclass
import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.interview_policy import InterviewBrief
from app.domain.memoir.narrative_types import MemoirFrameworkUserResolution
from app.domain.memoir.types import (
    MemoirGuidanceMode,
    MemoirSessionGoal,
)
from app.models.memoir import (
    MemoirChatMessage,
    MemoirVoiceSession,
    MemoirVoiceUtterance,
)
from app.models.memoir_narrative import (
    MemoirFrameworkItem,
    MemoirProjectFramework,
)
from app.services.memoir.narrative import frameworks, interview_policy


MAX_TOPIC_CANDIDATES = 6
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


def topic_offer_requested(
    session: MemoirVoiceSession,
    latest_user_message: MemoirChatMessage | None,
) -> bool:
    if (
        session.guidance_mode != MemoirGuidanceMode.GUIDED_INTERVIEW.value
        or session.session_goal != MemoirSessionGoal.OPEN_NARRATION.value
    ):
        return False
    if latest_user_message is None:
        return True
    normalized = "".join(latest_user_message.content_text.split())
    return any(phrase in normalized for phrase in DIRECTION_REQUEST_PHRASES)


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
    if session.anchor_framework_item_id and latest_user_message is None:
        eligible = [
            item
            for item in eligible
            if item.id == session.anchor_framework_item_id
        ]
    elif eligible:
        if session.anchor_framework_item_id:
            eligible = [
                item
                for item in eligible
                if item.id != session.anchor_framework_item_id
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


async def brief_for_candidate(
    db: AsyncSession,
    *,
    project_id: str,
    candidate: FrameworkTopicCandidate,
    base_brief: InterviewBrief,
) -> InterviewBrief:
    item = await db.scalar(
        select(MemoirFrameworkItem)
        .join(
            MemoirProjectFramework,
            MemoirProjectFramework.id
            == MemoirFrameworkItem.project_framework_id,
        )
        .where(
            MemoirFrameworkItem.id == candidate.item_id,
            MemoirProjectFramework.project_id == project_id,
        )
    )
    if (
        item is None
        or item.user_resolution != MemoirFrameworkUserResolution.NONE.value
    ):
        raise InterviewTopicUnavailableError(
            "interview_policy_topic_candidate_unavailable"
        )
    project_framework = await frameworks.framework_for_project(
        db,
        project_id=project_id,
    )
    if (
        project_framework is None
        or project_framework.id != item.project_framework_id
    ):
        raise InterviewTopicUnavailableError(
            "interview_policy_topic_candidate_unavailable"
        )
    template = frameworks.template_for_framework(project_framework)
    definition = next(
        definition
        for definition in template.items
        if definition.item_key == item.item_key
    )
    topic_material_ids = tuple(
        sorted(
            set(
                (
                    await db.scalars(
                        select(MemoirVoiceUtterance.id).where(
                            MemoirVoiceUtterance.framework_item_id == item.id
                        )
                    )
                ).all()
            )
        )
    )
    return interview_policy.framework_item_brief(
        item=item,
        definition=definition,
        template_version=project_framework.template_version,
        current_story_state=base_brief.current_story_state,
        conversation_momentum=base_brief.conversation_momentum,
        existing_material_refs=tuple(
            sorted(
                set(base_brief.existing_material)
                | set(topic_material_ids)
            )
        ),
        user_preferences=base_brief.user_preferences,
        safety_constraints=base_brief.safety_constraints,
        policy_version=base_brief.policy_version,
    )
