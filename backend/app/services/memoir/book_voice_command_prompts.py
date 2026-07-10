from __future__ import annotations

from typing import Any

from app.models.memoir import MemoirBookPlan
from app.schemas.memoir_book import MemoirBookVoiceCommandBookContext
from app.services.memoir.book_voice_command_context import (
    chapter_source_count,
    draft_char_count,
    latest_draft_content,
)
from app.services.memoir.book_plan_sources import (
    find_chapter_by_id as _chapter_by_id,
    sorted_chapters as _sorted_chapters,
)
from app.services.memoir.book_voice_command_rules import _clean_reply_text


BOOK_VOICE_COMMAND_SYSTEM_PROMPT = (
    "你是回忆录成书页面的语音编辑助手。你的任务是理解用户口语化的中文语音请求，"
    "把可执行修改解析成安全的操作预览，把编辑判断类问题解析成不会改动数据的建议回复，"
    "也要能回答当前成书状态、可修改范围和上下文追问。"
    "只输出 JSON，不要解释。"
)

BOOK_VOICE_COMMAND_USER_PROMPT_TEMPLATE = """请解析下面这句成书语音指令，输出严格 JSON。

当前章节上下文：
{chapter_lines}

当前成书状态：
{book_status}

当前素材覆盖：
{source_coverage_status}

当前正在编辑章节：
{current_chapter_line}

最近语音对话：
{conversation_lines}

用户原句：
{text}

JSON 格式：
{{
  "intent": "read_outline | read_chapter | read_uncovered_source | rename_chapter | merge_chapters | move_chapter | assign_source | split_chapter | rewrite_chapter | polish_chapter | confirm_chapter | review_chapter_title | review_chapter_length | review_chapter_structure | answer_book_question | capability_help | clarify_previous | unknown",
  "response_kind": "action_preview | consultation | answer | clarification",
  "chapter_ref": "用户提到的章节编号或标题；没有则 null",
  "target_chapter_ref": "合并目标章节编号或标题；没有则 null",
  "source_ref": "用户提到的待收录记录编号、语音记录编号或刚才这条；没有则 null",
  "new_title": "改名后的新标题；没有则 null",
  "direction": "up | down | null",
  "target_position": "first | last | null",
  "target_index": "目标最终章节序号，数字；没有则 null",
  "relative_chapter_ref": "用户说移到某章前/后时的相对章节；没有则 null",
  "relative_position": "before | after | null",
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
- “把某章移到第一章/最后一章/第 3 章”用 move_chapter，并填 target_position 或 target_index；不要改成上移/下移。
- “把某章移到某章前面/后面”用 move_chapter，并填 relative_chapter_ref 和 relative_position；不要改成上移/下移。
- “把第二章和第三章顺序换一下/交换第二章和第三章”这类相邻章节交换也用 move_chapter：把前一章下移，或把后一章上移。
- “把第 1 条待收录记录归到第二章/把刚才这条放到职场那章”用 assign_source，source_ref 填记录编号或“刚才这条”，target_chapter_ref 填目标章节。
- “先念一条待收录记录/下一条未收入记录/念一下第一条没收入的记录”用 read_uncovered_source，source_ref 填“第几条”或“下一条”，reply_text 要读出对应待收录记录摘录，并提示用户说“把这条归到哪一章”。
- “第一章是不是需要换个名字/标题好不好/题目是否合适”用 review_chapter_title，不要生成 rename_chapter。
- “第一章是不是太短/太长/字数够不够/篇幅是否合适”用 review_chapter_length，不要生成 rewrite_chapter。
- “这一章结构是否合理/要不要拆分或合并/顺序是否合适”用 review_chapter_structure，除非用户明确要求执行。
- “现在有几章/都有几章/目录里有什么/现在都有哪些章节”等状态问题用 answer_book_question，reply_text 要直接回答章节数量和标题。
- “还有多少记录未收入章节/是不是有记录还没归到章节”等素材覆盖问题用 answer_book_question，reply_text 要按当前素材覆盖回答。
- “我可以修改什么/能改哪些/可以怎么改”等能力问题用 capability_help，reply_text 要结合当前目录说明可做的修改。
- “那个/这个/现在呢/继续”等短追问要结合最近语音对话；能判断指代就继续回答，不能判断用 clarify_previous 并问一个具体澄清问题。
- 建议类 intent 不产生 operations，不需要确认；reply_text 要给判断理由和下一步建议，控制在 120 字以内。
- read_uncovered_source、answer_book_question、capability_help、clarify_previous 不产生 operations，不需要确认。
- 如果缺少必要章节或无法判断，intent 用 unknown。
"""


