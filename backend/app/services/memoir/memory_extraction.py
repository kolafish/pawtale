import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memoir import (
    MemoirCreatedBy,
    MemoirMemoryType,
    MemoirOutputType,
    MemoirQuestionType,
    MemoirSensitivity,
)
from app.models.memoir import MemoirAnswer, MemoirQuestion, MemoirSession
from app.schemas.memoir import (
    MemoirEventMemoryCreate,
    MemoirMemoryEvidenceCreate,
    MemoirOpenQuestionCreate,
    MemoirPersonMemoryCreate,
    MemoirPlaceMemoryCreate,
    MemoirSessionProcessRequest,
    MemoirSessionProcessResponse,
    MemoirThemeMemoryCreate,
)
from app.services import get_ai_service
from app.services.ai_types import AITextClient
from app.services.memoir.access import load_session
from app.services.memoir.memory_repository import (
    add_memory_evidence,
    create_event_memory,
    create_open_question,
    create_person_memory,
    create_place_memory,
    create_theme_memory,
)
from app.services.memoir.outputs import upsert_session_output_version
from app.services.memoir.session_summary import upsert_session_summary


SYSTEM_PROMPT = "你是一位谨慎的中文人生访谈记忆整理员，只根据给定问答提取事实和待确认问题。"
AI_EXTRACTION_UNAVAILABLE_MESSAGE = "AI 服务暂时不可用，请稍后重试。"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionTurn:
    session_id: str
    question_id: str
    answer_id: str
    question: str
    answer_text: str


def _answer_text(raw: str, edited: str) -> str:
    return (edited or raw or "").strip()


async def _collect_turns(db: AsyncSession, session: MemoirSession) -> list[SessionTurn]:
    turns: list[SessionTurn] = []
    result = await db.execute(
        select(MemoirQuestion, MemoirAnswer)
        .join(MemoirAnswer, MemoirAnswer.question_id == MemoirQuestion.id)
        .where(MemoirQuestion.session_id == session.id)
        .order_by(MemoirQuestion.sort_order, MemoirQuestion.created_at)
    )
    for question, answer in result.all():
        text = _answer_text(answer.transcript_raw, answer.transcript_edited)
        if not text:
            continue
        turns.append(
            SessionTurn(
                session_id=session.id,
                question_id=question.id,
                answer_id=answer.id,
                question=question.prompt,
                answer_text=text,
            )
        )
    return turns


def _build_user_prompt(session: MemoirSession, turns: list[SessionTurn]) -> str:
    qa_text = "\n\n".join(
        (
            f"Q{index}: {turn.question}\n"
            f"A{index}: {turn.answer_text}\n"
            f"question_id: {turn.question_id}\n"
            f"answer_id: {turn.answer_id}"
        )
        for index, turn in enumerate(turns, start=1)
    )
    return f"""请从这次回忆录访谈中提取长期结构化记忆。只允许使用问答里出现的信息，不要编造。

访谈标题：{session.title}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{{
  "people": [
    {{
      "canonical_name": "人物名",
      "aliases": ["别名"],
      "relationship": "与受访者关系",
      "description": "只基于原文的人物说明",
      "confidence": 0.0,
      "evidence_quote": "原文证据",
      "answer_id": "来源回答 id"
    }}
  ],
  "events": [
    {{
      "title": "事件标题",
      "life_stage": "童年/青年/成年/晚年等",
      "time_text": "原文时间",
      "place_text": "原文地点",
      "cause": "起因",
      "process": "经过",
      "result": "结果",
      "emotion": "情绪",
      "importance": 0.0,
      "confidence": 0.0,
      "evidence_quote": "原文证据",
      "answer_id": "来源回答 id"
    }}
  ],
  "places": [
    {{
      "name": "地点名",
      "elder_alias": "受访者说法",
      "modern_name": "可确定的现代地名，否则为空",
      "place_type": "家乡/学校/工作地点等",
      "description": "地点说明",
      "confidence": 0.0,
      "evidence_quote": "原文证据",
      "answer_id": "来源回答 id"
    }}
  ],
  "themes": [
    {{
      "name": "反复主题",
      "description": "主题说明",
      "strength": 0.0,
      "mention_count": 1,
      "confidence": 0.0,
      "evidence_quote": "原文证据",
      "answer_id": "来源回答 id"
    }}
  ],
  "open_questions": [
    {{
      "question": "下次需要确认或追问的问题",
      "question_type": "clarification/depth/scene/person/emotion/connection/closure",
      "reason": "为什么要问",
      "sensitivity": "low/medium/high",
      "priority": 0.0,
      "target_memory_type": "person/event/place/theme",
      "target_title": "刚提取出的目标记忆标题或名称",
      "evidence_quote": "触发该问题的原文证据",
      "answer_id": "来源回答 id"
    }}
  ],
  "session_summary": "本次访谈摘要",
  "story_fragment": "可直接回读给家人看的故事片段草稿",
  "next_question_suggestions": []
}}

要求：
- 不确定的信息必须降低 confidence，并优先生成 open_questions。
- evidence_quote 必须来自回答原文。
- 没有内容的数组返回空数组。
- 故事片段可以润色，但不得新增事实。

访谈问答：
{qa_text}
"""


