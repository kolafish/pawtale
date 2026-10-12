"""Fill the narrator's profile from what they say in a book's first AI interview.

A new book has no profile form. The book's first voice session where the narrator speaks becomes its
profile session: there the interviewer asks for 称呼, age and hometown, and every narrator segment
(one recording) is read once, together with the interviewer's line before it and what is already
known, for anything the narrator newly says or corrects about themselves. A segment is read again
when its transcript changes. Asking stops after two asks or a refusal; reading does not, so a late
answer is still kept. A value the user saved on the project page is never overwritten.
"""

from __future__ import annotations

from datetime import date
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memoir.types import (
    MemoirChatMessageRole,
    MemoirCreatedBy,
    MemoirEditingJobStatus,
    MemoirEditingJobType,
    MemoirInterviewType,
    MemoirSessionGoal,
)
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.memoir_jobs import MemoirEditingJob
from app.models.projects import Project
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir import book_outputs, editing_jobs
from app.services.memoir.project_ai_preferences import resolve_project_ai_model
from app.services.memoir.projects import (
    FIRST_PROJECT_TITLE,
    INFERRED_PROFILE_FIELDS_KEY,
    MEMOIR_PROFILE_METADATA_KEY,
    PROFILE_SESSION_KEY,
    SKIPPED_PROFILE_FIELDS_KEY,
    profile_items_to_ask,
)


# Each item is asked at most this many times; an answer given later is still kept.
MAX_ASKS = 2
SYSTEM_PROMPT = "你从回忆录访谈里记下讲述者本人的基本资料，只记他说出来或能明确看出的内容。"
_LABELS = {"narrator_name": "称呼", "narrator_age": "年龄", "narrator_origin": "老家"}
_SKIP_FIELDS = {"name": "narrator_name", "age": "narrator_age", "origin": "narrator_origin"}
# Words an interviewer line uses when it asks for each item.
_ASK_PATTERNS = {
    "narrator_name": re.compile(r"称呼|贵姓|叫您|叫什么|名字"),
    "narrator_age": re.compile(r"哪年|哪一年|出生|多大|年纪|高寿|岁数|几岁|属什么|属相"),
    "narrator_origin": re.compile(r"老家|哪里人|哪儿人|家乡|籍贯|祖籍"),
}


class _ProfileOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    gender: Literal["male", "female"] | None = None
    age: int | None = None
    origin: str | None = None
    skip: list[Literal["name", "age", "origin"]] = []


def _is_empty(value: object) -> bool:
    return value in (None, "", "unspecified")


def _clean(value: str | None, *, limit: int) -> str:
    return "".join((value or "").split())[:limit]


def _profile(project: Project) -> dict:
    profile = (project.metadata_json or {}).get(MEMOIR_PROFILE_METADATA_KEY)
    return dict(profile) if isinstance(profile, dict) else {}


def _collects_profile(voice_session: MemoirVoiceSession) -> bool:
    # Both speakers of a human interview are recorded as the narrator.
    return (
        voice_session.interview_type == MemoirInterviewType.AI_INTERVIEW.value
        and voice_session.session_goal == MemoirSessionGoal.OPEN_NARRATION.value
    )


async def _project_has_narration_elsewhere(db: AsyncSession, voice_session: MemoirVoiceSession) -> bool:
    return bool(await db.scalar(
        select(1)
        .select_from(MemoirChatMessage)
        .where(
            MemoirChatMessage.project_id == voice_session.project_id,
            MemoirChatMessage.voice_session_id != voice_session.id,
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
            MemoirChatMessage.content_text != "",
        )
        .limit(1)
    ))


async def is_profile_session(db: AsyncSession, voice_session: MemoirVoiceSession, project: Project | None) -> bool:
    """Whether this session asks for the profile; before the narrator speaks, whether it will."""
    if project is None or not _collects_profile(voice_session):
        return False
    pinned = _profile(project).get(PROFILE_SESSION_KEY)
    if pinned:
        return pinned == voice_session.id
    return not await _project_has_narration_elsewhere(db, voice_session)


async def pin_profile_session(db: AsyncSession, *, voice_session: MemoirVoiceSession) -> None:
    """Make this the profile session when the narrator first speaks in a book with no narration yet.

    Runs in the transaction that saves the narrator's segment.
    """
    if not _collects_profile(voice_session):
        return
    project = await db.get(Project, voice_session.project_id)
    if project is None or _profile(project).get(PROFILE_SESSION_KEY):
        return
    if await _project_has_narration_elsewhere(db, voice_session):
        return
    await db.refresh(project, with_for_update=True)
    profile = _profile(project)
    if profile.get(PROFILE_SESSION_KEY):
        return
    profile[PROFILE_SESSION_KEY] = voice_session.id
    project.metadata_json = {**(project.metadata_json or {}), MEMOIR_PROFILE_METADATA_KEY: profile}
    db.add(project)


