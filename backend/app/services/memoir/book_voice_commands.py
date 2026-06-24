from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.memoir import MemoirBookPlan, MemoirBookPlanChapter
from app.schemas.memoir_book import (
    MemoirBookVoiceCommandOperationResponse,
    MemoirBookVoiceCommandParseRequest,
    MemoirBookVoiceCommandParseResponse,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.access import load_memoir_project


logger = logging.getLogger(__name__)

BOOK_VOICE_COMMAND_SYSTEM_PROMPT = (
    "你是回忆录成书页面的语音编辑助手。你的任务是把用户口语化的中文语音指令解析成安全的成书操作预览。"
    "只输出 JSON，不要解释。"
)

BOOK_VOICE_COMMAND_USER_PROMPT_TEMPLATE = """请解析下面这句成书语音指令，输出严格 JSON。

当前章节：
{chapter_lines}

用户原句：
{text}

JSON 格式：
{{
  "intent": "read_outline | read_chapter | rename_chapter | merge_chapters | move_chapter | split_chapter | rewrite_chapter | polish_chapter | confirm_chapter | unknown",
  "chapter_ref": "用户提到的章节编号或标题；没有则 null",
  "target_chapter_ref": "合并目标章节编号或标题；没有则 null",
  "new_title": "改名后的新标题；没有则 null",
  "direction": "up | down | null",
  "summary": "用一句话说明识别出的操作"
}}

规则：
- 只根据用户原句和当前章节列表解析，不要发散新增章节名、人物或内容。
- “读目录/念目录/听目录”用 read_outline。
- “读第一章/念某章”用 read_chapter。
- “把第一章改名为童年往事/标题改成……”用 rename_chapter，并提取 new_title。
- “把第一章和第二章合并/把第一章合并到第二章”用 merge_chapters。
- “把某章上移/下移/往前/往后”用 move_chapter，并填 direction。
- 如果缺少必要章节或无法判断，intent 用 unknown。
"""

SUPPORTED_INTENTS = {
    "read_outline",
    "read_chapter",
    "rename_chapter",
    "merge_chapters",
    "move_chapter",
    "split_chapter",
    "rewrite_chapter",
    "polish_chapter",
    "confirm_chapter",
    "unknown",
}

INTENT_LABELS = {
    "read_outline": "朗读目录",
    "read_chapter": "朗读章节",
    "rename_chapter": "修改章名",
    "merge_chapters": "合并章节",
    "move_chapter": "移动章节",
    "split_chapter": "拆分章节",
    "rewrite_chapter": "重写章节",
    "polish_chapter": "润色章节",
    "confirm_chapter": "确认章节",
    "unknown": "需要再说明",
}

READ_INTENTS = {"read_outline", "read_chapter"}
CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
CHAPTER_NUMBER_RE = re.compile(r"第?\s*([0-9]{1,2}|[一二两三四五六七八九十])\s*(?:章|章节)?")
RENAME_RE = re.compile(r"(?:把|将)?(.{1,40}?)(?:的)?(?:章名|标题)?(?:改名为|改成|改为|叫)(.{2,40})")
MERGE_INTO_RE = re.compile(r"(?:把|将)?(.{1,40}?)(?:合并到|并到|放到)(.{1,40})")
MERGE_WITH_RE = re.compile(r"(?:把|将)?(.{1,40}?)(?:和|跟|与)(.{1,40}?)(?:合并|并到一起|放到一起)")
MOVE_RE = re.compile(r"(?:把|将)?(.{1,40}?)(上移|下移|往前|往后|前移|后移)")


@dataclass(frozen=True)
class ParsedCommandPayload:
    intent: str
    chapter_ref: str | None = None
    target_chapter_ref: str | None = None
    new_title: str | None = None
    direction: str | None = None
    summary: str | None = None
    used_ai: bool = False
    provider: str | None = None
    model: str | None = None


async def parse_book_voice_command(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookVoiceCommandParseRequest,
    ai_client: AITextClient | None = None,
) -> MemoirBookVoiceCommandParseResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")

    payload = await _parse_payload_with_ai(req, plan, ai_client=ai_client)
    return _response_from_payload(req.text, plan, req.current_chapter_id, payload)


async def _load_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(selectinload(MemoirBookPlan.chapters))
        .execution_options(populate_existing=True)
    )