def build_book_voice_command_user_prompt(
    *,
    text: str,
    plan: MemoirBookPlan,
    current_chapter_id: str | None,
    conversation_context: list[Any] | None,
    book_context: MemoirBookVoiceCommandBookContext | None,
    source_count_by_chapter: dict[str, int] | None = None,
) -> str:
    return BOOK_VOICE_COMMAND_USER_PROMPT_TEMPLATE.format(
        chapter_lines=_chapter_prompt_lines(plan, source_count_by_chapter=source_count_by_chapter),
        book_status=_book_status_prompt_line(plan),
        source_coverage_status=_source_coverage_prompt_line(book_context),
        current_chapter_line=_current_chapter_prompt_line(plan, current_chapter_id),
        conversation_lines=_conversation_prompt_lines(conversation_context),
        text=text,
    )


def _book_status_prompt_line(plan: MemoirBookPlan) -> str:
    chapters = _sorted_chapters(plan)
    non_empty_drafts = sum(1 for chapter in chapters if draft_char_count(chapter) > 0)
    return f"当前目录 {len(chapters)} 章，已有正文 {non_empty_drafts} 章，规划状态 {plan.status}。"


def _source_coverage_prompt_line(book_context: MemoirBookVoiceCommandBookContext | None) -> str:
    coverage = book_context.source_coverage if book_context else None
    if not coverage:
        return "未提供最新素材覆盖统计。"
    line = (
        f"讲述记录共 {coverage.total_source_count} 条，"
        f"已收入章节 {coverage.covered_source_count} 条，"
        f"未收入章节 {coverage.uncovered_source_count} 条。"
    )
    sources = list((book_context.uncovered_sources if book_context else []) or [])
    if sources:
        source_lines = []
        for index, source in enumerate(sources[:6], start=1):
            excerpt = _clean_reply_text(source.text) or "无文本"
            source_lines.append(f"待收录第 {index} 条｜语音记录 {source.source_index}｜摘录：{excerpt[:80]}")
        line += "\n待收录记录列表：\n" + "\n".join(source_lines)
    return line


def _conversation_prompt_lines(conversation_context: list[Any] | None) -> str:
    lines: list[str] = []
    for item in (conversation_context or [])[-6:]:
        role = getattr(item, "role", "")
        text = _clean_reply_text(getattr(item, "text", ""))
        if role not in {"user", "assistant"} or not text:
            continue
        label = "用户" if role == "user" else "AI"
        summary = _clean_reply_text(getattr(item, "summary", None))
        if summary:
            lines.append(f"{label}：{text}（{summary}）")
        else:
            lines.append(f"{label}：{text}")
    return "\n".join(lines) or "无"


def _chapter_prompt_lines(
    plan: MemoirBookPlan,
    *,
    source_count_by_chapter: dict[str, int] | None = None,
) -> str:
    lines = []
    for index, chapter in enumerate(_sorted_chapters(plan), start=1):
        content = latest_draft_content(chapter)
        excerpt = content[:80].replace("\n", " ")
        line = (
            f"{index}. {chapter.title}"
            f"｜素材 {chapter_source_count(chapter, source_count_by_chapter)} 条"
            f"｜正文 {draft_char_count(chapter)} 字"
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
    return f"第 {index} 章，{chapter.title}，正文 {draft_char_count(chapter)} 字"
