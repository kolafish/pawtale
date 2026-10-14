from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


TEMPLATE_COPY_FORBIDDEN_TERMS = (
    "爷爷主题回忆录",
    "主题回忆录",
    "章节规划",
    "这一章主要记录",
    "从口述内容看",
    "相关的回忆",
    "本章讲述",
    "凡是过往皆是序章",
    "后台",
    "系统生成",
    "AI生成",
    "AI 生成",
)

BACK_COVER_QUOTE_HINTS = (
    "一想起",
    "想起来",
    "心里面",
    "心里",
    "温暖",
    "踏实",
    "记得",
    "所以",
    "因为",
    "家乡",
    "小时候",
)
SHORT_DEFAULT_AFTERWORDS = {"这本书还可以继续补充。", "愿常翻看。", "愿常翻看"}


def _safe_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def brief_text(text: str, *, max_chars: int) -> str:
    normalized = " ".join(text.replace("\r\n", "\n").replace("\r", "\n").split())
    if len(normalized) <= max_chars:
        return normalized
    # Keep the source sentence ending instead of inventing one after a hard cut.
    last_end = max(normalized.rfind(mark, 0, max_chars) for mark in "。！？!?")
    return normalized[: last_end + 1] if last_end >= 0 else ""


def first_sentence(text: str, *, max_chars: int) -> str:
    normalized = " ".join(text.replace("\r\n", "\n").replace("\r", "\n").split())
    if not normalized:
        return ""
    for index, char in enumerate(normalized):
        if char in "。！？!?":
            return brief_text(normalized[: index + 1], max_chars=max_chars)
    return brief_text(normalized, max_chars=max_chars)


def _update_quote_stack(value: str, stack: list[str]) -> bool:
    pairs = {"“": "”", "‘": "’", "「": "」", "『": "』"}
    for char in value:
        if char == '"':
            if stack and stack[-1] == char:
                stack.pop()
            else:
                stack.append(char)
        elif char in pairs:
            stack.append(pairs[char])
        elif char in pairs.values():
            if not stack or stack.pop() != char:
                return False
    return True


def _balanced_quotes(value: str) -> bool:
    stack: list[str] = []
    return _update_quote_stack(value, stack) and not stack


def usable_quote(value: str, *, min_chars: int = 2) -> bool:
    return len(re.sub(r"[\W_]", "", value)) >= min_chars and _balanced_quotes(value) and not contains_template_language(value)


def _sentences(value: str) -> list[str]:
    sentences: list[str] = []
    start: int | None = None
    stack: list[str] = []
    for match in re.finditer(r'''[^。！？!?\n]+[。！？!?]*[”’」』"']*''', value):
        if start is None:
            start = match.start()
        valid = _update_quote_stack(match.group(), stack)
        if not stack or not valid:
            sentences.append(" ".join(value[start:match.end()].split()))
            start = None
            stack.clear()
    if start is not None:
        sentences.append(" ".join(value[start:].split()))
    return sentences


def _signature_sentences(value: str) -> list[str]:
    # Evidence v2 serialization is frozen: display-quality fixes must not
    # invalidate already processed books or trigger another paid AI pass.
    return [" ".join(part.split()) for part in re.findall(r"[^。！？!?\n]+[。！？!?]?", value) if part.strip()]


def original_quote_candidates(chapter: dict[str, Any], *, max_chars: int, for_signature: bool = False) -> dict[str, tuple[str, ...]]:
    """All source runs retained in their own section; split each body only once."""
    sentences = _signature_sentences if for_signature else _sentences
    sections = _list(chapter.get("sections"))
    by_source: dict[str, set[str]] = {}
    for section in sections:
        current = set(sentences(_safe_str(section.get("content"))))
        for source_id in section.get("used_material_ids", []):
            by_source.setdefault(source_id, set()).update(current)
    chapter_sentences = set(sentences(_safe_str(chapter.get("content")))) if not sections else set()
    result = {}
    for ref in _list(chapter.get("source_refs")):
        source_id = _safe_str(ref.get("utterance_id"))
        if not source_id:
            continue
        current = by_source.get(source_id, set()) if sections else chapter_sentences
        runs: list[str] = []
        run = ""
        for sentence in sentences(_safe_str(ref.get("quote"))):
            if sentence in current and len(run + sentence) <= max_chars:
                run += sentence
            else:
                if run:
                    runs.append(run)
                run = sentence if sentence in current and len(sentence) <= max_chars else ""
        if run:
            runs.append(run)
        result[source_id] = tuple(dict.fromkeys(run for run in runs if for_signature or usable_quote(run)))
    return result