def _extract_json_object(content: str) -> dict[str, Any]:
    value = str(content).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, flags=re.DOTALL)
    if fenced:
        value = fenced.group(1)
    elif "{" in value and "}" in value:
        value = value[value.find("{"): value.rfind("}") + 1]

    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="AI未返回有效的结构化记忆JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="AI未返回有效的结构化记忆JSON")
    return payload


def _items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _text_list(item: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, list):
            return [str(entry).strip() for entry in value if str(entry).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    return []


def _bounded_float(item: dict[str, Any], key: str) -> float | None:
    value = item.get(key)
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def _positive_int(item: dict[str, Any], key: str, default: int = 1) -> int:
    value = item.get(key, default)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, number)


def _enum_value(enum_cls: type[Enum], raw: Any, default: Enum) -> Enum:
    if raw is None:
        return default
    value = str(raw).strip().lower()
    aliases = {
        "scene_detail": MemoirQuestionType.SCENE,
        "person_detail": MemoirQuestionType.PERSON,
        "emotion_detail": MemoirQuestionType.EMOTION,
    }
    if value in aliases and isinstance(aliases[value], enum_cls):
        return aliases[value]
    for option in enum_cls:
        if value == option.value:
            return option
    return default


def _find_turn(turns: list[SessionTurn], item: dict[str, Any]) -> SessionTurn:
    answer_id = _text(item, "answer_id")
    question_id = _text(item, "question_id")
    quote = _text(item, "evidence_quote", "quote")

    for turn in turns:
        if answer_id and turn.answer_id == answer_id:
            return turn
        if question_id and turn.question_id == question_id:
            return turn
    if quote:
        for turn in turns:
            if quote in turn.answer_text:
                return turn
    return turns[0]


def _quote(item: dict[str, Any], turn: SessionTurn) -> str:
    return _text(item, "evidence_quote", "quote") or turn.answer_text[:300]


async def _add_evidence(
    db: AsyncSession,
    user_id: str,
    project_id: str,
    turns: list[SessionTurn],
    memory_type: MemoirMemoryType,
    memory_id: str,
    item: dict[str, Any],
) -> None:
    turn = _find_turn(turns, item)
    await add_memory_evidence(
        db,
        user_id,
        MemoirMemoryEvidenceCreate(
            project_id=project_id,
            memory_type=memory_type,
            memory_id=memory_id,
            session_id=turn.session_id,
            question_id=turn.question_id,
            answer_id=turn.answer_id,
            quote=_quote(item, turn),
            confidence=_bounded_float(item, "confidence"),
        ),
    )


def _memory_lookup_key(memory_type: MemoirMemoryType, label: str) -> tuple[MemoirMemoryType, str]:
    return (memory_type, label.strip().lower())


def _resolve_target(
    item: dict[str, Any],
    created_memory_ids: dict[tuple[MemoirMemoryType, str], str],
) -> tuple[MemoirMemoryType | None, str | None]:
    raw_type = _text(item, "target_memory_type", "target_type")
    if not raw_type:
        return None, None
    try:
        memory_type = MemoirMemoryType(raw_type)
    except ValueError:
        return None, None

    label = _text(
        item,
        "target_title",
        "target_name",
        "target_memory_title",
        "event_title",
        "person_name",
        "place_name",
        "theme_name",
    )
    memory_id = created_memory_ids.get(_memory_lookup_key(memory_type, label)) if label else None
    if not memory_id:
        return None, None
    return memory_type, memory_id


def _slim_people(people) -> list[dict]:
    return [
        {
            "id": item.id,
            "canonical_name": item.canonical_name,
            "relationship": item.relationship,
            "confidence": item.confidence,
        }
        for item in people
    ]


