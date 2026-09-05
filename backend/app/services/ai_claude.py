from collections.abc import AsyncIterator
import json
import time

import httpx
from pydantic import BaseModel

from app.config import get_settings
from app.services.ai_calls import AITextCallResult, build_text_call_result
from app.services.ai_base import AITextServiceBase
from app.services.ai_timeouts import (
    AIProviderTimeoutError,
    effective_ai_text_timeout_seconds,
)
from app.services.ai_streaming import (
    AITextStreamChunk,
    anthropic_messages_stream_chunk,
    iter_sse_json_events,
)
from app.services.provider_limits import provider_concurrency_limiter

settings = get_settings()

CLAUDE_FABLE_MODEL = "claude-fable-5"
CLAUDE_OPUS_4_8_MODEL = "claude-opus-4-8"
STRUCTURED_RESULT_TOOL_NAME = "emit_structured_result"


class ClaudeTextService(AITextServiceBase):
    """Claude 文本生成服务（通过 OpenAI 兼容代理）"""

    def __init__(self, model: str = CLAUDE_OPUS_4_8_MODEL):
        self.auth_token = settings.anthropic_auth_token
        self.base_url = settings.anthropic_base_url
        model_map = {
            CLAUDE_FABLE_MODEL: CLAUDE_FABLE_MODEL,
            CLAUDE_OPUS_4_8_MODEL: CLAUDE_OPUS_4_8_MODEL,
            "claude-sonnet-4": "claude-sonnet-4-6",
            "claude-sonnet-4-6": "claude-sonnet-4-6",
        }
        if model not in model_map:
            raise ValueError(f"不支持的 Claude 模型：{model}")
        self.model = model_map[model]
        self.max_concurrent_requests = settings.ai_text_max_concurrent_requests

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def model_name(self) -> str:
        return self.model

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
        return_raw: bool = False,
    ) -> str | tuple[str, dict]:
        """调用 Claude API（通过 OpenAI 兼容代理）

        代理忽略 system role，所以将 system_prompt 合并到 user message。
        """
        merged = f"{system_prompt}\n\n{user_prompt}" if system_prompt.strip() else user_prompt
        request_timeout = effective_ai_text_timeout_seconds(timeout)

        async def request() -> dict:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self._chat_completions_url(),
                        headers={
                            "Authorization": f"Bearer {self.auth_token}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model,
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                            "messages": [
                                {"role": "user", "content": merged},
                            ],
                        },
                        timeout=request_timeout,
                    )
                    response.raise_for_status()
                    return response.json()
            except httpx.TimeoutException as exc:
                raise AIProviderTimeoutError(
                    provider=self.provider_name,
                    model=self.model_name,
                    timeout_seconds=request_timeout,
                ) from exc

        result = await provider_concurrency_limiter.run(
            "ai_text",
            self.max_concurrent_requests,
            request,
        )

        content = self._extract_response_text(result)
        if not content:
            raise ValueError("Claude 返回内容为空或格式无法解析")

        if return_raw:
            return content, result
        return content

    def _chat_completions_url(self) -> str:
        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

    def _messages_url(self) -> str:
        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            return f"{base_url}/messages"
        return f"{base_url}/v1/messages"

    async def _generate_structured_once(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        output_model: type[BaseModel],
        requested_model: str | None,
        temperature: float,
        max_tokens: int,
        timeout: float | None,
        attempt_count: int,
    ) -> tuple[AITextCallResult, object | None]:
        request_timeout = effective_ai_text_timeout_seconds(timeout)
        merged = (
            f"{system_prompt}\n\n{user_prompt}"
            if system_prompt.strip()
            else user_prompt
        )
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": merged}],
            "stream": False,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": STRUCTURED_RESULT_TOOL_NAME,
                        "description": "提交最终结构化结果，不要把结果写成普通文本。",
                        "strict": True,
                        "parameters": output_model.model_json_schema(),
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": STRUCTURED_RESULT_TOOL_NAME},
            },
        }

        async def request() -> dict:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self._chat_completions_url(),
                        headers={
                            "Authorization": f"Bearer {self.auth_token}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=request_timeout,
                    )
                    response.raise_for_status()
                    return response.json()
            except httpx.TimeoutException as exc:
                raise AIProviderTimeoutError(
                    provider=self.provider_name,
                    model=self.model_name,
                    timeout_seconds=request_timeout,
                ) from exc

        started_at = time.monotonic()
        response_json = await provider_concurrency_limiter.run(
            "ai_text",
            self.max_concurrent_requests,
            request,
        )
        arguments = self._structured_function_arguments(response_json)
        structured_payload: object = []
        if arguments:
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                pass
            else:
                structured_payload = parsed if parsed is not None else []
        result = build_text_call_result(
            (arguments, response_json),
            client=self,
            requested_model=requested_model,
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
            attempt_count=attempt_count,
        )
        return result, structured_payload

    @staticmethod
    def _structured_function_arguments(response_json: dict) -> str:
        choices = response_json.get("choices")
        if not isinstance(choices, list):
            return ""
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                if function.get("name") != STRUCTURED_RESULT_TOOL_NAME:
                    continue
                arguments = function.get("arguments")
                return arguments.strip() if isinstance(arguments, str) else ""
        return ""

    async def stream_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
    ) -> AsyncIterator[AITextStreamChunk]:
        request_timeout = effective_ai_text_timeout_seconds(timeout)
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": user_prompt}],
            "stream": True,
        }
        if system_prompt.strip():
            payload["system"] = system_prompt
        try:
            async with provider_concurrency_limiter.slot(
                "ai_text",
                self.max_concurrent_requests,
            ):
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST",
                        self._messages_url(),
                        headers={
                            "Authorization": f"Bearer {self.auth_token}",
                            "x-api-key": self.auth_token,
                            "anthropic-version": "2023-06-01",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=request_timeout,
                    ) as response:
                        response.raise_for_status()
                        async for event in iter_sse_json_events(
                            response.aiter_lines()
                        ):
                            chunk = anthropic_messages_stream_chunk(event)
                            if chunk is not None:
                                yield chunk
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError(
                provider=self.provider_name,
                model=self.model_name,
                timeout_seconds=request_timeout,
            ) from exc

    def _extract_response_text(self, result: dict) -> str:
        """兼容不同代理/网关返回的 Claude 内容格式"""
        # OpenAI 兼容格式：choices[0].message.content
        choices = result.get("choices", [])
        if isinstance(choices, list) and choices:
            first_choice = choices[0] if isinstance(choices[0], dict) else {}
            message = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
            raw_content = message.get("content") if isinstance(message, dict) else None
            content = self._normalize_message_content(raw_content)
            if content:
                return content
            # 兼容少数网关把文本放在 choices[0].text
            text = first_choice.get("text") if isinstance(first_choice, dict) else None
            if isinstance(text, str) and text.strip():
                return text.strip()

        # Anthropic 原生格式：content: [{type:'text', text:'...'}]
        content_blocks = result.get("content")
        if isinstance(content_blocks, list):
            parts: list[str] = []
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                text = block.get("text")
                if isinstance(text, str) and text.strip() and block_type in (None, "text"):
                    parts.append(text.strip())
            if parts:
                return "\n".join(parts).strip()

        # 兜底：content 是纯字符串
        if isinstance(content_blocks, str) and content_blocks.strip():
            return content_blocks.strip()

        return ""