def body_excerpt_candidates(content: str, *, max_chars: int) -> tuple[str, ...]:
    sentences = _sentences(content)
    opening = sentences[0] if sentences else ""
    return tuple(dict.fromkeys(sentence for sentence in sentences[1:]
        if sentence != opening and len(sentence) <= max_chars and usable_quote(sentence, min_chars=8)))


def matched_quote(value: str, candidates: list[str] | tuple[str, ...], *, max_chars: int, allow_substring: bool = False) -> str:
    value = " ".join(value.split())
    for opening, closing in (("“", "”"), ("「", "」"), ("『", "』"), ('"', '"'), ("'", "'")):
        if value.startswith(opening) and value.endswith(closing):
            value = value[1:-1].strip()
            break
    if not usable_quote(value):
        return ""
    candidates = [brief_text(candidate, max_chars=max_chars) for candidate in candidates]
    def key(text: str) -> str:
        for opening, closing in (("“", "”"), ("「", "」"), ("『", "』"), ('"', '"'), ("'", "'")):
            if text.startswith(opening) and text.endswith(closing):
                text = text[1:-1].strip()
                break
        return re.sub(r'''[。！？!?]+(?=[”’」』"']*$)''', "", text)
    for candidate in candidates:
        if len(candidate) <= max_chars and usable_quote(candidate) and key(value) == key(candidate):
            return candidate  # Return the supplied evidence with its original punctuation.
    if allow_substring:
        for candidate in candidates:
            if not usable_quote(candidate):
                continue
            if len(value) <= max_chars and value in candidate:
                return value
            if candidate in value:
                return candidate
    return ""


def repair_original_pull_quotes(chapter: dict[str, Any]) -> list[Any]:
    quotes = deepcopy(_list(chapter.get("pull_quotes")))
    source_quotes = {
        _safe_str(_dict(ref).get("utterance_id")): " ".join(_safe_str(_dict(ref).get("quote")).split())
        for ref in _list(chapter.get("source_refs"))
    }
    for raw_quote in list(quotes):
        quote = _dict(raw_quote)
        if quote.get("source") != "original_quote":
            continue
        text = _safe_str(quote.get("text"))
        for source_id in _list(quote.get("source_ref_ids")):
            source = source_quotes.get(_safe_str(source_id), "")
            if len(source) <= 80 or text in source:
                continue
            if text != source[:80].rstrip("，。；、,. ") + "。":
                continue
            replacement = brief_text(source, max_chars=80)
            if replacement:
                quote["text"] = replacement
            else:
                quotes.remove(raw_quote)
            break
    return quotes


def _strip_title_wrappers(title: str) -> str:
    return title.strip().strip("《》「」“”\"' ")


def formal_book_title(raw_title: str) -> str:
    title = _strip_title_wrappers(raw_title.replace("章节规划", "")).strip(" -_·：:")
    title = title.replace("主题回忆录", "回忆录").replace("主题", "").strip(" -_·：:")
    if not title or title == "回忆录":
        return "家庭回忆录"
    if title == "家庭回忆录" or title.endswith("口述回忆录"):
        return title
    if title.endswith("的回忆录"):
        return title
    if title.endswith("回忆录"):
        subject = title[: -len("回忆录")].strip(" -_·：:的")
        return f"{subject}的回忆录" if subject else "家庭回忆录"
    return title


def short_topic(title: str) -> str:
    topic = _strip_title_wrappers(title)
    for word in ("与", "和", "里的", "中的"):
        if word in topic and len(topic) > 7:
            topic = topic.split(word)[0]
            break
    return topic[:8]