def _slim_events(events) -> list[dict]:
    return [
        {
            "id": item.id,
            "title": item.title,
            "life_stage": item.life_stage,
            "time_text": item.time_text,
            "place_text": item.place_text,
            "confidence": item.confidence,
        }
        for item in events
    ]


def _slim_places(places) -> list[dict]:
    return [
        {
            "id": item.id,
            "name": item.name,
            "elder_alias": item.elder_alias,
            "modern_name": item.modern_name,
            "confidence": item.confidence,
        }
        for item in places
    ]


def _slim_themes(themes) -> list[dict]:
    return [
        {
            "id": item.id,
            "name": item.name,
            "strength": item.strength,
            "mention_count": item.mention_count,
            "confidence": item.confidence,
        }
        for item in themes
    ]


def _slim_open_questions(open_questions) -> list[dict]:
    return [
        {
            "id": item.id,
            "question": item.question,
            "question_type": item.question_type,
            "reason": item.reason,
            "sensitivity": item.sensitivity,
            "priority": item.priority,
        }
        for item in open_questions
    ]


async def _call_extraction_ai(
    session: MemoirSession,
    turns: list[SessionTurn],
    req: MemoirSessionProcessRequest,
    ai_client: AITextClient | None,
) -> tuple[dict[str, Any], str, AITextClient]:
    client = ai_client or get_ai_service(req.ai_model)
    user_prompt = _build_user_prompt(session, turns)
    try:
        content = await client._call_llm(
            SYSTEM_PROMPT,
            user_prompt,
            temperature=0.2,
            max_tokens=5000,
            timeout=120.0,
        )
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.exception(
            "AI memoir extraction failed provider=%s model=%s",
            client.provider_name,
            client.model_name,
        )
        raise HTTPException(status_code=503, detail=AI_EXTRACTION_UNAVAILABLE_MESSAGE) from exc
    except Exception as exc:
        logger.exception(
            "AI memoir extraction failed provider=%s model=%s",
            client.provider_name,
            client.model_name,
        )
        raise HTTPException(status_code=503, detail=AI_EXTRACTION_UNAVAILABLE_MESSAGE) from exc

    return _extract_json_object(str(content)), user_prompt, client


