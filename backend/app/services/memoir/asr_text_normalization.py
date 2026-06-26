from __future__ import annotations

import re


_CHINESE_NUMBER = r"0-9一二两三四五六七八九十"
_CHAPTER_HOMOPHONE_RE = re.compile(r"(?:张杰|张洁|张节|章杰)")
_CHAPTER_WORD_ASR_ORDINAL_RE = re.compile(rf"第\s*([{_CHINESE_NUMBER}]+)\s*张")
_CHAPTER_WORD_ASR_BARE_RE = re.compile(
    rf"([{_CHINESE_NUMBER}]+)\s*张(?=(?:和|跟|与|的|换|调|移|顺序|章节|章|[，。,.!！?？、\s]|$))"
)
_CHAPTER_COUNT_ASR_REPLACEMENTS = (
    (re.compile(r"几\s*张"), "几章"),
    (re.compile(r"多少\s*张"), "多少章"),
)
_CHAPTER_HOMOPHONE_BEFORE_RE = re.compile(
    r"(?:"
    r"书的|本书的|成书的|目录的|回忆录的|"
    r"未\s*收入|未\s*收录|没\s*收入|没\s*收录|"
    r"归\s*到|归\s*入|收\s*录\s*到|收\s*入\s*到|放\s*到|分\s*到|"
    r"(?:现在有|一共有|总共有)?\s*(?:几|几个|多少|哪些|哪个|每个|所有|全部)|"
    r"不是.{0,8}人名的?"
    r")\s*$"
)
_CHAPTER_HOMOPHONE_AFTER_RE = re.compile(
    r"^\s*(?:"
    r"规划|结构|顺序|目录|标题|题目|章名|正文|内容|归属|"
    r"归入|归到|收录|收入|改名|改成|移动|上移|下移|合并|拆分|确认"
    r")"
)


def normalize_memoir_asr_text(text: str) -> str:
    """Normalize high-frequency memoir/book terms commonly misheard by ASR."""
    normalized = text
    normalized = _CHAPTER_WORD_ASR_ORDINAL_RE.sub(r"第\1章", normalized)
    normalized = _CHAPTER_WORD_ASR_BARE_RE.sub(r"\1章", normalized)
    for pattern, replacement in _CHAPTER_COUNT_ASR_REPLACEMENTS:
        normalized = pattern.sub(replacement, normalized)
    return _CHAPTER_HOMOPHONE_RE.sub(lambda match: _chapter_homophone_replacement(normalized, match), normalized)


def _chapter_homophone_replacement(text: str, match: re.Match[str]) -> str:
    before = text[max(0, match.start() - 18):match.start()]
    after = text[match.end():match.end() + 18]
    if _CHAPTER_HOMOPHONE_BEFORE_RE.search(before) or _CHAPTER_HOMOPHONE_AFTER_RE.search(after):
        return "章节"
    return match.group(0)
