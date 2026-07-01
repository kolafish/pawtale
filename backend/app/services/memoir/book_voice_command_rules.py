from __future__ import annotations

import re
from typing import Any, Protocol

from app.services.memoir.asr_text_normalization import normalize_memoir_asr_text

SUPPORTED_INTENTS = {
    "read_outline",
    "read_chapter",
    "read_uncovered_source",
    "rename_chapter",
    "merge_chapters",
    "move_chapter",
    "assign_source",
    "split_chapter",
    "rewrite_chapter",
    "polish_chapter",
    "confirm_chapter",
    "review_chapter_title",
    "review_chapter_length",
    "review_chapter_structure",
    "answer_book_question",
    "capability_help",
    "clarify_previous",
    "unknown",
}

INTENT_LABELS = {
    "read_outline": "朗读目录",
    "read_chapter": "朗读章节",
    "read_uncovered_source": "朗读记录",
    "rename_chapter": "修改章名",
    "merge_chapters": "合并章节",
    "move_chapter": "移动章节",
    "assign_source": "归入记录",
    "split_chapter": "拆分章节",
    "rewrite_chapter": "重写章节",
    "polish_chapter": "润色章节",
    "confirm_chapter": "确认章节",
    "review_chapter_title": "章名建议",
    "review_chapter_length": "篇幅建议",
    "review_chapter_structure": "结构建议",
    "answer_book_question": "成书问答",
    "capability_help": "修改帮助",
    "clarify_previous": "需要澄清",
    "unknown": "需要再说明",
}

READ_INTENTS = {"read_outline", "read_chapter"}
CONSULTATION_INTENTS = {"review_chapter_title", "review_chapter_length", "review_chapter_structure"}
ANSWER_INTENTS = {"read_uncovered_source", "answer_book_question", "capability_help"}
CLARIFICATION_INTENTS = {"clarify_previous", "unknown"}
NON_CONFIRMATION_INTENTS = READ_INTENTS | CONSULTATION_INTENTS | ANSWER_INTENTS | CLARIFICATION_INTENTS
EXECUTABLE_MUTATION_INTENTS = {"rename_chapter", "merge_chapters", "move_chapter", "assign_source"}
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
CHAPTER_NUMBER_RE = re.compile(r"第?\s*([0-9]{1,2}|[一二两三四五六七八九十])\s*(?:章|章节)")
STRICT_CHAPTER_REF_RE = re.compile(r"第\s*([0-9]{1,2}|[一二两三四五六七八九十])\s*(?:章|章节)")
RENAME_RE = re.compile(r"(?:把|将)?(.{1,40}?)(?:的)?(?:章名|标题)?(?:改名为|改成|改为|叫)(.{2,40})")
MERGE_INTO_RE = re.compile(r"(?:把|将)?(.{1,40}?)(?:合并到|并到|放到)(.{1,40})")
MERGE_WITH_RE = re.compile(r"(?:把|将)?(.{1,40}?)(?:和|跟|与)(.{1,40}?)(?:合并|并到一起|放到一起)")
MOVE_RE = re.compile(r"(?:把|将)?(.{1,40}?)(上移|下移|往前|往后|前移|后移)")
MOVE_TARGET_VERBS_RE = r"(?:移到|移动到|挪到|放到|排到|调到|调整到)"
MOVE_TO_INDEX_RE = re.compile(
    rf"(?:把|将)?(.{{1,50}}?){MOVE_TARGET_VERBS_RE}第?\s*([0-9]{{1,2}}|[一二两三四五六七八九十])\s*(?:章|章节)(前面|后面|前|后)?"
)
MOVE_TO_POSITION_RE = re.compile(
    rf"(?:把|将)?(.{{1,50}}?){MOVE_TARGET_VERBS_RE}(最前面|最前|开头|第一章|第一个章节|最后一章|最后面|最末尾|末尾|最后|最后一个章节)"
)
ASSIGN_SOURCE_RE = re.compile(r"(?:把|将)?(.{0,40}?)(?:归到|归入|收录到|放到|分到|归类到)(.{1,40})")
TITLE_REVIEW_KEYWORDS = ("换个名字", "改个名字", "名字", "标题", "题目", "章名")
LENGTH_REVIEW_KEYWORDS = ("太短", "太长", "字数", "长度", "篇幅", "内容少", "内容多", "够不够")
STRUCTURE_REVIEW_KEYWORDS = ("结构", "顺序", "节奏", "拆分", "合并", "拆开", "放一起")
QUESTION_KEYWORDS = ("是不是", "是否", "要不要", "需不需要", "需要", "合不合适", "好不好", "好吗", "怎么样", "够不够")
CAPABILITY_KEYWORDS = (
    "可以修改什么",
    "可以修改些啥",
    "可以改什么",
    "可以改些啥",
    "可以改点啥",
    "可以改点什么",
    "还可以改什么",
    "还可以改些啥",
    "还可以改点啥",
    "还可以改点什么",
    "还可以修改什么",
    "还可以修改些啥",
    "能修改什么",
    "能修改些啥",
    "能改什么",
    "能改些啥",
    "还能改什么",
    "还能改些啥",
    "还能改点啥",
    "还能改点什么",
    "还能修改什么",
    "还能修改些啥",
    "改点啥",
    "改点什么",
    "改哪些",
    "修改哪些",
    "怎么修改",
    "怎么改",
    "能做什么",
    "可以做什么",
    "会做什么",
)
CHAPTER_COUNT_KEYWORDS = (
    "几章",
    "几张",
    "几个章",
    "几个章节",
    "多少章",
    "多少章节",
    "目录有几",
    "现在有几",
    "现在都有几",
    "一共有几",
    "总共有几",
    "章节有几",
    "章有几个",
)
OUTLINE_STATUS_KEYWORDS = ("有哪些章节", "都有哪些章", "现在有什么章", "目录里有什么", "目录是什么", "现在目录")
SOURCE_COVERAGE_KEYWORDS = (
    "未收入",
    "未收录",
    "未归入",
    "未归到",
    "未归类",
    "没收入",
    "没收录",
    "没归入",
    "没归到",
    "没归类",
    "还没收入",
    "还没收录",
    "还没归入",
    "还没归到",
    "还没归类",
)
SOURCE_COVERAGE_OBJECT_KEYWORDS = ("记录", "素材", "讲述", "语音", "转写")
SOURCE_READ_KEYWORDS = (
    "念一条",
    "读一条",
    "念一下",
    "读一下",
    "念下",
    "读下",
    "给我念",
    "给我读",
    "先念",
    "先读",
    "下一条",
    "下一个",
    "一条一条",
    "逐条",
)
SOURCE_ASSIGN_KEYWORDS = ("归到", "归入", "收录到", "放到", "分到", "归类到")
CURRENT_SOURCE_REFS = ("这条", "这一条", "刚才这条", "刚念的", "刚读的", "它", "这个")
CHAPTER_SWAP_KEYWORDS = (
    "顺序换",
    "顺序调换",
    "顺序交换",
    "交换顺序",
    "换一下",
    "换一换",
    "互换",
    "对调",
    "调换",
    "换成",
)
AMBIGUOUS_FOLLOW_UPS = {"那个", "这个", "那", "嗯", "现在呢", "然后呢", "继续", "就是那个", "刚才那个", "前面那个"}


