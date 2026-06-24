from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.memoir import MemoirBookChapterDraft, MemoirBookPlan, MemoirBookPlanChapter
from app.schemas.memoir_book import (
    MemoirBookAudioResponse,
    MemoirBookVoiceAssistantTTSRequest,
    MemoirBookVoiceCommandOperationResponse,
    MemoirBookVoiceCommandParseRequest,
    MemoirBookVoiceCommandParseResponse,
)
from app.services.ai_factory import get_ai_service
from app.services.ai_types import AITextClient
from app.services.json_utils import extract_json_payload
from app.services.memoir.access import load_memoir_project
from app.services.memoir.book_audio import audio_response, discard_staged_audio_files, synthesize_text_audio
from app.services.memoir.voice_tts import TTSClient


logger = logging.getLogger(__name__)

BOOK_VOICE_COMMAND_SYSTEM_PROMPT = (
    "你是回忆录成书页面的语音编辑助手。你的任务是理解用户口语化的中文语音请求，"
    "把可执行修改解析成安全的操作预览，把编辑判断类问题解析成不会改动数据的建议回复。"
    "只输出 JSON，不要解释。"
)

BOOK_VOICE_COMMAND_USER_PROMPT_TEMPLATE = """请解析下面这句成书语音指令，输出严格 JSON。

当前章节上下文：
{chapter_lines}

当前正在编辑章节：
{current_chapter_line}

用户原句：
{text}

JSON 格式：
{{
  "intent": "read_outline | read_chapter | rename_chapter | merge_chapters | move_chapter | split_chapter | rewrite_chapter | polish_chapter | confirm_chapter | review_chapter_title | review_chapter_length | review_chapter_structure | unknown",
  "chapter_ref": "用户提到的章节编号或标题；没有则 null",
  "target_chapter_ref": "合并目标章节编号或标题；没有则 null",
  "new_title": "改名后的新标题；没有则 null",
  "direction": "up | down | null",
  "summary": "用一句话说明识别出的操作或问题",
  "reply_text": "建议类问题必须给自然、具体、可直接播报的中文回复；操作类没有则 null"
}}

规则：
- 只根据用户原句和当前章节上下文解析，不要发散新增人物或内容。
- “读目录/念目录/听目录”用 read_outline。
- “读第一章/念某章”用 read_chapter。
- “把第一章改名为童年往事/标题改成……”用 rename_chapter，并提取 new_title。
- “把第一章和第二章合并/把第一章合并到第二章”用 merge_chapters。
- “把某章上移/下移/往前/往后”用 move_chapter，并填 direction。
- “第一章是不是需要换个名字/标题好不好/题目是否合适”用 review_chapter_title，不要生成 rename_chapter。
- “第一章是不是太短/太长/字数够不够/篇幅是否合适”用 review_chapter_length，不要生成 rewrite_chapter。
- “这一章结构是否合理/要不要拆分或合并/顺序是否合适”用 review_chapter_structure，除非用户明确要求执行。
- 建议类 intent 不产生 operations，不需要确认；reply_text 要给判断理由和下一步建议，控制在 120 字以内。
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
    "review_chapter_title",
    "review_chapter_length",
    "review_chapter_structure",
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
    "review_chapter_title": "章名建议",
    "review_chapter_length": "篇幅建议",
    "review_chapter_structure": "结构建议",
    "unknown": "需要再说明",
}

READ_INTENTS = {"read_outline", "read_chapter"}
CONSULTATION_INTENTS = {"review_chapter_title", "review_chapter_length", "review_chapter_structure"}
NON_CONFIRMATION_INTENTS = READ_INTENTS | CONSULTATION_INTENTS
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
TITLE_REVIEW_KEYWORDS = ("换个名字", "改个名字", "名字", "标题", "题目", "章名")
LENGTH_REVIEW_KEYWORDS = ("太短", "太长", "字数", "长度", "篇幅", "内容少", "内容多", "够不够")
STRUCTURE_REVIEW_KEYWORDS = ("结构", "顺序", "节奏", "拆分", "合并", "拆开", "放一起")
QUESTION_KEYWORDS = ("是不是", "是否", "要不要", "需不需要", "需要", "合不合适", "好不好", "好吗", "怎么样", "够不够")


@dataclass(frozen=True)
class ParsedCommandPayload:
    intent: str
    chapter_ref: str | None = None
    target_chapter_ref: str | None = None
    new_title: str | None = None
    direction: str | None = None
    summary: str | None = None
    reply_text: str | None = None
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


async def synthesize_book_voice_assistant_audio(
    db: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    req: MemoirBookVoiceAssistantTTSRequest,
    tts_client: TTSClient | None = None,
) -> MemoirBookAudioResponse:
    await load_memoir_project(db, project_id, user_id)
    plan = await _load_plan(db, project_id)
    if not plan:
        raise HTTPException(status_code=404, detail="章节规划不存在")

    synthesis = await synthesize_text_audio(
        db,
        user_id=user_id,
        text=req.text,
        voice=req.voice,
        speed_ratio=req.speed_ratio,
        tts_client=tts_client,
    )
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        await discard_staged_audio_files(synthesis.staged_audio)
        raise
    return audio_response(synthesis)


async def _load_plan(db: AsyncSession, project_id: str) -> MemoirBookPlan | None:
    return await db.scalar(
        select(MemoirBookPlan)
        .where(MemoirBookPlan.project_id == project_id)
        .options(
            selectinload(MemoirBookPlan.chapters)
            .selectinload(MemoirBookPlanChapter.draft)
            .selectinload(MemoirBookChapterDraft.versions)
        )
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
                current_chapter_line=_current_chapter_prompt_line(plan, req.current_chapter_id),
                text=req.text,
            ),
            temperature=0.1,
            max_tokens=700,
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
            reply_text=parsed.reply_text,
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
        reply_text=_clean_reply_text(payload.get("reply_text")),
    )


def _fallback_payload(text: str) -> ParsedCommandPayload:
    normalized = _compact(text)
    if not normalized:
        return ParsedCommandPayload(intent="unknown")
    if any(keyword in normalized for keyword in ("读目录", "念目录", "听目录", "朗读目录")):
        return ParsedCommandPayload(intent="read_outline", summary="朗读当前章节目录")

    consultation_intent = _fallback_consultation_intent(normalized)
    if consultation_intent:
        return ParsedCommandPayload(
            intent=consultation_intent,
            chapter_ref=_fallback_chapter_ref(normalized),
        )

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


def _fallback_consultation_intent(normalized: str) -> str | None:
    if not any(keyword in normalized for keyword in QUESTION_KEYWORDS):
        return None
    if any(keyword in normalized for keyword in TITLE_REVIEW_KEYWORDS):
        return "review_chapter_title"
    if any(keyword in normalized for keyword in LENGTH_REVIEW_KEYWORDS):
        return "review_chapter_length"
    if any(keyword in normalized for keyword in STRUCTURE_REVIEW_KEYWORDS):
        return "review_chapter_structure"
    return None


def _fallback_chapter_ref(normalized: str) -> str | None:
    if any(keyword in normalized for keyword in ("本章", "这章", "这一章", "当前章", "当前章节")):
        return "本章"
    match = CHAPTER_NUMBER_RE.search(normalized)
    if match:
        return match.group(0)
    return normalized


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
    elif intent in CONSULTATION_INTENTS:
        if not chapter:
            chapter = _chapter_by_id(chapters, current_chapter_id)
        if chapter or intent == "review_chapter_structure":
            operations = []
        else:
            intent = "unknown"

    if intent in CONSULTATION_INTENTS:
        summary = payload.summary or _summary_for_consultation(intent, chapter)
        reply_text = _consultation_reply_text(intent, chapter, chapters, payload.reply_text)
    else:
        summary = _summary_for_operation(intent, operations)
        reply_text = None

    return MemoirBookVoiceCommandParseResponse(
        original_text=original_text.strip(),
        intent=intent,
        intent_label=INTENT_LABELS[intent],
        summary=summary,
        reply_text=reply_text,
        operations=operations,
        requires_confirmation=intent not in NON_CONFIRMATION_INTENTS,
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


def _summary_for_consultation(intent: str, chapter: MemoirBookPlanChapter | None) -> str:
    if chapter and intent == "review_chapter_title":
        return f"正在判断「{chapter.title}」的章名是否合适。"
    if chapter and intent == "review_chapter_length":
        return f"正在判断「{chapter.title}」的篇幅是否合适。"
    if chapter and intent == "review_chapter_structure":
        return f"正在判断「{chapter.title}」的结构是否合适。"
    return "正在判断当前目录结构是否合适。"


def _consultation_reply_text(
    intent: str,
    chapter: MemoirBookPlanChapter | None,
    chapters: list[MemoirBookPlanChapter],
    ai_reply_text: str | None,
) -> str:
    cleaned_ai_reply = _clean_reply_text(ai_reply_text)
    if cleaned_ai_reply:
        return cleaned_ai_reply

    if intent == "review_chapter_title" and chapter:
        content = _latest_draft_content(chapter)
        if content:
            return (
                f"「{chapter.title}」能概括这一章，但还偏概括。"
                "如果正文里有更鲜明的人、地点或事件，章名可以换得更具体；你也可以直接说想改成什么。"
            )
        return (
            f"「{chapter.title}」先能表达方向，但现在正文还不多。"
            "建议等这一章生成或补充素材后，再根据最有画面感的场景来定章名。"
        )

    if intent == "review_chapter_length" and chapter:
        char_count = _draft_char_count(chapter)
        average_count = _average_draft_char_count(chapters)
        if char_count <= 0:
            return f"「{chapter.title}」现在还没有正文，暂时不能判断长短。可以先生成这一章，或补充一两段关键回忆。"
        if average_count and char_count < max(180, average_count * 0.65):
            return (
                f"「{chapter.title}」现在约 {char_count} 字，短于其他章节平均约 {average_count} 字。"
                "建议补一个具体场景、人物对话或时间线转折，再考虑进入下一章。"
            )
        if average_count and char_count > average_count * 1.45:
            return (
                f"「{chapter.title}」现在约 {char_count} 字，比其他章节更长。"
                "如果里面有两个明显阶段，可以考虑拆开；否则保持也没问题。"
            )
        return (
            f"「{chapter.title}」现在约 {char_count} 字，篇幅看起来是可以的。"
            "如果你想让它更饱满，优先补一个最有记忆点的细节。"
        )

    if intent == "review_chapter_structure":
        if chapter:
            source_count = len(chapter.source_refs_json or [])
            return (
                f"「{chapter.title}」目前收录 {source_count} 条素材。"
                "结构上可以先按时间、地点或一个核心事件来收束；如果有两条主线，再考虑拆章。"
            )
        return (
            f"现在目录有 {len(chapters)} 章。"
            "整体结构可以先看每章是否只承担一个主题，再调整顺序或合并重复章节。"
        )

    return "我还没判断清楚。你可以直接问：第一章名字合适吗，或者第一章是不是太短了。"


def _chapter_prompt_lines(plan: MemoirBookPlan) -> str:
    lines = []
    for index, chapter in enumerate(_sorted_chapters(plan), start=1):
        content = _latest_draft_content(chapter)
        excerpt = content[:80].replace("\n", " ")
        line = (
            f"{index}. {chapter.title}"
            f"｜素材 {len(chapter.source_refs_json or [])} 条"
            f"｜正文 {_draft_char_count(chapter)} 字"
        )
        if excerpt:
            line += f"｜摘录：{excerpt}"
        lines.append(line)
    return "\n".join(lines) or "暂无章节"


def _current_chapter_prompt_line(plan: MemoirBookPlan, current_chapter_id: str | None) -> str:
    chapters = _sorted_chapters(plan)
    chapter = _chapter_by_id(chapters, current_chapter_id)
    if not chapter:
        return "未指定"
    index = chapters.index(chapter) + 1
    return f"第 {index} 章，{chapter.title}，正文 {_draft_char_count(chapter)} 字"


def _sorted_chapters(plan: MemoirBookPlan) -> list[MemoirBookPlanChapter]:
    return sorted(plan.chapters, key=lambda chapter: chapter.sort_order)


def _chapter_by_id(chapters: list[MemoirBookPlanChapter], chapter_id: str | None) -> MemoirBookPlanChapter | None:
    if not chapter_id:
        return None
    return next((chapter for chapter in chapters if chapter.id == chapter_id), None)


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


def _latest_draft_content(chapter: MemoirBookPlanChapter) -> str:
    draft = chapter.draft
    if not draft or not draft.versions:
        return ""
    return (draft.versions[-1].content or "").strip()


def _draft_char_count(chapter: MemoirBookPlanChapter) -> int:
    return len(re.sub(r"\s+", "", _latest_draft_content(chapter)))


def _average_draft_char_count(chapters: list[MemoirBookPlanChapter]) -> int:
    counts = [_draft_char_count(chapter) for chapter in chapters]
    non_empty_counts = [count for count in counts if count > 0]
    if not non_empty_counts:
        return 0
    return round(sum(non_empty_counts) / len(non_empty_counts))


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


def _clean_reply_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value.strip()).strip()
    if not text:
        return None
    if len(text) > 280:
        return f"{text[:277]}..."
    return text


def _clean_title(value: str) -> str:
    return value.strip(" ，。,.!！?？\"'“”‘’")[:80]


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())
