"""Reject review requests that exceed the bounded model input window."""

from fastapi import HTTPException


MAX_REVIEW_PROMPT_CHARS = 100_000


def checked_review_prompt(prompt: str, *, subject: str) -> str:
    if len(prompt) > MAX_REVIEW_PROMPT_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"{subject}审阅输入过长，无法一次完整处理；请先缩短当前内容后重试",
        )
    return prompt