class MovePayloadTarget(Protocol):
    direction: str | None
    target_position: str | None
    target_index: int | None
    relative_chapter_ref: str | None
    relative_position: str | None


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


def _clean_chapter_ref(value: str) -> str:
    text = _compact(value).strip("，。,.!！?？、")
    while text:
        before = text
        text = text.strip("，。,.!！?？、")
        text = re.sub(
            r"^(?:不是啊|不是|我说|我是说|我讲的是|我想|我要|你帮我|帮我|请|麻烦|把|将|那个|这个)",
            "",
            text,
        )
        if text == before:
            break
    return re.sub(
        r"(这?两(?:章|个章节)|两个章节|那一章|这一章|那章|这章|章节|的?顺序|顺序|那个|这个|那|这)$",
        "",
        text,
    ).strip("，。,.!！?？、")


def _is_chapter_count_question(normalized: str) -> bool:
    return any(keyword in normalized for keyword in CHAPTER_COUNT_KEYWORDS)


def _is_source_coverage_question(normalized: str) -> bool:
    return (
        any(keyword in normalized for keyword in SOURCE_COVERAGE_KEYWORDS)
        and any(keyword in normalized for keyword in SOURCE_COVERAGE_OBJECT_KEYWORDS)
    )


def _is_source_read_request(normalized: str) -> bool:
    return (
        any(keyword in normalized for keyword in SOURCE_READ_KEYWORDS)
        and (
            any(keyword in normalized for keyword in SOURCE_COVERAGE_OBJECT_KEYWORDS)
            or any(keyword in normalized for keyword in SOURCE_COVERAGE_KEYWORDS)
            or "条" in normalized
        )
    )


def _source_read_ref(normalized: str) -> str | None:
    if "下一条" in normalized or "下一个" in normalized:
        return "下一条"
    match = re.search(r"第?\s*([0-9]{1,3}|[一二两三四五六七八九十])\s*条", normalized)
    if match:
        return f"待收录第{match.group(1)}条"
    return None


def _clean_source_ref(value: str) -> str:
    text = _compact(value).strip("，。,.!！?？、")
    text = re.sub(r"^(把|将|那个|这个|那条|这条)", "", text)
    return text


def _source_ref_number(ref: str) -> int | None:
    match = re.search(r"([0-9]{1,3}|[一二两三四五六七八九十])", ref)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return CHINESE_NUMBERS.get(raw)


def _is_ambiguous_follow_up(normalized: str) -> bool:
    return normalized in AMBIGUOUS_FOLLOW_UPS or (
        len(normalized) <= 6 and normalized.startswith(("那个", "这个", "刚才", "前面"))
    )


def _response_kind_for_intent(intent: str) -> str:
    if intent in CONSULTATION_INTENTS:
        return "consultation"
    if intent in ANSWER_INTENTS:
        return "answer"
    if intent in CLARIFICATION_INTENTS:
        return "clarification"
    return "action_preview"


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


def _normalized_target_position(value: Any) -> str | None:
    text = _compact(str(value or ""))
    if text in {"first", "最前", "最前面", "开头", "第一章", "第一个章节"}:
        return "first"
    if text in {"last", "最后", "最后一章", "最后面", "末尾", "最末尾", "最后一个章节"}:
        return "last"
    return None


def _normalized_relative_position(value: Any) -> str | None:
    text = _compact(str(value or ""))
    if text in {"before", "前", "前面", "之前"}:
        return "before"
    if text in {"after", "后", "后面", "之后"}:
        return "after"
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = _compact(value)
        if text.isdigit():
            return int(text)
        return CHINESE_NUMBERS.get(text)
    return None


def _payload_has_move_target(payload: MovePayloadTarget) -> bool:
    return (
        payload.direction in {"up", "down"}
        or payload.target_position in {"first", "last"}
        or payload.target_index is not None
        or (payload.relative_chapter_ref is not None and payload.relative_position in {"before", "after"})
    )


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


def _normalize_chapter_word_text(value: str) -> str:
    return normalize_memoir_asr_text(value.strip())


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())
