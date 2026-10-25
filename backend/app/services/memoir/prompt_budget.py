"""Reject review requests that exceed the bounded model input window."""

from fastapi import HTTPException
import re


MAX_REVIEW_PROMPT_CHARS = 100_000


def representative_excerpt(value: str, *, limit: int) -> str:
    """Keep the full text when possible, otherwise sample beginning, middle and end."""
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    if limit < 5:
        return text[:max(0, limit)]
    width = (limit - 2) // 3
    middle = (len(text) - width) // 2
    tail_width = limit - 2 - 2 * width
    return f"{text[:width]}…{text[middle:middle + width]}…{text[-tail_width:]}"


def checked_review_prompt(prompt: str, *, subject: str) -> str:
    if len(prompt) > MAX_REVIEW_PROMPT_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"{subject}审阅输入过长，无法一次完整处理；请先缩短当前内容后重试",
        )
    return prompt
