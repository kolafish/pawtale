from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.services.ai_calls import (
    generate_structured_with_client,
    generate_text_with_client,
)
from app.services.ai_timeouts import ai_text_timeout_seconds
from app.services.ai_types import AITextClient
from app.services.ai_workloads import AITextWorkload, ai_text_workload_policy


ARTICLE_CONTINUE_SYSTEM_PROMPT = """你是一位专业的中文文章编辑和写作搭档。
文章可能是短文、公众号文章、随笔、评论、说明文或其他体裁。请根据原文自行判断写作目的、语气和读者，保持作者已有的表达方向。
不要套用固定文章模板，不要擅自把文章改成小说或回忆录，也不要编造原文没有依据的人物经历、数据和事实。"""

ARTICLE_REVISE_SYSTEM_PROMPT = """你是一位专业的中文文章编辑。
请尊重原文的体裁、目的、观点和作者语气，只按用户的修改意见处理文本。
不要套用固定文章模板，不强行添加标题、小标题、故事结构或公众号格式，也不要编造原文没有依据的事实。"""

ARTICLE_REVIEW_SYSTEM_PROMPT = """你是一位审慎的中文文章审校。
这是普通文章的表达检查，不是回忆录事实完整性检查，也不使用固定文章模板。
只指出正文中真实存在且值得修改的问题；没有问题的维度不要凑数。你可以检查意思是否清楚、前后逻辑、结构衔接、重复和口语残留、措辞与语病。
你不能凭空判断外部事实真伪；遇到可能需要来源支持的陈述，只能建议作者核实。"""


class _ArticleReviewSuggestion(BaseModel):
    excerpt: str = ""
    issue: str
    suggestion: str


class _ArticleReviewOutput(BaseModel):
    summary: str
    suggestions: list[_ArticleReviewSuggestion] = Field(default_factory=list, max_length=8)


@dataclass(frozen=True)
class ArticleTextResult:
    content: str
    debug_info: dict | None = None


@dataclass(frozen=True)
class ArticleReviewResult:
    summary: str
    suggestions: tuple[_ArticleReviewSuggestion, ...]
    debug_info: dict | None = None


def _debug_info(
    client: AITextClient,
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    content: str,
    response_json: dict,
) -> dict:
    return client._wrap_debug(
        system_prompt,
        user_prompt,
        temperature,
        max_tokens,
        content,
        response_json,
    )


async def _generate_article_text(
    client: AITextClient,
    *,
    system_prompt: str,
    user_prompt: str,
    requested_model: str | None,
    temperature: float,
    debug: bool,
) -> ArticleTextResult:
    policy = ai_text_workload_policy(AITextWorkload.GENERATION_TEXT)
    generated = await generate_text_with_client(
        client,
        system_prompt,
        user_prompt,
        requested_model=requested_model,
        temperature=temperature,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
    )
    return ArticleTextResult(
        content=generated.content.strip(),
        debug_info=(
            _debug_info(
                client,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=policy.max_tokens,
                content=generated.content,
                response_json=generated.response_json,
            )
            if debug
            else None
        ),
    )


async def continue_article(
    client: AITextClient,
    *,
    content: str,
    instruction: str | None,
    requested_model: str | None,
    debug: bool,
) -> ArticleTextResult:
    user_prompt = f"""请自然续写下面的文章。

要求：
- 延续原文的主题、观点、语气和行文节奏；
- 不重复已经写过的内容；
- 只输出续写正文，不解释写作过程，不使用代码块。

已有正文：
{content}
"""
    if instruction and instruction.strip():
        user_prompt += f"\n用户对续写方向的补充要求：\n{instruction.strip()}\n"

    return await _generate_article_text(
        client,
        system_prompt=ARTICLE_CONTINUE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        requested_model=requested_model,
        temperature=0.7,
        debug=debug,
    )


async def revise_article(
    client: AITextClient,
    *,
    content: str,
    instruction: str,
    context: str | None,
    requested_model: str | None,
    debug: bool,
) -> ArticleTextResult:
    user_prompt = f"""请按修改意见改写下面的文本。

修改意见：
{instruction.strip()}

待修改文本：
{content}
"""
    if context and context.strip() and context.strip() != content.strip():
        user_prompt += f"\n全文上下文（只用于保持衔接，不要一并重写）：\n{context.strip()}\n"
    user_prompt += "\n只输出修改后的文本，不解释修改过程，不使用代码块。"

    return await _generate_article_text(
        client,
        system_prompt=ARTICLE_REVISE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        requested_model=requested_model,
        temperature=0.6,
        debug=debug,
    )


async def review_article(
    client: AITextClient,
    *,
    content: str,
    requested_model: str | None,
    debug: bool,
) -> ArticleReviewResult:
    user_prompt = f"""请检查下面这篇文章，并返回 JSON：
{{
  "summary": "对当前文章表达质量的简短判断",
  "suggestions": [
    {{
      "excerpt": "对应的原文短句；如果是整体问题可以为空",
      "issue": "问题是什么",
      "suggestion": "具体怎么修改"
    }}
  ]
}}

建议最多 8 条，可以为空。不要重写全文，不要为了填满列表而制造问题。

正文：
{content}
"""
    temperature = 0.2
    policy = ai_text_workload_policy(AITextWorkload.INTERACTIVE_STRUCTURED)
    generated = await generate_structured_with_client(
        client,
        ARTICLE_REVIEW_SYSTEM_PROMPT,
        user_prompt,
        output_model=_ArticleReviewOutput,
        requested_model=requested_model,
        temperature=temperature,
        max_tokens=policy.max_tokens,
        timeout=ai_text_timeout_seconds(policy.timeout_profile),
        max_attempts=policy.max_attempts,
    )
    return ArticleReviewResult(
        summary=generated.value.summary,
        suggestions=tuple(generated.value.suggestions),
        debug_info=(
            _debug_info(
                client,
                system_prompt=ARTICLE_REVIEW_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=policy.max_tokens,
                content=generated.response.content,
                response_json=generated.response.response_json,
            )
            if debug
            else None
        ),
    )