def _has_open_fields(profile: dict) -> bool:
    """Whether a segment could still change the profile: a field is empty or was heard rather than saved."""
    inferred = profile.get(INFERRED_PROFILE_FIELDS_KEY) or {}
    return any(_is_empty(profile.get(key)) or key in inferred for key in (*_LABELS, "narrator_gender"))


def profile_asks(messages: list[MemoirChatMessage]) -> dict[str, int]:
    """How many interviewer lines in a session asked for each item."""
    counts: dict[str, int] = {}
    for message in messages:
        field = (message.metadata_json or {}).get("profile_asked")
        if message.role == MemoirChatMessageRole.ASSISTANT.value and field in _LABELS:
            counts[field] = counts.get(field, 0) + 1
    return counts


def next_profile_question(profile: dict, messages: list[MemoirChatMessage]) -> str | None:
    """The one item the interviewer may ask for now, in the order 称呼, 年龄, 老家."""
    asks = profile_asks(messages)
    return next((key for key in profile_items_to_ask(profile) if asks.get(key, 0) < MAX_ASKS), None)


def asked_profile_field(field: str | None, text: str) -> str | None:
    """The offered item, if this interviewer line asks for it."""
    if field not in _ASK_PATTERNS or not re.search(r"[？?]", text):
        return None
    return field if _ASK_PATTERNS[field].search(text) else None


async def schedule_narrator_profile(db: AsyncSession, *, utterance: MemoirVoiceUtterance) -> None:
    """Queue reading this segment, once per transcript version, if it belongs to the profile session."""
    # Callers load the utterance differently; read its message by id rather than through the relationship.
    message = await db.get(MemoirChatMessage, utterance.chat_message_id) if utterance.chat_message_id else None
    if message is None or message.role != MemoirChatMessageRole.USER.value or not message.content_text.strip():
        return
    voice_session = await db.get(MemoirVoiceSession, utterance.voice_session_id)
    project = await db.get(Project, voice_session.project_id) if voice_session else None
    if voice_session is None or project is None:
        return
    profile = _profile(project)
    if profile.get(PROFILE_SESSION_KEY) != voice_session.id or not _has_open_fields(profile):
        return
    version_id = message.transcript_version_id or ""
    dedupe_key = f"narrator_profile:{utterance.id}"
    existing = await db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == dedupe_key))
    if (
        existing is not None
        and (existing.payload_json or {}).get("transcript_version_id") == version_id
        and existing.status != MemoirEditingJobStatus.FAILED.value
    ):
        return
    previous_line = await db.scalar(
        select(MemoirChatMessage.id)
        .where(
            MemoirChatMessage.voice_session_id == voice_session.id,
            MemoirChatMessage.role == MemoirChatMessageRole.ASSISTANT.value,
            MemoirChatMessage.turn_index < message.turn_index,
        )
        .order_by(MemoirChatMessage.turn_index.desc())
        .limit(1)
    )
    await editing_jobs.enqueue_editing_job(
        db,
        project_id=project.id,
        job_type=MemoirEditingJobType.FILL_NARRATOR_PROFILE,
        payload_json={
            "utterance_id": utterance.id,
            "transcript_version_id": version_id,
            "previous_message_id": previous_line,
        },
        dedupe_key=dedupe_key,
        replace_completed=True,
        reschedule_running=True,
    )


async def run_narrator_profile_job(db: AsyncSession, job: MemoirEditingJob) -> editing_jobs.EditingJobResult:
    payload = job.payload_json or {}
    utterance = await _load_utterance(db, payload.get("utterance_id"))
    project = await db.get(Project, job.project_id)
    if utterance is None or utterance.chat_message is None or project is None:
        return editing_jobs.EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="讲述片段或回忆录不存在",
        )
    if not await fill_narrator_profile(
        db,
        project=project,
        utterance=utterance,
        transcript_version_id=str(payload.get("transcript_version_id") or ""),
        previous_message_id=payload.get("previous_message_id"),
    ):
        return editing_jobs.EditingJobResult(
            status=MemoirEditingJobStatus.SKIPPED,
            error_message="转写版本已更新",
        )
    return editing_jobs.EditingJobResult(result_json={"utterance_id": utterance.id})


async def _load_utterance(db: AsyncSession, utterance_id: object) -> MemoirVoiceUtterance | None:
    if not isinstance(utterance_id, str):
        return None
    return await db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == utterance_id)
        .options(selectinload(MemoirVoiceUtterance.chat_message))
        .execution_options(populate_existing=True)
    )