async def _parse_payload_with_ai(
    req: MemoirBookVoiceCommandParseRequest,
    plan: MemoirBookPlan,
    *,
    ai_client: AITextClient | None = None,
) -> ParsedCommandPayload:
    fallback = _fallback_payload(req.text)
    client = ai_client
    try:
        if client is None:
            client = get_ai_service(req.ai_model)
        content = await client._call_llm(
            BOOK_VOICE_COMMAND_SYSTEM_PROMPT,
            BOOK_VOICE_COMMAND_USER_PROMPT_TEMPLATE.format(
                chapter_lines=_chapter_prompt_lines(plan),
                text=req.text,
            ),
            temperature=0.1,
            max_tokens=500,
            timeout=45.0,
        )
        payload = extract_json_payload(str(content))
        parsed = _payload_from_json(payload)
        if parsed.intent == "unknown":
            return fallback
        return ParsedCommandPayload(
            intent=parsed.intent,
            chapter_ref=parsed.chapter_ref,
            target_chapter_ref=parsed.target_chapter_ref,
            new_title=parsed.new_title,
            direction=parsed.direction,
            summary=parsed.summary,
            used_ai=True,
            provider=getattr(client, "provider_name", None),
            model=getattr(client, "model_name", req.ai_model),
        )
    except Exception:
        logger.warning(
            "AI book voice command parse failed provider=%s model=%s; using fallback",
            getattr(client, "provider_name", "unknown"),
            getattr(client, "model_name", req.ai_model or "default"),
            exc_info=True,
        )
        return fallback


def _payload_from_json(payload: Any) -> ParsedCommandPayload:
    if not isinstance(payload, dict):
        return ParsedCommandPayload(intent="unknown")
    intent = str(payload.get("intent") or "").strip()
    if intent not in SUPPORTED_INTENTS:
        intent = "unknown"
    direction = _normalized_direction(payload.get("direction"))
    return ParsedCommandPayload(
        intent=intent,
        chapter_ref=_optional_text(payload.get("chapter_ref")),
        target_chapter_ref=_optional_text(payload.get("target_chapter_ref")),
        new_title=_optional_text(payload.get("new_title")),
        direction=direction,
        summary=_optional_text(payload.get("summary")),
    )


def _fallback_payload(text: str) -> ParsedCommandPayload:
    normalized = _compact(text)
    if not normalized:
        return ParsedCommandPayload(intent="unknown")
    if any(keyword in normalized for keyword in ("读目录", "念目录", "听目录", "朗读目录")):
        return ParsedCommandPayload(intent="read_outline", summary="朗读当前章节目录")

    move_match = MOVE_RE.search(normalized)
    if move_match:
        return ParsedCommandPayload(
            intent="move_chapter",
            chapter_ref=move_match.group(1),
            direction=_normalized_direction(move_match.group(2)),
        )

    merge_into = MERGE_INTO_RE.search(normalized)
    if merge_into:
        return ParsedCommandPayload(
            intent="merge_chapters",
            chapter_ref=merge_into.group(1),
            target_chapter_ref=merge_into.group(2),
        )
    merge_with = MERGE_WITH_RE.search(normalized)
    if merge_with:
        return ParsedCommandPayload(
            intent="merge_chapters",
            chapter_ref=merge_with.group(1),
            target_chapter_ref=merge_with.group(2),
        )

    rename = RENAME_RE.search(normalized)
    if rename:
        return ParsedCommandPayload(
            intent="rename_chapter",
            chapter_ref=rename.group(1),
            new_title=_clean_title(rename.group(2)),
        )

    read_match = CHAPTER_NUMBER_RE.search(normalized)
    if read_match and any(keyword in normalized for keyword in ("读", "念", "听", "朗读")):
        return ParsedCommandPayload(intent="read_chapter", chapter_ref=read_match.group(0))

    return ParsedCommandPayload(intent="unknown")


def _response_from_payload(
    original_text: str,
    plan: MemoirBookPlan,
    current_chapter_id: str | None,
    payload: ParsedCommandPayload,
) -> MemoirBookVoiceCommandParseResponse:
    intent = payload.intent if payload.intent in SUPPORTED_INTENTS else "unknown"
    chapters = _sorted_chapters(plan)
    chapter = _resolve_chapter(chapters, payload.chapter_ref, current_chapter_id)
    target_chapter = _resolve_chapter(chapters, payload.target_chapter_ref, current_chapter_id)

    operations: list[MemoirBookVoiceCommandOperationResponse] = []
    if intent == "read_outline":
        operations.append(MemoirBookVoiceCommandOperationResponse(type="read_outline"))
    elif intent in {"read_chapter", "rewrite_chapter", "polish_chapter", "confirm_chapter", "split_chapter"}:
        if chapter:
            operations.append(_operation(intent, chapter=chapter))
        else:
            intent = "unknown"
    elif intent == "rename_chapter":
        title = _clean_title(payload.new_title or "")
        if chapter and title:
            operations.append(_operation(intent, chapter=chapter, new_title=title))
        else:
            intent = "unknown"
    elif intent == "merge_chapters":
        if chapter and target_chapter and chapter.id != target_chapter.id:
            operations.append(_operation(intent, chapter=chapter, target_chapter=target_chapter))
        else:
            intent = "unknown"
    elif intent == "move_chapter":
        direction = payload.direction if payload.direction in {"up", "down"} else None
        if chapter and direction:
            operations.append(_operation(intent, chapter=chapter, direction=direction))
        else:
            intent = "unknown"

    summary = _summary_for_operation(intent, operations)
    return MemoirBookVoiceCommandParseResponse(
        original_text=original_text.strip(),
        intent=intent,
        intent_label=INTENT_LABELS[intent],
        summary=summary,
        operations=operations,
        requires_confirmation=intent not in READ_INTENTS,
        used_ai=payload.used_ai and intent != "unknown",
        provider=payload.provider if payload.used_ai and intent != "unknown" else None,
        model=payload.model if payload.used_ai and intent != "unknown" else None,
    )


