"""Fill the narrator's profile from what they say in AI interviews.

A new book has no profile form. The book's first voice session where the narrator speaks becomes its
profile session: there, and in the next session if items are left, the interviewer asks for 称呼, age,
hometown and gender. In every AI interview, each narrator segment (one recording) is read with the
interviewer's line before it and what is already known, for anything the narrator newly says or corrects
about themselves. A segment is read again when its transcript changes. Asking stops after two asks (one
for gender) or a refusal; reading does not, so a late answer is still kept. A value the user saved on the
project page is never overwritten.
Each message retains its versioned reading, even when a later segment supplies the same value. Retracting
the selected source restores the newest remaining support; only a field with no support is cleared.
The same job reads the people in a corrected segment (see narration_people).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field as dataclass_field
from datetime import date, timedelta
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.application_errors import InsufficientAICreditError
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
from app.clock import utcnow_naive
from app.database import async_session_maker
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import AITextTimeoutProfile, ai_text_timeout_seconds
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir import book_outputs, editing_jobs, narration_people
from app.services.memoir.project_ai_preferences import resolve_project_ai_model
from app.services.memoir.projects import (
    FIRST_PROJECT_TITLE,
    INFERRED_PROFILE_FIELDS_KEY,
    MEMOIR_PROFILE_METADATA_KEY,
    PROFILE_SESSION_KEY,
    SKIPPED_PROFILE_FIELDS_KEY,
    profile_items_to_ask,
)
from app.services.memoir.transcript_projection import normalized_material_text, source_text_hash


logger = logging.getLogger(__name__)

# Each item is asked at most this many times; an answer given later is still kept.
MAX_ASKS = 2
# Gender is asked once and otherwise left to what the narrator says.
_ASK_LIMITS = {"narrator_gender": 1}
# A failed reading is tried again after these delays, then left failed.
RETRY_DELAYS = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10))
SYSTEM_PROMPT = "你从回忆录访谈里记下讲述者本人的基本资料，只记他说出来或能明确看出的内容。"
READING_KEY = "profile_reading"
_LABELS = {"narrator_name": "称呼", "narrator_age": "年龄", "narrator_origin": "老家", "narrator_gender": "性别"}
_SKIP_FIELDS = {
    "name": "narrator_name", "gender": "narrator_gender", "age": "narrator_age", "origin": "narrator_origin",
}
_ASK_TOPICS = {
    "narrator_name": "他本人怎么称呼、叫什么名字",
    "narrator_age": "他本人现在的年纪，或者哪年出生",
    "narrator_origin": "他本人的老家、籍贯或是哪里人",
    "narrator_gender": "他本人是男是女，比如该叫他叔叔还是阿姨",
}
# Only when the check call fails: wording that plainly asks for each item.
_ASK_PATTERNS = {
    "narrator_name": re.compile(r"怎么称呼|贵姓|叫什么名字|您的名字"),
    "narrator_age": re.compile(r"多少岁|几岁|多大岁数|多大年纪|年纪多大|高寿|贵庚|哪一?年(出)?生|出生年|属什么|属相"),
    "narrator_origin": re.compile(r"老家|哪里人|哪儿人|家乡|籍贯|祖籍"),
    "narrator_gender": re.compile(r"性别|(男的|女的|男士|女士|叔叔|阿姨|大哥|大姐|爷爷|奶奶)还是"),
}


class _AskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["narrator_name", "narrator_age", "narrator_origin", "narrator_gender"] | None


class _ProfileOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    gender: Literal["male", "female"] | None = None
    age: int | None = None
    origin: str | None = None
    skip: list[Literal["name", "gender", "age", "origin"]] = []


class _ProfileReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2] = 2
    transcript_version_id: str
    output: _ProfileOutput
    source_text_hash: str | None = None


def _reading(message: MemoirChatMessage) -> _ProfileReading | None:
    stored = (message.metadata_json or {}).get(READING_KEY)
    if not isinstance(stored, dict):
        return None
    try:
        reading = _ProfileReading.model_validate(stored)
    except ValidationError:
        return None
    version_id = message.transcript_version_id or ""
    if reading.transcript_version_id == version_id:
        return reading
    if reading.schema_version == 2 and reading.source_text_hash == source_text_hash(normalized_material_text(message.content_text)):
        return reading.model_copy(update={"transcript_version_id": version_id})
    return None


def _profile_values(output: _ProfileOutput) -> dict[str, str | int | None]:
    return {
        "narrator_name": _clean(output.name, limit=40),
        "narrator_gender": output.gender,
        "narrator_age": output.age if output.age is not None and 0 < output.age <= 120 else None,
        "narrator_origin": _clean(output.origin, limit=40),
    }


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


async def _project_has_narration_elsewhere(
    db: AsyncSession, voice_session: MemoirVoiceSession, *, besides: str | None = None,
) -> bool:
    return bool(await db.scalar(
        select(1)
        .select_from(MemoirChatMessage)
        .where(
            MemoirChatMessage.project_id == voice_session.project_id,
            MemoirChatMessage.voice_session_id.not_in({voice_session.id, besides or voice_session.id}),
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
            MemoirChatMessage.content_text != "",
        )
        .limit(1)
    ))


async def is_profile_session(db: AsyncSession, voice_session: MemoirVoiceSession, project: Project | None) -> bool:
    """Whether this session asks for the profile: the book's first session where the narrator spoke, or the one
    after it, so a short first session does not end the asking. Before the narrator speaks, whether it will."""
    if project is None or not _collects_profile(voice_session):
        return False
    pinned = _profile(project).get(PROFILE_SESSION_KEY)
    return pinned == voice_session.id or not await _project_has_narration_elsewhere(
        db, voice_session, besides=pinned,
    )


@dataclass(frozen=True, slots=True)
class ProfileAnswer:
    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class ProfileQuestionContext:
    questions: tuple[str, ...] = ()
    previous_answers: tuple[ProfileAnswer, ...] = ()


async def profile_question_context_for(
    db: AsyncSession, voice_session: MemoirVoiceSession, project: Project | None,
) -> ProfileQuestionContext:
    """The item this session's next reply asks for, and its fallback. Asks in the book's other sessions count
    toward the cap, including an opening ask the narrator left without answering. Profile exchanges from
    both this and earlier sessions survive the recent-dialogue window while their reading is pending."""
    if not await is_profile_session(db, voice_session, project) or not profile_items_to_ask(_profile(project)):
        return ProfileQuestionContext()
    earlier = list((await db.scalars(
        select(MemoirChatMessage)
        .where(
            MemoirChatMessage.project_id == voice_session.project_id,
            MemoirChatMessage.voice_session_id != voice_session.id,
        )
        .order_by(MemoirChatMessage.created_at, MemoirChatMessage.turn_index)
    )).all())
    messages = earlier + list(voice_session.messages)
    questions = profile_questions_to_ask(_profile(project), messages)
    messages.sort(key=lambda message: (message.created_at, message.turn_index))
    return ProfileQuestionContext(questions, _previous_profile_answers(messages) if questions else ())


def _previous_profile_answers(messages: list[MemoirChatMessage]) -> tuple[ProfileAnswer, ...]:
    """Keep complete profile exchanges, never pairing a silent opening with another session's answer.

    No-answer reminders continue the preceding question; other interviewer lines replace it.
    At most seven asks are allowed (two per item, one for gender). Keep that many exchanges rather than
    a tail of all messages, so unrelated stories or silent openings cannot displace the answers.
    """
    previous: dict[str, MemoirChatMessage] = {}
    exchanges: dict[str, tuple[MemoirChatMessage, list[str]]] = {}
    for message in messages:
        if message.role == MemoirChatMessageRole.ASSISTANT.value:
            if message.guidance_intent != "no_answer_prompt":
                previous[message.voice_session_id] = message
        elif message.role == MemoirChatMessageRole.USER.value and message.content_text.strip():
            question = previous.get(message.voice_session_id)
            if question is not None and any(field in _LABELS for field in _asked(question)):
                _, answers = exchanges.setdefault(question.id, (question, []))
                answers.append(message.content_text.strip())
    limit = sum(_ASK_LIMITS.get(field, MAX_ASKS) for field in _LABELS)
    return tuple(
        ProfileAnswer(question.content_text.strip(), "\n".join(answers))
        for question, answers in list(exchanges.values())[-limit:]
    )


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
    return any(_is_empty(profile.get(key)) or key in inferred for key in _LABELS)


def _asked(message: MemoirChatMessage) -> list[str]:
    """Items an interviewer line asked for; until its check finishes, every item it was offered."""
    metadata = message.metadata_json or {}
    if "profile_asked" in metadata:
        return [metadata["profile_asked"]]
    return metadata.get("profile_questions", [metadata.get("profile_question")])


def profile_asks(messages: list[MemoirChatMessage]) -> dict[str, int]:
    """How many interviewer lines in a session asked for each item.

    A line whose check is still running counts as an ask, so the cap holds however late the check finishes.
    """
    counts: dict[str, int] = {}
    for message in messages:
        if message.role != MemoirChatMessageRole.ASSISTANT.value:
            continue
        for field in _asked(message):
            if field in _LABELS:
                counts[field] = counts.get(field, 0) + 1
    return counts


def profile_questions_to_ask(profile: dict, messages: list[MemoirChatMessage]) -> tuple[str, ...]:
    """The item the next reply asks for, then the one it asks instead when the narrator has just given the
    first; messages are in turn order. Answers may not be saved yet.

    The item the latest interviewer line asked is skipped once the narrator has spoken since. If they did not
    answer it, that line asked nothing and it is offered again.
    """
    asks = profile_asks(messages)
    spoke = False
    answering: list[str] = []
    for message in reversed(messages):
        if message.role == MemoirChatMessageRole.ASSISTANT.value:
            if message.guidance_intent == "no_answer_prompt":
                continue
            answering = _asked(message) if spoke else []
            break
        spoke = spoke or bool(message.content_text.strip())
    return tuple(
        key for key in profile_items_to_ask(profile)
        if asks.get(key, 0) < _ASK_LIMITS.get(key, MAX_ASKS) and key not in answering
    )[:2]


def ask_needs_check(fields: tuple[str, ...], text: str) -> bool:
    """Whether a saved interviewer line may have asked for the offered item; only a question can."""
    return bool(fields) and bool(re.search(r"[？?]", text))


_ask_checks: set[asyncio.Task] = set()


def run_ask_check_later(check: Coroutine[Any, Any, None]) -> None:
    """Run a check on a saved interviewer line apart from the reply turn, so a narrator who starts talking
    right away cannot cancel it."""
    task = asyncio.create_task(check)
    _ask_checks.add(task)
    task.add_done_callback(_ask_checks.discard)


def check_profile_ask_later(*, message_id: str, fields: tuple[str, ...], text: str, ai_model: str | None) -> None:
    """Record on a saved interviewer line whether it asked for the offered item."""
    run_ask_check_later(_record_profile_ask(message_id=message_id, fields=fields, text=text, ai_model=ai_model))


async def wait_for_profile_ask_checks() -> None:
    await asyncio.gather(*list(_ask_checks), return_exceptions=True)


async def _record_profile_ask(*, message_id: str, fields: tuple[str, ...], text: str, ai_model: str | None) -> None:
    try:
        asked = await asked_profile_field(fields, text, ai_model=ai_model)
        async with async_session_maker() as db:
            message = await db.get(MemoirChatMessage, message_id)
            if message is None:
                return
            message.metadata_json = {**(message.metadata_json or {}), "profile_asked": asked}
            db.add(message)
            await db.commit()
    except Exception:
        # The line keeps counting as an ask.
        logger.warning("Recording the profile ask failed for message %s", message_id, exc_info=True)


async def asked_profile_field(fields: tuple[str, ...], text: str, *, ai_model: str | None) -> str | None:
    """The offered item, if this interviewer line asks the narrator for it."""
    if not ask_needs_check(fields, text):
        return None
    topics = "\n".join(f"- {field}：{_ASK_TOPICS[field]}" for field in fields)
    prompt = (
        f"访谈主持人对讲述者说了这句话：\n{text}\n\n"
        f"这句话问的是下面哪一项？\n{topics}\n"
        "只选实际问到的一项。问过去某个时候的事、问家人的情况、问别的事，都不算。"
        '只返回 JSON：{"field":"narrator_age"}（用选中的字段名）或 {"field":null}'
    )
    try:
        generated = await generate_structured_with_client(
            get_ai_service(ai_model),
            "你判断访谈主持人的一句话问的是什么。",
            prompt,
            output_model=_AskOutput,
            requested_model=ai_model,
            temperature=0,
            max_tokens=20,
            timeout=min(ai_text_timeout_seconds(AITextTimeoutProfile.INTERACTIVE), 15),
            max_attempts=1,
        )
        asked = generated.value.field
    except Exception:
        logger.warning("Profile ask check failed; falling back to wording", exc_info=True)
        asked = next((field for field in fields if _ASK_PATTERNS[field].search(text)), None)
    return asked if asked in fields else None


async def schedule_narrator_profile(db: AsyncSession, *, utterance: MemoirVoiceUtterance,
                                   require_answer_reading: bool = False) -> None:
    """Queue reading each AI narration segment once per transcript version, even in later sessions."""
    # Callers load the utterance differently; read its message by id rather than through the relationship.
    message = await db.get(MemoirChatMessage, utterance.chat_message_id) if utterance.chat_message_id else None
    if message is None or message.role != MemoirChatMessageRole.USER.value or not message.content_text.strip():
        return
    voice_session = await db.get(MemoirVoiceSession, utterance.voice_session_id)
    project = await db.get(Project, voice_session.project_id) if voice_session else None
    if voice_session is None or project is None:
        return
    profile = _profile(project)
    if not _collects_profile(voice_session) or not (
        _has_open_fields(profile) or narration_people.ready(utterance, message) or require_answer_reading
    ):
        return
    version_id = message.transcript_version_id or ""
    dedupe_key = f"narrator_profile:{utterance.id}"
    existing = await db.scalar(select(MemoirEditingJob).where(MemoirEditingJob.dedupe_key == dedupe_key))
    if (
        existing is not None
        and (existing.payload_json or {}).get("transcript_version_id") == version_id
        and existing.status != MemoirEditingJobStatus.FAILED.value
        and (not require_answer_reading or (existing.status in editing_jobs.ACTIVE_EDITING_JOB_STATUSES
             and (existing.payload_json or {}).get("require_answer_reading") is True)
             or narration_people.answer_story_content(message) is not None)
    ):
        return
    previous_line = await _previous_line_id(db, message)
    await editing_jobs.enqueue_editing_job(
        db,
        project_id=project.id,
        job_type=MemoirEditingJobType.FILL_NARRATOR_PROFILE,
        payload_json={
            "utterance_id": utterance.id,
            "transcript_version_id": version_id,
            "previous_message_id": previous_line,
            **({"require_answer_reading": True} if require_answer_reading else {}),
        },
        dedupe_key=dedupe_key,
        replace_completed=True,
        reschedule_running=True,
    )


async def _previous_line_id(db: AsyncSession, message: MemoirChatMessage) -> str | None:
    return await db.scalar(
        select(MemoirChatMessage.id)
        .where(
            MemoirChatMessage.voice_session_id == message.voice_session_id,
            MemoirChatMessage.role == MemoirChatMessageRole.ASSISTANT.value,
            MemoirChatMessage.guidance_intent.is_distinct_from("no_answer_prompt"),
            MemoirChatMessage.turn_index < message.turn_index,
        )
        .order_by(MemoirChatMessage.turn_index.desc())
        .limit(1)
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
    version_id = str(payload.get("transcript_version_id") or "")
    correction = await db.scalar(select(MemoirEditingJob.id).where(
        MemoirEditingJob.dedupe_key == f"correct_transcript:{utterance.id}",
        MemoirEditingJob.status.in_(editing_jobs.ACTIVE_EDITING_JOB_STATUSES),
    ))
    if correction and not narration_people.ready(utterance, utterance.chat_message):
        # The correction completion coalesces its final version into this same job.
        job.run_after = utcnow_naive() + timedelta(seconds=1)
        return editing_jobs.EditingJobResult(status=MemoirEditingJobStatus.PENDING,
            result_json={"awaiting_transcript_correction": True})
    try:
        current = not _has_open_fields(_profile(project)) or await fill_narrator_profile(
            db,
            project=project,
            utterance=utterance,
            transcript_version_id=version_id,
            previous_message_id=payload.get("previous_message_id"),
        )
        if current is None:
            # A dependency changed without necessarily queuing its own profile job (e.g. a writing
            # correction). Continue this same job; incomplete evidence is not a provider failure.
            await db.execute(
                update(MemoirEditingJob)
                .where(MemoirEditingJob.id == job.id)
                .values(rerun_requested=True, run_after=utcnow_naive())
            )
            return editing_jobs.EditingJobResult(result_json={"awaiting_profile_sources": True})
        if current and (narration_people.ready(utterance, utterance.chat_message)
                        or payload.get("require_answer_reading") is True):
            previous = payload.get("previous_message_id")
            previous_message = await db.get(MemoirChatMessage, previous) if isinstance(previous, str) else None
            current = await narration_people.read_people(
                db,
                project=project,
                narrator=str(_profile(project).get("narrator_name") or ""),
                message=utterance.chat_message,
                previous_line=previous_message.content_text.strip() if previous_message else "",
                transcript_version_id=version_id,
            )
            if current and narration_people.answer_story_content(utterance.chat_message) is not None:
                from app.services.memoir.story_tree_growth import schedule_story_tree_growth
                await schedule_story_tree_growth(db, project_id=project.id,
                    voice_session_id=utterance.voice_session_id, source="person_answer_read")
    except InsufficientAICreditError:
        raise
    except Exception as exc:
        await db.rollback()
        queued = await db.scalar(select(MemoirEditingJob.payload_json).where(MemoirEditingJob.id == job.id))
        if (queued or {}).get("transcript_version_id") != payload.get("transcript_version_id"):
            # A newer transcript was queued while this one failed; finish so the queue runs it with fresh retries.
            logger.warning("Narrator profile reading failed for job %s; a newer version is queued", job.id, exc_info=True)
            return editing_jobs.EditingJobResult(result_json={"superseded": True})
        failures = int((job.result_json or {}).get("failures") or 0)
        if failures >= len(RETRY_DELAYS):
            raise
        logger.warning("Narrator profile reading failed for job %s; retrying", job.id, exc_info=True)
        # Ask the queue to run this job again later; it keeps any newer transcript version queued meanwhile.
        await db.execute(
            update(MemoirEditingJob)
            .where(MemoirEditingJob.id == job.id)
            .values(rerun_requested=True, run_after=utcnow_naive() + RETRY_DELAYS[failures])
        )
        return editing_jobs.EditingJobResult(result_json={"failures": failures + 1, "last_error": str(exc)[:200]})
    # When the transcript changed meanwhile, its new version is queued as a rerun, which only follows a success.
    return editing_jobs.EditingJobResult(
        result_json={"utterance_id": utterance.id, **({} if current else {"superseded": True})}
    )


async def _load_utterance(db: AsyncSession, utterance_id: object) -> MemoirVoiceUtterance | None:
    if not isinstance(utterance_id, str):
        return None
    return await db.scalar(
        select(MemoirVoiceUtterance)
        .where(MemoirVoiceUtterance.id == utterance_id)
        .options(selectinload(MemoirVoiceUtterance.chat_message))
        .execution_options(populate_existing=True)
    )


def _known_profile(profile: dict, *, excluding_utterance_id: str) -> str:
    inferred = profile.get(INFERRED_PROFILE_FIELDS_KEY) or {}
    known = []
    for key, label in _LABELS.items():
        source = inferred.get(key)
        if _is_empty(profile.get(key)) or (
            isinstance(source, dict) and source.get("utterance_id") == excluding_utterance_id
        ):
            continue
        known.append(f"{label}：{profile[key]}")
    return "；".join(known)


def build_profile_prompt(*, previous_line: str, segment: str, known: str, today: date) -> str:
    previous = f"AI：{previous_line}\n" if previous_line else ""
    return (
        f"今年是 {today.year} 年。下面是回忆录访谈里的一段：“AI”是访谈主持人，“叙述者”是讲述者本人。\n"
        f"已经记下的资料：{known or '还没有'}\n\n"
        f"{previous}叙述者：{segment}\n\n"
        "只看叙述者这一段，记下这一段支持的本人资料，包括再次确认的资料，或者明确纠正已经记下的资料：\n"
        "- name：他希望怎么被称呼；说了全名就用全名，否则用他说的称呼，比如“老王”。"
        "回答“该叫叔叔还是阿姨”这类问题，只是在说性别，name 填 null\n"
        "- gender：male 或 female；从用户讲述中提取性别，不要猜。\n"
        "- age：周岁；他说的是出生年份就按今年算\n"
        "- origin：本产品记录的是家乡，不要求他明确说出“老家”或“籍贯”二字。"
        "老家、籍贯、本人小时候住的地方或长大的地方都算。还不知道家乡时，"
        "他说“我小时候住在X”或“我在X长大”，origin 必须填写地名 X，不能因为没说籍贯而填 null，"
        "保留他说出的省、市、县。临时住处、工作地和家人的老家不算。"
        "已经记下家乡时，提到其他居住地不算纠正\n"
        "- skip：name、gender、age、origin 里他这段明确不想透露的本人资料。"
        "不想透露家人或他人的资料不算拒答本人资料，比如“母亲的年纪就不说了”应填空数组，"
        "即使 AI 上一句问的是他本人的年纪，也不能把这句变成本人的拒答。"
        "他说想自己讲、不想被问、别打断他，四项都填；"
        "只是不答上一句问的那一项，接着说“咱接着讲”“说点别的”，只填那一项\n"
        "只记他自己的情况，家人的名字、年龄、老家不算。和已经记下的一样也照填，只有这段没说到的才填 null。\n"
        "只返回包含 name、gender、age、origin、skip 五个字段的 JSON。"
        "name 和 origin 是字符串或 null，gender 是 male、female 或 null，age 是整数或 null，skip 是数组。"
    )


async def _extract_profile(
    db: AsyncSession,
    *,
    project: Project,
    utterance: MemoirVoiceUtterance,
    previous_message_id: str | None,
) -> _ProfileReading:
    message = utterance.chat_message
    version_id = message.transcript_version_id or ""
    segment = message.content_text.strip()
    previous = await db.get(MemoirChatMessage, previous_message_id) if isinstance(previous_message_id, str) else None
    ai_model = resolve_project_ai_model(project, None)
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    prompt = build_profile_prompt(
        previous_line=previous.content_text.strip() if previous else "",
        segment=segment,
        # Re-extract this segment without letting its superseded values bias the reading.
        known=_known_profile(_profile(project), excluding_utterance_id=utterance.id),
        today=date.today(),
    )
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
    return _ProfileReading(transcript_version_id=version_id, output=generated.value,
        source_text_hash=source_text_hash(normalized_material_text(segment)))


def _other_segments(project_id: str, utterance_id: str) -> Select[tuple[MemoirVoiceUtterance]]:
    return (
        select(MemoirVoiceUtterance)
        .join(MemoirChatMessage, MemoirVoiceUtterance.chat_message_id == MemoirChatMessage.id)
        .join(MemoirVoiceSession, MemoirVoiceUtterance.voice_session_id == MemoirVoiceSession.id)
        .where(
            MemoirChatMessage.project_id == project_id,
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
            func.trim(MemoirChatMessage.content_text) != "",
            MemoirVoiceSession.interview_type == MemoirInterviewType.AI_INTERVIEW.value,
            MemoirVoiceSession.session_goal == MemoirSessionGoal.OPEN_NARRATION.value,
            MemoirVoiceUtterance.id != utterance_id,
        )
        .options(contains_eager(MemoirVoiceUtterance.chat_message))
        .order_by(MemoirVoiceUtterance.created_at.desc(), MemoirVoiceUtterance.id.desc())
        .execution_options(populate_existing=True)
    )


@dataclass
class _ProfileRecovery:
    values: dict[str, str | int] = dataclass_field(default_factory=dict)
    inferred: dict[str, dict[str, str | int]] = dataclass_field(default_factory=dict)
    declined: dict[str, str] = dataclass_field(default_factory=dict)
    pending: MemoirVoiceUtterance | None = None


async def _profile_recovery(
    db: AsyncSession, *, project_id: str, excluding: str, values: set[str], declined: set[str],
) -> _ProfileRecovery:
    """Resolve only the needed fields, newest first, while their messages are locked.

    A missing/currently stale reading is unknown, not negative evidence. Stop at the first such
    dependency, or as soon as all fields have support; only exhausting valid readings permits clearing.
    """
    result = _ProfileRecovery()
    if not values and not declined:
        return result
    for segment in await db.scalars(_other_segments(project_id, excluding).with_for_update(
        of=(MemoirVoiceUtterance, MemoirChatMessage),
    )):
        reading = _reading(segment.chat_message)
        if reading is None:
            result.pending = segment
            return result
        for key, value in _profile_values(reading.output).items():
            if key in values and not _is_empty(value):
                result.values[key] = value
                result.inferred[key] = {
                    "utterance_id": segment.id,
                    "transcript_version_id": reading.transcript_version_id,
                    "turn_index": segment.chat_message.turn_index,
                }
                values.remove(key)
        for item in reading.output.skip:
            key = _SKIP_FIELDS[item]
            if key in declined:
                result.declined[key] = segment.id
                declined.remove(key)
        if not values and not declined:
            break
    return result


async def fill_narrator_profile(
    db: AsyncSession,
    *,
    project: Project,
    utterance: MemoirVoiceUtterance,
    transcript_version_id: str,
    previous_message_id: str | None,
) -> bool | None:
    """True: applied; False: own transcript superseded; None: recovery needs a continuation."""
    if (utterance.chat_message.transcript_version_id or "") != transcript_version_id:
        return False
    reading = _reading(utterance.chat_message) or await _extract_profile(
        db, project=project, utterance=utterance, previous_message_id=previous_message_id,
    )
    output = reading.output
    found = _profile_values(output)
    attempted: set[str] = set()
    while True:
        # Recompute ownership and recovery after every provider call. The final selection and profile
        # write share the same locks, so a previously valid reading cannot silently turn into a gap.
        await db.refresh(project, with_for_update=True)
        current = await _load_utterance(db, utterance.id)
        if current is None or current.chat_message is None:
            await db.commit()
            return False
        message = current.chat_message
        await db.refresh(message, with_for_update=True)
        if (message.transcript_version_id or "") != transcript_version_id:
            await db.commit()
            return False
        message.metadata_json = {**(message.metadata_json or {}), READING_KEY: reading.model_dump()}
        profile = _profile(project)
        recovery = await _profile_recovery(
            db, project_id=project.id, excluding=utterance.id,
            values={
                key for key, owner in (profile.get(INFERRED_PROFILE_FIELDS_KEY) or {}).items()
                if isinstance(owner, dict) and owner.get("utterance_id") == utterance.id and _is_empty(found.get(key))
            },
            declined={
                key for key, owner in (profile.get(SKIPPED_PROFILE_FIELDS_KEY) or {}).items()
                if owner == utterance.id and key not in {_SKIP_FIELDS[item] for item in output.skip}
            },
        )
        pending = recovery.pending
        if pending is None:
            break
        # Persist the current extraction and release every lock before reading a dependency. A rerun
        # reuses successful readings, and each dependency is attempted at most once per invocation.
        await db.commit()
        if pending.id in attempted:
            return None
        attempted.add(pending.id)
        recovered = await _extract_profile(
            db, project=project, utterance=pending,
            previous_message_id=await _previous_line_id(db, pending.chat_message),
        )
        pending = await _load_utterance(db, pending.id)
        if pending is not None and pending.chat_message is not None:
            pending_message = pending.chat_message
            await db.refresh(pending_message, with_for_update=True)
            if _reading(pending_message) is None:
                if (pending_message.transcript_version_id or "") != recovered.transcript_version_id:
                    await db.commit()
                    return None
                pending_message.metadata_json = {
                    **(pending_message.metadata_json or {}), READING_KEY: recovered.model_dump(),
                }
        await db.commit()
    turn_index = message.turn_index
    segment_created_at = current.created_at
    profile = _profile(project)
    inferred = dict(profile.get(INFERRED_PROFILE_FIELDS_KEY) or {})
    declined = dict(profile.get(SKIPPED_PROFILE_FIELDS_KEY) or {})
    source_ids = {
        source["utterance_id"] for source in inferred.values()
        if isinstance(source, dict) and source.get("utterance_id")
    }
    source_ids.update(source for source in declined.values() if isinstance(source, str))
    source_times = dict((await db.execute(
        select(MemoirVoiceUtterance.id, MemoirVoiceUtterance.created_at)
        .where(MemoirVoiceUtterance.id.in_(source_ids))
    )).all()) if source_ids else {}

    def writable(key: str) -> bool:
        if _is_empty(profile.get(key)):
            return True
        source = inferred.get(key)
        # A heard value may be replaced by the same segment or a later one; a saved value never.
        if not isinstance(source, dict):
            return False
        source_time = source_times.get(source.get("utterance_id"))
        # Deleting the source recording must not turn a heard value into a manually saved one.
        return source_time is None or source_time <= segment_created_at

    source = {"utterance_id": utterance.id, "transcript_version_id": transcript_version_id, "turn_index": turn_index}
    own_fields = {
        key for key, previous_source in inferred.items()
        if isinstance(previous_source, dict) and previous_source.get("utterance_id") == utterance.id
    }
    removed = {key: None for key in own_fields if key in found and _is_empty(found[key])}
    changed = {
        key: value
        for key, value in found.items()
        if not _is_empty(value) and writable(key)
        and (value != profile.get(key) or inferred.get(key) != source)
    }
    # Only replace this segment's conclusions. Manual values and evidence owned by other segments survive.
    next_declined = {key: owner for key, owner in declined.items() if owner != utterance.id}
    for item in output.skip:
        key = _SKIP_FIELDS[item]
        owner_time = source_times.get(next_declined.get(key))
        if key not in changed and (key in removed or _is_empty(profile.get(key))) and (
            owner_time is None or owner_time <= segment_created_at
        ):
            next_declined[key] = utterance.id
    next_inferred = {
        **{key: owner for key, owner in inferred.items() if key not in removed},
        **{key: source for key in changed},
    }
    changed.update(recovery.values)
    for key in recovery.values:
        removed.pop(key)
    next_inferred.update(recovery.inferred)
    next_declined.update(recovery.declined)
    if not changed and not removed and next_declined == declined:
        await db.commit()
        return True
    previous_name = str(profile.get("narrator_name") or "")
    profile.update(removed)
    profile.update(changed)
    profile[INFERRED_PROFILE_FIELDS_KEY] = next_inferred
    profile[SKIPPED_PROFILE_FIELDS_KEY] = next_declined
    project.metadata_json = {**(project.metadata_json or {}), MEMOIR_PROFILE_METADATA_KEY: profile}
    name = changed.get("narrator_name")
    renamable = {FIRST_PROJECT_TITLE, f"{previous_name}的回忆录"} if previous_name else {FIRST_PROJECT_TITLE}
    if (name or "narrator_name" in removed) and project.title.strip() in renamable:
        project.title = f"{name}的回忆录" if name else FIRST_PROJECT_TITLE
        await book_outputs.synchronize_book_output_title(
            db,
            project_id=project.id,
            title=project.title,
            created_by=MemoirCreatedBy.AI,
        )
    db.add(project)
    await db.commit()
    return True