async def process_session_memory(
    db: AsyncSession,
    session_id: str,
    user_id: str,
    req: MemoirSessionProcessRequest,
    ai_client: AITextClient | None = None,
) -> MemoirSessionProcessResponse:
    session = await load_session(db, session_id, user_id)
    turns = await _collect_turns(db, session)
    if not turns:
        raise HTTPException(status_code=400, detail="本次访谈没有可处理的回答")

    payload, prompt_snapshot, client = await _call_extraction_ai(session, turns, req, ai_client)
    project_id = session.project_id
    created_memory_ids: dict[tuple[MemoirMemoryType, str], str] = {}

    people = []
    for item in _items(payload, "people"):
        canonical_name = _text(item, "canonical_name", "name")
        if not canonical_name:
            continue
        person = await create_person_memory(
            db,
            user_id,
            MemoirPersonMemoryCreate(
                project_id=project_id,
                canonical_name=canonical_name,
                aliases_json=_text_list(item, "aliases", "aliases_json"),
                relationship=_text(item, "relationship") or None,
                first_seen_session_id=session.id,
                description=_text(item, "description"),
                confidence=_bounded_float(item, "confidence"),
                created_by=MemoirCreatedBy.AI,
            ),
        )
        people.append(person)
        created_memory_ids[_memory_lookup_key(MemoirMemoryType.PERSON, person.canonical_name)] = person.id
        await _add_evidence(db, user_id, project_id, turns, MemoirMemoryType.PERSON, person.id, item)

    events = []
    for item in _items(payload, "events"):
        title = _text(item, "title", "event_title")
        if not title:
            continue
        event = await create_event_memory(
            db,
            user_id,
            MemoirEventMemoryCreate(
                project_id=project_id,
                title=title,
                life_stage=_text(item, "life_stage") or None,
                time_text=_text(item, "time_text", "time") or None,
                place_text=_text(item, "place_text", "place") or None,
                cause=_text(item, "cause"),
                process=_text(item, "process", "description"),
                result=_text(item, "result"),
                emotion=_text(item, "emotion") or None,
                importance=_bounded_float(item, "importance"),
                confidence=_bounded_float(item, "confidence"),
                created_by=MemoirCreatedBy.AI,
            ),
        )
        events.append(event)
        created_memory_ids[_memory_lookup_key(MemoirMemoryType.EVENT, event.title)] = event.id
        await _add_evidence(db, user_id, project_id, turns, MemoirMemoryType.EVENT, event.id, item)

    places = []
    for item in _items(payload, "places"):
        name = _text(item, "name", "place_name")
        if not name:
            continue
        place = await create_place_memory(
            db,
            user_id,
            MemoirPlaceMemoryCreate(
                project_id=project_id,
                name=name,
                elder_alias=_text(item, "elder_alias", "alias") or None,
                modern_name=_text(item, "modern_name") or None,
                place_type=_text(item, "place_type", "type") or None,
                description=_text(item, "description"),
                confidence=_bounded_float(item, "confidence"),
                created_by=MemoirCreatedBy.AI,
            ),
        )
        places.append(place)
        created_memory_ids[_memory_lookup_key(MemoirMemoryType.PLACE, place.name)] = place.id
        await _add_evidence(db, user_id, project_id, turns, MemoirMemoryType.PLACE, place.id, item)

    themes = []
    for item in _items(payload, "themes"):
        name = _text(item, "name", "theme")
        if not name:
            continue
        theme = await create_theme_memory(
            db,
            user_id,
            MemoirThemeMemoryCreate(
                project_id=project_id,
                name=name,
                description=_text(item, "description"),
                strength=_bounded_float(item, "strength"),
                mention_count=_positive_int(item, "mention_count"),
                confidence=_bounded_float(item, "confidence"),
                created_by=MemoirCreatedBy.AI,
            ),
        )
        themes.append(theme)
        created_memory_ids[_memory_lookup_key(MemoirMemoryType.THEME, theme.name)] = theme.id
        await _add_evidence(db, user_id, project_id, turns, MemoirMemoryType.THEME, theme.id, item)

    open_questions = []
    for item in _items(payload, "open_questions"):
        question = _text(item, "question")
        if not question:
            continue
        target_memory_type, target_memory_id = _resolve_target(item, created_memory_ids)
        open_question = await create_open_question(
            db,
            user_id,
            MemoirOpenQuestionCreate(
                project_id=project_id,
                session_id=session.id,
                target_memory_type=target_memory_type,
                target_memory_id=target_memory_id,
                question=question,
                question_type=_enum_value(
                    MemoirQuestionType,
                    item.get("question_type") or item.get("type"),
                    MemoirQuestionType.CLARIFICATION,
                ),
                reason=_text(item, "reason"),
                sensitivity=_enum_value(
                    MemoirSensitivity,
                    item.get("sensitivity"),
                    MemoirSensitivity.LOW,
                ),
                priority=_bounded_float(item, "priority"),
                created_by=MemoirCreatedBy.AI,
            ),
        )
        open_questions.append(open_question)
        await _add_evidence(
            db,
            user_id,
            project_id,
            turns,
            MemoirMemoryType.OPEN_QUESTION,
            open_question.id,
            item,
        )

    summary_text = str(payload.get("session_summary") or payload.get("summary") or "").strip()
    if not summary_text:
        summary_text = "本次访谈已完成结构化处理，暂无可用摘要。"
    session_summary = await upsert_session_summary(
        db,
        session,
        summary=summary_text,
        new_people_json=_slim_people(people),
        new_events_json=_slim_events(events),
        new_places_json=_slim_places(places),
        new_themes_json=_slim_themes(themes),
        open_questions_json=_slim_open_questions(open_questions),
        next_question_suggestions_json=payload.get("next_question_suggestions")
        if isinstance(payload.get("next_question_suggestions"), list)
        else [],
    )

    story_fragment = None
    story_text = str(payload.get("story_fragment") or "").strip()
    if story_text:
        story_fragment = await upsert_session_output_version(
            db,
            project_id=project_id,
            session_id=session.id,
            output_type=MemoirOutputType.STORY_FRAGMENT,
            title=str(payload.get("story_title") or session.title or "访谈故事片段").strip(),
            content=story_text,
            model=client.model_name,
            prompt_snapshot=prompt_snapshot,
        )

    await db.commit()
    return MemoirSessionProcessResponse(
        session_summary=session_summary,
        story_fragment=story_fragment,
        people=people,
        events=events,
        places=places,
        themes=themes,
        open_questions=open_questions,
    )