def contains_template_language(text: object) -> bool:
    value = _safe_str(text)
    return any(term in value for term in TEMPLATE_COPY_FORBIDDEN_TERMS)


def fallback_preface(chapter_titles: list[str]) -> str:
    topics = []
    for title in chapter_titles:
        topic = short_topic(title)
        if topic and topic not in topics:
            topics.append(topic)
    if topics:
        topic_text = "、".join(topics[:4])
        first_sentence = (
            f"这本书记录的是一次次口述中慢慢浮现出来的生活。里面有{topic_text}，"
            "也有那些多年以后仍然记得的细节。"
        )
    else:
        first_sentence = (
            "这本书记录的是一次次口述中慢慢浮现出来的生活。里面有亲口说出的往事，"
            "也有那些多年以后仍然记得的细节。"
        )
    return (
        f"{first_sentence}\n\n"
        "它不试图概括一个时代，也不把一个人的人生写成宏大的传记，"
        "只是尽量把亲口说出的记忆保存下来，留给家人以后慢慢翻看。"
    )


def expanded_afterword() -> str:
    return (
        "这些讲述有些清楚，有些还需要家人继续补充。记忆本来就不是一次说完的，"
        "它会在某个名字、某个地方、某件旧物出现时，又慢慢回来。\n\n"
        "能先留下来，已经是这本书最重要的意义。以后如果还有新的故事、新的照片，"
        "或者家人想起了更多细节，也可以继续补进来。愿这些话被保存下来，"
        "成为家里人以后还能反复翻看的记忆。"
    )


def fallback_chapter_intro(title: str, content: str) -> str:
    topic = short_topic(title)
    excerpt = first_sentence(content, max_chars=70)
    if topic and excerpt:
        return f"记忆先回到{topic}。{excerpt}"
    if excerpt:
        return f"记忆由一个清楚的场景展开：{excerpt}"
    return f"记忆在「{title}」里慢慢展开，留下讲述者亲口说出的片段和语气。"


def _quote_candidates(chapters: list[Any]) -> list[str]:
    candidates: list[str] = []
    for raw_chapter in chapters:
        chapter = _dict(raw_chapter)
        for raw_quote in _list(chapter.get("pull_quotes")):
            quote = brief_text(_safe_str(_dict(raw_quote).get("text")), max_chars=45)
            if usable_quote(quote, min_chars=8) and quote in " ".join(_safe_str(chapter.get("content")).split()):
                candidates.append(quote)
        candidates.extend(body_excerpt_candidates(_safe_str(chapter.get("content")), max_chars=45))
    result: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in result and not contains_template_language(candidate):
            result.append(candidate)
    return result


def fallback_back_cover_quote(chapters: list[Any]) -> str:
    candidates = _quote_candidates(chapters)
    for candidate in candidates:
        if any(hint in candidate for hint in BACK_COVER_QUOTE_HINTS):
            return candidate
    if candidates:
        return candidates[0]
    return "把亲口说出的生活，尽量完整地留下来。"


def default_generation_note() -> str:
    return (
        "这本书由家人的口述材料整理而成，供家人保存与阅读。"
        "整理过程中保留原始讲述的事实边界，对语句、段落和章节顺序做了必要的梳理，"
        "便于日后阅读和继续修订。\n\n"
        "书中仍可能有时间、地点或姓名需要家人补充确认。若发现遗漏或不准确之处，"
        "可以在后续版本中继续修改。"
    )


def default_version_note() -> str:
    return (
        "本书正文采用适合阅读的整理版表达。原始口述材料已保留，"
        "便于家人日后继续核对、补充和修订。"
    )


def interview_records_note(*, chapter_count: int, source_count: int) -> str:
    return f"本书当前收录 {chapter_count} 章，共整理自 {source_count} 条口述片段。"


def _chapter_source_count(chapter: dict[str, Any]) -> int:
    value = chapter.get("source_count")
    if isinstance(value, int) and value >= 0:
        return value
    refs = _list(chapter.get("source_refs"))
    return len(refs)


def _rewrite_source_terms(text: str) -> str:
    return (
        text.replace("讲述来源", "口述片段")
        .replace("章节来源", "口述片段")
        .replace("后台保留章节来源和口述素材", default_version_note())
    )


