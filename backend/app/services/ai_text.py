from collections.abc import AsyncIterator

from app.config import get_settings
from app.services.ai_base import AITextServiceBase
from app.services.ai_streaming import (
    AITextStreamChunk,
    openai_chat_stream_chunk,
)

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

    def _request_payload(
        self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int,
    ) -> dict:
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

        return payload

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
        payload = self._request_payload(system_prompt, user_prompt, temperature, max_tokens)

        result = await self._request_json(f"{self.base_url}/v1/chat/completions", payload, timeout)

        content = result["choices"][0]["message"]["content"]

        if return_raw:
            return content, result
        return content

    def stream_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
    ) -> AsyncIterator[AITextStreamChunk]:
        payload = self._request_payload(system_prompt, user_prompt, temperature, max_tokens)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        return self._stream_request(
            f"{self.base_url}/v1/chat/completions", payload, timeout, openai_chat_stream_chunk,
        )
