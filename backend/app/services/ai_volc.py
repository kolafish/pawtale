from collections.abc import AsyncIterator

import httpx

from app.config import get_settings
from app.services.ai_base import AITextServiceBase
from app.services.ai_streaming import (
    AITextStreamChunk,
    iter_sse_json_events,
    volc_responses_stream_chunk,
)
from app.services.provider_limits import provider_concurrency_limiter

settings = get_settings()


class VolcArkTextService(AITextServiceBase):
    """VolcEngine Ark text generation service."""

    def __init__(self, model: str | None = None):
        self.api_key = settings.volc_ark_api_key
        self.base_url = settings.volc_ark_base_url.rstrip("/")
        self.model = model or settings.volc_ark_model
        self.thinking_type = settings.volc_ark_thinking_type.strip()
        self.max_concurrent_requests = settings.ai_text_max_concurrent_requests

    @property
    def provider_name(self) -> str:
        return "volc_ark"

    @property
    def model_name(self) -> str:
        return self.model

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 60.0,
        return_raw: bool = False,
    ) -> str | tuple[str, dict]:
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                }
            ],
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system_prompt.strip():
            payload["instructions"] = system_prompt
        if self.thinking_type:
            payload["thinking"] = {"type": self.thinking_type}

        async def request() -> dict:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()

        result = await provider_concurrency_limiter.run(
            "ai_text",
            self.max_concurrent_requests,
            request,
        )

        content = self._extract_response_text(result)
        if not content:
            raise ValueError("Volc Ark returned empty or unsupported response content")

        if return_raw:
            return content, result
        return content

    async def stream_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float = 60.0,
    ) -> AsyncIterator[AITextStreamChunk]:
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                }
            ],
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "stream": True,
        }
        if system_prompt.strip():
            payload["instructions"] = system_prompt
        if self.thinking_type:
            payload["thinking"] = {"type": self.thinking_type}

        async with provider_concurrency_limiter.slot("ai_text", self.max_concurrent_requests):
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout,
                ) as response:
                    response.raise_for_status()
                    async for event in iter_sse_json_events(response.aiter_lines()):
                        chunk: AITextStreamChunk | None = volc_responses_stream_chunk(event)
                        if chunk:
                            yield chunk

    def _extract_response_text(self, result: dict) -> str:
        output_text = result.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        output = result.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    text = part.get("text")
                    if isinstance(text, str) and text.strip() and part.get("type") in (
                        None,
                        "output_text",
                        "text",
                    ):
                        parts.append(text.strip())
            if parts:
                return "\n".join(parts).strip()

        choices = result.get("choices", [])
        if not isinstance(choices, list) or not choices:
            return ""
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
        raw_content = message.get("content") if isinstance(message, dict) else None
        return self._normalize_message_content(raw_content)

    def _normalize_message_content(self, raw_content: str | list | dict | None) -> str:
        if isinstance(raw_content, str):
            return raw_content.strip()
        if isinstance(raw_content, list):
            parts: list[str] = []
            for item in raw_content:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()
        if isinstance(raw_content, dict):
            text = raw_content.get("text")
            if isinstance(text, str):
                return text.strip()
        return ""