def _known_profile(profile: dict) -> str:
    known = [f"{label}：{profile[key]}" for key, label in _LABELS.items() if not _is_empty(profile.get(key))]
    if not _is_empty(profile.get("narrator_gender")):
        known.append(f"性别：{profile['narrator_gender']}")
    return "；".join(known)


def build_profile_prompt(*, previous_line: str, segment: str, known: str, today: date) -> str:
    previous = f"AI：{previous_line}\n" if previous_line else ""
    return (
        f"今年是 {today.year} 年。下面是回忆录访谈里的一段：“AI”是访谈主持人，“叙述者”是讲述者本人。\n"
        f"已经记下的资料：{known or '还没有'}\n\n"
        f"{previous}叙述者：{segment}\n\n"
        "只看叙述者这一段，记下他本人这次新说出的资料，或者明确纠正已经记下的资料：\n"
        "- name：他希望怎么被称呼；说了全名就用全名，否则用他说的称呼，比如“老王”\n"
        "- gender：male 或 female，只在他讲的内容能明确看出时填写\n"
        "- age：周岁；他说的是出生年份就按今年算\n"
        "- origin：老家或籍贯，写成地名，比如“四川成都”\n"
        "- skip：name、age、origin 里他这段明确说不想说的；他说想自己讲、不想被问，三项都填\n"
        "只记他自己的情况，家人的名字、年龄、老家不算。和已经记下的一样，或者这段没说到的，都填 null。\n"
        '只返回 JSON：{"name":null,"gender":null,"age":null,"origin":null,"skip":[]}'
    )


async def fill_narrator_profile(
    db: AsyncSession,
    *,
    project: Project,
    utterance: MemoirVoiceUtterance,
    transcript_version_id: str,
    previous_message_id: str | None,
) -> bool:
    """Read one segment and merge what it adds. Returns False when its transcript has changed since."""
    message = utterance.chat_message
    if (message.transcript_version_id or "") != transcript_version_id:
        return False
    segment = message.content_text.strip()
    previous = await db.get(MemoirChatMessage, previous_message_id) if isinstance(previous_message_id, str) else None
    ai_model = resolve_project_ai_model(project, None)
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    prompt = build_profile_prompt(
        previous_line=previous.content_text.strip() if previous else "",
        segment=segment,
        known=_known_profile(_profile(project)),
        today=date.today(),
    )
    turn_index = message.turn_index
    await db.commit()
    # A failure propagates so the job is marked failed; the segment is read again when its transcript changes.
    generated = await generate_structured_with_client(
        get_ai_service(ai_model),
        SYSTEM_PROMPT,
        prompt,
        output_model=_ProfileOutput,
        requested_model=ai_model,
        temperature=0.1,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    # Serialize the read/merge/write with manual saves and other segments after the provider call.
    await db.refresh(project, with_for_update=True)
    current = await _load_utterance(db, utterance.id)
    if current is None or current.chat_message is None or (
        (current.chat_message.transcript_version_id or "") != transcript_version_id
    ):
        return False
    profile = _profile(project)
    inferred = dict(profile.get(INFERRED_PROFILE_FIELDS_KEY) or {})
    declined = dict(profile.get(SKIPPED_PROFILE_FIELDS_KEY) or {})
    output = generated.value
    found = {
        "narrator_name": _clean(output.name, limit=40),
        "narrator_gender": output.gender,
        "narrator_age": output.age if output.age is not None and 0 < output.age <= 120 else None,
        "narrator_origin": _clean(output.origin, limit=40),
    }

    def writable(key: str) -> bool:
        if _is_empty(profile.get(key)):
            return True
        source = inferred.get(key)
        # A heard value may be replaced by the same segment or a later one; a saved value never.
        return isinstance(source, dict) and int(source.get("turn_index", -1)) <= turn_index

    changed = {
        key: value
        for key, value in found.items()
        if not _is_empty(value) and value != profile.get(key) and writable(key)
    }
    newly_declined = {
        _SKIP_FIELDS[item]: utterance.id
        for item in output.skip
        if _SKIP_FIELDS[item] not in declined
        and _SKIP_FIELDS[item] not in changed
        and _is_empty(profile.get(_SKIP_FIELDS[item]))
    }
    if not changed and not newly_declined:
        await db.commit()
        return True
    previous_name = str(profile.get("narrator_name") or "")
    source = {"utterance_id": utterance.id, "transcript_version_id": transcript_version_id, "turn_index": turn_index}
    profile.update(changed)
    profile[INFERRED_PROFILE_FIELDS_KEY] = {**inferred, **{key: source for key in changed}}
    if newly_declined:
        profile[SKIPPED_PROFILE_FIELDS_KEY] = {**declined, **newly_declined}
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
    return True

