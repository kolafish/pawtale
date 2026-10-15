"""Fill the narrator's profile from what they say in AI interviews.

A new book has no profile form. The book's first voice session where the narrator speaks becomes its
profile session: only there does the interviewer ask for 称呼, age, hometown and gender. In every AI interview,
each narrator segment (one recording) is read with the interviewer's line before it and what is already
known, for anything the narrator newly says or corrects about themselves. A segment is read again
when its transcript changes. Asking stops after two asks or a refusal; reading does not, so a late
answer is still kept. A value the user saved on the project page is never overwritten.
The same job reads the people in a corrected segment (see narration_people).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import date, timedelta
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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


logger = logging.getLogger(__name__)

# Each item is asked at most this many times; an answer given later is still kept.
MAX_ASKS = 2
# A failed reading is tried again after these delays, then left failed.
RETRY_DELAYS = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10))
SYSTEM_PROMPT = "你从回忆录访谈里记下讲述者本人的基本资料，只记他说出来或能明确看出的内容。"
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
            answering = _asked(message) if spoke else []
            break
        spoke = spoke or bool(message.content_text.strip())
    return tuple(
        key for key in profile_items_to_ask(profile) if asks.get(key, 0) < MAX_ASKS and key not in answering
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


async def schedule_narrator_profile(db: AsyncSession, *, utterance: MemoirVoiceUtterance) -> None:
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
        _has_open_fields(profile) or narration_people.ready(utterance, message)
    ):
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
    version_id = str(payload.get("transcript_version_id") or "")
    try:
        current = not _has_open_fields(_profile(project)) or await fill_narrator_profile(
            db,
            project=project,
            utterance=utterance,
            transcript_version_id=version_id,
            previous_message_id=payload.get("previous_message_id"),
        )
        if current and narration_people.ready(utterance, utterance.chat_message):
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


def _known_profile(profile: dict) -> str:
    known = [f"{label}：{profile[key]}" for key, label in _LABELS.items() if not _is_empty(profile.get(key))]
    return "；".join(known)


def build_profile_prompt(*, previous_line: str, segment: str, known: str, today: date) -> str:
    previous = f"AI：{previous_line}\n" if previous_line else ""
    return (
        f"今年是 {today.year} 年。下面是回忆录访谈里的一段：“AI”是访谈主持人，“叙述者”是讲述者本人。\n"
        f"已经记下的资料：{known or '还没有'}\n\n"
        f"{previous}叙述者：{segment}\n\n"
        "只看叙述者这一段，记下他本人这次新说出的资料，或者明确纠正已经记下的资料：\n"
        "- name：他希望怎么被称呼；说了全名就用全名，否则用他说的称呼，比如“老王”。"
        "回答“该叫叔叔还是阿姨”这类问题，只是在说性别，name 填 null\n"
        "- gender：male 或 female；他说自己是男的、女的，用“我们女孩子”“我当姑娘那会儿”这样的话说自己，"
        "或者自称、让人称呼自己为大姐、女士、阿姨、奶奶等，可记 female；男的、“我们男娃”、大哥、先生、叔叔、爷爷等"
        "可记 male。不能只凭普通姓名猜，家人的称呼不算\n"
        "- age：周岁；他说的是出生年份就按今年算\n"
        "- origin：本产品记录的是家乡，不要求他明确说出“老家”或“籍贯”二字。"
        "老家、籍贯、本人小时候住的地方或长大的地方都算。还不知道家乡时，"
        "他说“我小时候住在X”或“我在X长大”，origin 必须填写地名 X，不能因为没说籍贯而填 null，"
        "保留他说出的省、市、县。临时住处、工作地和家人的老家不算。"
        "已经记下家乡时，提到其他居住地不算纠正\n"
        "- skip：name、gender、age、origin 里他这段明确说不想说的。他说想自己讲、不想被问、别打断他，四项都填；"
        "只是不答上一句问的那一项，接着说“咱接着讲”“说点别的”，只填那一项\n"
        "只记他自己的情况，家人的名字、年龄、老家不算。和已经记下的一样，或者这段没说到的，都填 null。\n"
        "只返回包含 name、gender、age、origin、skip 五个字段的 JSON。"
        "name 和 origin 是字符串或 null，gender 是 male、female 或 null，age 是整数或 null，skip 是数组。"
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
    segment_created_at = utterance.created_at
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
    source_ids = {
        source["utterance_id"] for source in inferred.values()
        if isinstance(source, dict) and source.get("utterance_id")
    }
    source_times = dict((await db.execute(
        select(MemoirVoiceUtterance.id, MemoirVoiceUtterance.created_at)
        .where(MemoirVoiceUtterance.id.in_(source_ids))
    )).all()) if source_ids else {}
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
        if not isinstance(source, dict):
            return False
        source_time = source_times.get(source.get("utterance_id"))
        # Deleting the source recording must not turn a heard value into a manually saved one.
        return source_time is None or source_time <= segment_created_at

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

