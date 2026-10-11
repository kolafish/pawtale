"""Fill the narrator's profile from what they say in an AI interview.

A new book has no profile form: its first opening asks how to address the narrator, and the
interviewer picks up age and hometown along the way. After a narration settles, this reads the
start of the session's dialogue and fills profile fields that are still empty. A field filled in
the same session can still change, so a spoken correction sticks; a value the user saved on the
project page is never overwritten.
"""

from __future__ import annotations

from datetime import date
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirCreatedBy, MemoirInterviewType
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession
from app.models.projects import Project
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir import book_outputs
from app.services.memoir.project_ai_preferences import resolve_project_ai_model
from app.services.memoir.projects import (
    FIRST_PROJECT_TITLE,
    INFERRED_PROFILE_FIELDS_KEY,
    MEMOIR_PROFILE_METADATA_KEY,
)
from app.services.memoir.voice_followup_ai import _message_context, _ordered_messages


logger = logging.getLogger(__name__)

# The interviewer asks for these early, so the start of a session is where they are said.
DIALOGUE_MESSAGE_LIMIT = 30
# Gender is only inferred, so a book without it is not "missing" anything worth another call.
_ASKED_FIELDS = ("narrator_name", "narrator_age", "narrator_origin")
SYSTEM_PROMPT = "你从回忆录访谈对话里记下讲述者本人的基本资料，只记他说出来或能明确看出的内容。"


class _ProfileOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    gender: Literal["male", "female"] | None = None
    age: int | None = None
    origin: str | None = None


def _is_empty(value: object) -> bool:
    return value in (None, "", "unspecified")


def _clean(value: str | None, *, limit: int) -> str:
    return "".join((value or "").split())[:limit]


def _profile(project: Project) -> tuple[dict, dict]:
    profile = dict((project.metadata_json or {}).get(MEMOIR_PROFILE_METADATA_KEY) or {})
    return profile, dict(profile.get(INFERRED_PROFILE_FIELDS_KEY) or {})


def build_profile_prompt(dialogue: str, *, today: date) -> str:
    return (
        f"今年是 {today.year} 年。下面是一次回忆录访谈开头的对话，“叙述者”是讲述者本人，“AI”是访谈主持人。\n\n"
        f"{dialogue}\n\n"
        "记下讲述者本人的：\n"
        "- name：他希望怎么被称呼；说了全名就用全名，否则用他说的称呼，比如“老王”\n"
        "- gender：male 或 female，只在他讲的内容能明确看出时填写\n"
        "- age：周岁；他说的是出生年份就按今年算\n"
        "- origin：老家或籍贯，写成地名，比如“四川成都”\n"
        "只记他自己的情况，家人的名字、年龄、老家不算。他后来纠正过的，以纠正后为准。没说到的填 null。\n"
        '只返回 JSON：{"name":null,"gender":null,"age":null,"origin":null}'
    )


async def fill_narrator_profile(
    db: AsyncSession,
    *,
    project: Project,
    voice_session: MemoirVoiceSession,
) -> None:
    if voice_session.interview_type != MemoirInterviewType.AI_INTERVIEW.value:
        # Both speakers of a human interview are recorded as the narrator.
        return
    profile, inferred = _profile(project)
    if not any(_is_empty(profile.get(key)) for key in _ASKED_FIELDS) and voice_session.id not in inferred.values():
        return
    messages = (
        await db.scalars(select(MemoirChatMessage).where(MemoirChatMessage.voice_session_id == voice_session.id))
    ).all()
    dialogue = _message_context(_ordered_messages(list(messages))[:DIALOGUE_MESSAGE_LIMIT])
    if not dialogue:
        return
    ai_model = resolve_project_ai_model(project, None)
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    await db.commit()
    try:
        generated = await generate_structured_with_client(
            get_ai_service(ai_model),
            SYSTEM_PROMPT,
            build_profile_prompt(dialogue, today=date.today()),
            output_model=_ProfileOutput,
            requested_model=ai_model,
            temperature=0.1,
            max_tokens=policy.max_tokens,
            timeout=ai_text_timeout_seconds(policy.timeout_profile),
            max_attempts=policy.max_attempts,
        )
    except Exception:
        # The profile is optional and the next narration of this book tries again.
        logger.warning("Narrator profile extraction failed for project %s", project.id, exc_info=True)
        return
    # The user may have saved the project page during the call.
    await db.refresh(project)
    profile, inferred = _profile(project)
    output = generated.value
    found = {
        "narrator_name": _clean(output.name, limit=40),
        "narrator_gender": output.gender,
        "narrator_age": output.age if output.age is not None and 0 < output.age <= 120 else None,
        "narrator_origin": _clean(output.origin, limit=40),
    }
    changed = {
        key: value
        for key, value in found.items()
        if not _is_empty(value)
        and value != profile.get(key)
        and (_is_empty(profile.get(key)) or inferred.get(key) == voice_session.id)
    }
    if not changed:
        return
    previous_name = str(profile.get("narrator_name") or "")
    profile.update(changed)
    profile[INFERRED_PROFILE_FIELDS_KEY] = {**inferred, **{key: voice_session.id for key in changed}}
    project.metadata_json = {**(project.metadata_json or {}), MEMOIR_PROFILE_METADATA_KEY: profile}
    name = changed.get("narrator_name")
    renamable = {FIRST_PROJECT_TITLE, f"{previous_name}的回忆录"} if previous_name else {FIRST_PROJECT_TITLE}
    if name and project.title.strip() in renamable:
        project.title = f"{name}的回忆录"
        await book_outputs.synchronize_book_output_title(
            db,
            project_id=project.id,
            title=project.title,
            created_by=MemoirCreatedBy.AI,
        )
    db.add(project)
    await db.commit()