def _operation(
    operation_type: str,
    *,
    chapter: MemoirBookPlanChapter | None = None,
    target_chapter: MemoirBookPlanChapter | None = None,
    new_title: str | None = None,
    direction: str | None = None,
) -> MemoirBookVoiceCommandOperationResponse:
    return MemoirBookVoiceCommandOperationResponse(
        type=operation_type,
        chapter_id=chapter.id if chapter else None,
        chapter_title=chapter.title if chapter else None,
        target_chapter_id=target_chapter.id if target_chapter else None,
        target_chapter_title=target_chapter.title if target_chapter else None,
        new_title=new_title,
        direction=direction,
    )


def _summary_for_operation(intent: str, operations: list[MemoirBookVoiceCommandOperationResponse]) -> str:
    operation = operations[0] if operations else None
    if intent == "read_outline":
        return "准备朗读当前章节目录。"
    if intent == "read_chapter" and operation:
        return f"准备朗读「{operation.chapter_title}」。"
    if intent == "rename_chapter" and operation:
        return f"准备把「{operation.chapter_title}」改名为「{operation.new_title}」。"
    if intent == "merge_chapters" and operation:
        return f"准备把「{operation.chapter_title}」合并到「{operation.target_chapter_title}」。"
    if intent == "move_chapter" and operation:
        direction = "上移" if operation.direction == "up" else "下移"
        return f"准备把「{operation.chapter_title}」{direction}。"
    if intent == "split_chapter" and operation:
        return f"准备拆分「{operation.chapter_title}」。"
    if intent == "rewrite_chapter" and operation:
        return f"准备重写「{operation.chapter_title}」。"
    if intent == "polish_chapter" and operation:
        return f"准备润色「{operation.chapter_title}」。"
    if intent == "confirm_chapter" and operation:
        return f"准备确认「{operation.chapter_title}」。"
    return "还没有听清要修改哪一章，请换个说法再试。"


def _chapter_prompt_lines(plan: MemoirBookPlan) -> str:
    lines = []
    for index, chapter in enumerate(_sorted_chapters(plan), start=1):
        lines.append(f"{index}. {chapter.title}")
    return "\n".join(lines) or "暂无章节"


def _sorted_chapters(plan: MemoirBookPlan) -> list[MemoirBookPlanChapter]:
    return sorted(plan.chapters, key=lambda chapter: chapter.sort_order)


def _resolve_chapter(
    chapters: list[MemoirBookPlanChapter],
    ref: str | None,
    current_chapter_id: str | None,
) -> MemoirBookPlanChapter | None:
    text = _compact(ref or "")
    if text in {"本章", "这一章", "当前章", "当前章节"} and current_chapter_id:
        return next((chapter for chapter in chapters if chapter.id == current_chapter_id), None)
    chapter_number = _chapter_number(text)
    if chapter_number is not None and 1 <= chapter_number <= len(chapters):
        return chapters[chapter_number - 1]
    if not text:
        return None
    for chapter in chapters:
        title = _compact(chapter.title)
        if text == title or text in title or title in text:
            return chapter
    return None


def _chapter_number(text: str) -> int | None:
    match = CHAPTER_NUMBER_RE.search(text)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return CHINESE_NUMBERS.get(raw)


def _normalized_direction(value: Any) -> str | None:
    text = _compact(str(value or ""))
    if text in {"up", "上移", "往前", "前移", "提前"}:
        return "up"
    if text in {"down", "下移", "往后", "后移"}:
        return "down"
    return None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _clean_title(value: str) -> str:
    return value.strip(" ，。,.!！?？\"'“”‘’")[:80]


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())