def sanitize_manuscript_payload(*, title: str, manuscript: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(manuscript, dict):
        return {}
    cleaned = deepcopy(manuscript)
    profile = _dict(cleaned.get("profile"))
    chapters = _list(cleaned.get("chapters"))
    chapter_titles = [
        _safe_str(_dict(chapter).get("title"))
        for chapter in chapters
        if _safe_str(_dict(chapter).get("title"))
    ]
    normalized_title = _safe_str(title) or _safe_str(profile.get("title")) or "家庭回忆录"
    normalized_subtitle = _safe_str(profile.get("subtitle"))
    if profile:
        profile["title"] = normalized_title
        profile["subtitle"] = normalized_subtitle
        cleaned["profile"] = profile

    for section in _list(cleaned.get("front_matter")):
        if not isinstance(section, dict):
            continue
        kind = _safe_str(section.get("kind"))
        if kind == "cover":
            section["title"] = normalized_title
            section["content"] = "\n".join(part for part in [normalized_title, normalized_subtitle] if part)
        elif kind == "title_page" and contains_template_language(section.get("content")):
            prepared_by = _safe_str(profile.get("prepared_by")) or "家人整理"
            narrator_name = _safe_str(profile.get("narrator_name"))
            lines = [
                normalized_subtitle,
                f"口述：{narrator_name}" if narrator_name else "",
                f"整理：{prepared_by}" if prepared_by else "",
            ]
            section["content"] = "\n\n".join(line for line in lines if line)
        elif kind == "generation_note" and contains_template_language(section.get("content")):
            section["content"] = default_generation_note()
        elif kind == "preface" and (
            not _safe_str(section.get("content"))
            or contains_template_language(section.get("content"))
        ):
            section["content"] = fallback_preface(chapter_titles)

    for raw_chapter in chapters:
        chapter = _dict(raw_chapter)
        if not chapter:
            continue
        intro = _safe_str(chapter.get("intro"))
        if not intro or contains_template_language(intro):
            chapter["intro"] = fallback_chapter_intro(
                _safe_str(chapter.get("title")),
                _safe_str(chapter.get("content")),
            )
        chapter["pull_quotes"] = [
            quote
            for quote in repair_original_pull_quotes(chapter)
            if not contains_template_language(_safe_str(_dict(quote).get("text")))
        ]

    back_matter = _dict(cleaned.get("back_matter"))
    if back_matter:
        chapter_count = len(chapters)
        source_count = sum(_chapter_source_count(_dict(chapter)) for chapter in chapters)
        afterword = _dict(back_matter.get("afterword"))
        afterword_content = _safe_str(afterword.get("content"))
        if afterword and (
            not afterword_content
            or afterword_content in SHORT_DEFAULT_AFTERWORDS
            or contains_template_language(afterword_content)
        ):
            afterword["content"] = expanded_afterword()
        interview_records = _dict(back_matter.get("interview_records"))
        if interview_records:
            interview_records["title"] = "口述片段"
            content = _rewrite_source_terms(_safe_str(interview_records.get("content")))
            interview_records["content"] = (
                content
                if content and not contains_template_language(content)
                else interview_records_note(chapter_count=chapter_count, source_count=source_count)
            )
        version_note = _dict(back_matter.get("version_note"))
        if version_note:
            version_note["title"] = "版本与修订"
            content = _rewrite_source_terms(_safe_str(version_note.get("content")))
            version_note["content"] = (
                content
                if content and not contains_template_language(content)
                else default_version_note()
            )
        source_appendix = _dict(back_matter.get("source_appendix"))
        if source_appendix:
            source_appendix["title"] = "口述片段"
            source_appendix["content"] = _rewrite_source_terms(_safe_str(source_appendix.get("content")))
        cleaned["back_matter"] = back_matter

    settings = _dict(cleaned.get("render_settings"))
    if settings is not None:
        quote = _safe_str(settings.get("back_cover_quote"))
        if not quote or contains_template_language(quote):
            settings["back_cover_quote"] = fallback_back_cover_quote(chapters)
        cleaned["render_settings"] = settings
    return cleaned
