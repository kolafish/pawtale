"""People the narrator talks about in AI interviews.

Each narrator segment is read once per corrected (or hand-edited) transcript version, inside the narrator
profile job. The reading is stored on the narrator message. The roster is never stored: it is folded from
the readings in narration order whenever it is needed, so a deleted or re-read segment needs no override
rules. A person keeps a stable id; the model sees P1, P2… and a new person gets "<message id>:<version>:<n>".
Once a story is told, 小余 may ask about one gap in someone the narrator keeps mentioning; the next reading
takes the answer from 小余's question and the narrator's reply.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import logging

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir.types import MemoirChatContentStatus, MemoirChatMessageRole, MemoirInterviewType
from app.models.memoir import MemoirChatMessage, MemoirVoiceSession, MemoirVoiceUtterance
from app.models.projects import Project
from app.database import async_session_maker
from app.services.ai_calls import generate_structured_with_client
from app.services.ai_factory import get_ai_service
from app.services.ai_timeouts import AITextTimeoutProfile, ai_text_timeout_seconds
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy
from app.services.memoir.project_ai_preferences import resolve_project_ai_model

logger = logging.getLogger(__name__)

READING_KEY = "people_reading"
SYSTEM_PROMPT = "你帮一本口述回忆录记住书里出现的人，只记讲述者亲口说出的内容。"
_FIELDS = ("call", "name", "relation", "note", "unclear")
_PRONOUNS = {"他", "她", "它", "ta", "TA"}
# Set on a 小余 question offered a person question, and dropped again if a check finds it asked something else;
# each person is asked once per book, at most twice per session.
ASKED_KEY = "person_asked"
MAX_ASKS_PER_SESSION = 2


class _PersonOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref: str = "new"
    same_as: str | None = None
    call: str | None = None
    name: str | None = None
    relation: str | None = None
    note: str | None = None
    unclear: str | None = None


class _ReadingOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    people: list[_PersonOutput] = []


@dataclass
class Person:
    id: str
    call: str = ""
    name: str = ""
    relation: str = ""
    note: str = ""
    unclear: str = ""
    message_ids: list[str] = field(default_factory=list)
    last_seen: int = 0

    @property
    def line(self) -> str:
        label = self.call or self.name
        name = f"（{self.name}）" if self.name and self.name != label else ""
        relation = f"，{self.relation}" if self.relation and self.relation != label else ""
        note = f"：{self.note}" if self.note else ""
        return f"{label}{name}{relation}{note}"


def ready(utterance: MemoirVoiceUtterance, message: MemoirChatMessage) -> bool:
    """People are read only from the version that will stay: corrected, or edited by hand."""
    return message.content_status == MemoirChatContentStatus.CORRECTED.value or bool(
        (utterance.transcript_edited or "").strip()
    )


def fold(readings: Iterable[tuple[str, dict]]) -> dict[str, Person]:
    """Merge (message id, reading) pairs in narration order; a later reading fills or corrects an earlier one."""
    people: dict[str, Person] = {}
    alias: dict[str, str] = {}
    for position, (message_id, reading) in enumerate(readings):
        for item in reading.get("people") or []:
            person_id = alias.get(item.get("id"), item.get("id"))
            same = alias.get(item.get("same_as"), item.get("same_as"))
            if not person_id:
                continue
            if same in people and same != person_id:
                # The narrator said two names are one person: the earlier card absorbs the other.
                merged = people.pop(person_id, None)
                if merged:
                    target = people[same]
                    for key in _FIELDS:
                        setattr(target, key, getattr(target, key) or getattr(merged, key))
                    target.message_ids += merged.message_ids
                alias[person_id] = same
                person_id = same
            person = people.get(person_id)
            if person is None:
                if not (item.get("call") or item.get("name")):
                    continue
                person = people[person_id] = Person(id=person_id)
            for key in _FIELDS:
                value = item.get(key)
                # A pronoun never replaces how the narrator already calls someone.
                if value and not (key == "call" and value in _PRONOUNS and person.call):
                    setattr(person, key, value)
            if message_id not in person.message_ids:
                person.message_ids.append(message_id)
            person.last_seen = position
    return people


@dataclass(frozen=True)
class Question:
    person_id: str
    topic: str  # "他" is the narrator, as in the interviewer prompt


class _AskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asked: bool


def _topic(person: Person) -> str:
    """What a reader would miss about someone the narrator keeps mentioning, if anything."""
    label = person.name or ("" if person.call in _PRONOUNS else person.call)
    if not person.relation:
        return f"{label}和他是什么关系" if label else ""
    if not person.name and person.call in _PRONOUNS:
        return f"这位{person.relation}叫什么名字"
    return ""


async def asked(question: Question, line: str, *, ai_model: str | None) -> bool:
    """小余 words the question freely, so a small call judges whether a line asked it."""
    prompt = (
        f"访谈主持人对讲述者说了这句话：\n{line}\n\n"
        f"这句话有没有问讲述者这件事：{question.topic}（“他”指讲述者）？"
        "只是提到这个人、问这个人别的事、问别人之间的关系，都不算。"
        '只返回 JSON：{"asked":true} 或 {"asked":false}'
    )
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
    return generated.value.asked


async def drop_ask_unless_asked(*, message_id: str, question: Question, line: str, ai_model: str | None) -> None:
    """The saved line counts as asked until the check answers, or if it fails."""
    try:
        if await asked(question, line, ai_model=ai_model):
            return
        async with async_session_maker() as db:
            message = await db.get(MemoirChatMessage, message_id)
            if message is None:
                return
            message.metadata_json = {
                key: value for key, value in (message.metadata_json or {}).items() if key != ASKED_KEY
            }
            db.add(message)
            await db.commit()
    except Exception:
        logger.warning("Person ask check failed for message %s", message_id, exc_info=True)


async def question_for(db: AsyncSession, voice_session: MemoirVoiceSession) -> Question | None:
    """Someone told about in this session and at least one earlier segment, with a gap not yet asked about."""
    if sum(ASKED_KEY in (message.metadata_json or {}) for message in voice_session.messages) >= MAX_ASKS_PER_SESSION:
        return None
    asked_column = MemoirChatMessage.metadata_json[ASKED_KEY].as_string()
    asked_ids = set(await db.scalars(
        select(asked_column).where(
            MemoirChatMessage.project_id == voice_session.project_id,
            MemoirChatMessage.role == MemoirChatMessageRole.ASSISTANT.value,
            asked_column.is_not(None),
        )
    ))
    session_ids = {message.id for message in voice_session.messages}
    people = sorted((await load_people(db, voice_session.project_id)).values(), key=lambda person: -person.last_seen)
    for person in people:
        topic = _topic(person)
        if topic and person.id not in asked_ids and len(person.message_ids) > 1 and session_ids.intersection(person.message_ids):
            return Question(person_id=person.id, topic=topic)
    return None


async def load_people(db: AsyncSession, project_id: str) -> dict[str, Person]:
    rows = await db.execute(
        select(MemoirChatMessage.id, MemoirChatMessage.metadata_json, MemoirChatMessage.transcript_version_id)
        .join(MemoirVoiceSession, MemoirChatMessage.voice_session_id == MemoirVoiceSession.id)
        .where(
            MemoirChatMessage.project_id == project_id,
            MemoirChatMessage.role == MemoirChatMessageRole.USER.value,
            MemoirVoiceSession.interview_type == MemoirInterviewType.AI_INTERVIEW.value,
            func.trim(MemoirChatMessage.content_text) != "",
        )
        .order_by(MemoirVoiceSession.created_at, MemoirChatMessage.turn_index, MemoirChatMessage.created_at)
    )
    # A reading of an older transcript version is dropped: what it said may have been corrected, and a
    # re-read of that segment must not see its own old result as already known.
    return fold(
        (message_id, metadata[READING_KEY])
        for message_id, metadata, version in rows
        if isinstance((metadata or {}).get(READING_KEY), dict)
        and metadata[READING_KEY].get("transcript_version_id") == (version or "")
    )


async def recent_people_lines(db: AsyncSession, project_id: str, *, limit: int = 12) -> tuple[str, ...]:
    people = sorted((await load_people(db, project_id)).values(), key=lambda person: -person.last_seen)
    return tuple(person.line for person in people[:limit])


async def people_lines_for(db: AsyncSession, project_id: str, message_ids: Iterable[str | None]) -> tuple[str, ...]:
    ids = set(message_ids)
    return tuple(
        person.line for person in (await load_people(db, project_id)).values() if ids.intersection(person.message_ids)
    )


async def names(db: AsyncSession, project_id: str) -> tuple[str, ...]:
    return tuple(person.name for person in (await load_people(db, project_id)).values() if person.name)


def build_people_prompt(*, people: list[Person], narrator: str, previous_line: str, segment: str) -> str:
    roster = "\n".join(
        f"P{index} {person.call or '?'}｜名字：{person.name or '未知'}｜"
        f"{person.relation or '关系未说明'}｜{person.note}"
        for index, person in enumerate(people, start=1)
    ) or "（还没有）"
    previous = f"小余：{previous_line}\n" if previous_line else ""
    return (
        "你在帮一本口述回忆录记住书里出现的人。记下来有两个用处：写书时称呼和关系不出错；"
        "访谈员小余下次聊到这个人时知道他是谁。\n\n"
        f"讲述者本人：{narrator or '称呼未知'}\n"
        f"已经记下的人：\n{roster}\n\n"
        f"{previous}讲述者：{segment}\n\n"
        "读讲述者这一段，只记他亲口说出的；小余说的、讲述者没接的话不算：\n"
        "- 这段讲到了哪些具体的人。讲述者本人（包括他介绍自己的称呼和名字）和小余不算；宠物和物件不算；"
        "“同学们”“他们”这样的一群人不算，“爷爷奶奶”这样合在一起说的，分成两个人记。"
        "讲述者只说“他”“她”时，按上下文归到他正在讲的那个人\n"
        "- 已经记下的人就写他的编号。爸爸、妈妈、爷爷、奶奶、外公、外婆、儿子这样的亲人，讲述者一般只有一位，"
        "同一个称呼就是已经记下的那位，除非他说明是另一位。别的人确定是同一个才写编号，拿不准就当新人物："
        "分开记最多称呼不统一，并错人会把两个人的事写到一起\n"
        "- 讲述者说明两个称呼是同一个人时，在其中一条的 same_as 写另一个人的编号\n"
        "- 每个人只按这段填：这段说到的都填上，已经记下的也照填；这段没说到的填 null，不从已经记下的人那里抄。"
        "call 讲述者在这段里怎么叫他（只用“他”“她”指代的填“他”或“她”），name 名字，"
        "relation 和讲述者的关系（用他自己的说法），note 一句话说他在这段里是谁、做了什么（30 字以内）\n"
        "- unclear：如果某一个具体的人有一件事不问清楚，读者就看不懂他是谁，而且讲述者一定知道答案，"
        "写成一句小余能直接问讲述者的话，用称呼不用编号；大多数段落没有\n\n"
        "只返回 JSON：\n"
        '{"people":[{"ref":"P1 或 new","same_as":null,"call":null,"name":null,"relation":null,"note":null,"unclear":null}]}'
    )


async def read_people(
    db: AsyncSession,
    *,
    project: Project,
    narrator: str,
    message: MemoirChatMessage,
    previous_line: str,
    transcript_version_id: str,
) -> bool:
    """Read one segment and store what it says about people. Returns False when its transcript changed since.

    Each reading stands on its own, so nothing is lost when it is replaced. A person keeps their id only by the
    model naming it: on a re-read, the people this segment recorded before are offered again with their ids,
    so a reorder, a renamed call or two people with one call keep their ids; anyone it no longer names is
    simply not defined here any more, and a new person gets an id no earlier version used.
    """
    roster = await load_people(db, project.id)
    previous = (message.metadata_json or {}).get(READING_KEY)
    earlier = fold([(message.id, previous)]) if isinstance(previous, dict) else {}
    people = [*roster.values(), *(person for person_id, person in earlier.items() if person_id not in roster)]
    prompt = build_people_prompt(
        people=people,
        narrator=narrator,
        previous_line=previous_line,
        segment=message.content_text.strip(),
    )
    refs = {f"P{index}": person.id for index, person in enumerate(people, start=1)}
    message_id = message.id
    ai_model = resolve_project_ai_model(project, None)
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_STRUCTURED)
    await db.commit()
    generated = await generate_structured_with_client(
        get_ai_service(ai_model),
        SYSTEM_PROMPT,
        prompt,
        output_model=_ReadingOutput,
        requested_model=ai_model,
        temperature=0.1,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    current = await db.scalar(
        select(MemoirChatMessage)
        .where(MemoirChatMessage.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None or (current.transcript_version_id or "") != transcript_version_id:
        return False

    def text(value: str | None) -> str | None:
        value = (value or "").strip()
        return value[:80] if value and value.lower() != "null" else None

    stored = [
        {
            "id": refs.get(item.ref.strip()) or f"{message_id}:{transcript_version_id}:{index}",
            "same_as": refs.get((item.same_as or "").strip()),
            **{key: text(getattr(item, key)) for key in _FIELDS},
        }
        for index, item in enumerate(generated.value.people, start=1)
    ]
    current.metadata_json = {
        **(current.metadata_json or {}),
        READING_KEY: {"transcript_version_id": transcript_version_id, "people": stored},
    }
    await db.commit()
    return True
