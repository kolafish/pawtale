from collections.abc import AsyncIterator

import httpx
from app.config import get_settings
from app.services.ai_base import AITextServiceBase
from app.services.ai_timeouts import (
    AIProviderTimeoutError,
    effective_ai_text_timeout_seconds,
)
from app.services.ai_streaming import (
    AITextStreamChunk,
    iter_sse_json_events,
    openai_chat_stream_chunk,
)
from app.services.provider_limits import provider_concurrency_limiter

settings = get_settings()


class DeepSeekTextService(AITextServiceBase):
    """DeepSeek 文本生成服务"""

    def __init__(self, model: str | None = None):
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url
        self.model = model or settings.deepseek_model
        self.thinking_type = settings.deepseek_thinking_type.strip()
        self.max_concurrent_requests = settings.ai_text_max_concurrent_requests

    @property
    def provider_name(self) -> str:
        return "deepseek"

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
        """调用 DeepSeek API（原生 OpenAI 兼容格式，支持 system role）"""
        request_timeout = effective_ai_text_timeout_seconds(timeout)
        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.thinking_type:
            payload["thinking"] = {"type": self.thinking_type}

        async def request() -> dict:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
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

        result = await provider_concurrency_limiter.run(
            "ai_text",
            self.max_concurrent_requests,
            request,
        )

        content = result["choices"][0]["message"]["content"]

        if return_raw:
            return content, result
        return content

    async def stream_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
    ) -> AsyncIterator[AITextStreamChunk]:
        request_timeout = effective_ai_text_timeout_seconds(timeout)
        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.thinking_type:
            payload["thinking"] = {"type": self.thinking_type}

        try:
            async with provider_concurrency_limiter.slot("ai_text", self.max_concurrent_requests):
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=request_timeout,
                    ) as response:
                        response.raise_for_status()
                        async for event in iter_sse_json_events(response.aiter_lines()):
                            chunk: AITextStreamChunk | None = openai_chat_stream_chunk(event)
                            if chunk:
                                yield chunk
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError(
                provider=self.provider_name,
                model=self.model_name,
                timeout_seconds=request_timeout,
            ) from exc
