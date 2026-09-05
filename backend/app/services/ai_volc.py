from collections.abc import AsyncIterator
import time

import httpx
from pydantic import BaseModel

from app.config import get_settings
from app.services.ai_base import AITextServiceBase
from app.services.ai_calls import AITextCallResult, build_text_call_result
from app.services.ai_timeouts import (
    AIProviderTimeoutError,
    effective_ai_text_timeout_seconds,
)
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
        timeout: float | None = None,
        return_raw: bool = False,
        text_format: dict[str, object] | None = None,
    ) -> str | tuple[str, dict]:
        request_timeout = effective_ai_text_timeout_seconds(timeout)
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
        if text_format is not None:
            payload["text"] = {"format": text_format}

        async def request() -> dict:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/responses",
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

        content = self._extract_response_text(result)
        if not content:
            raise ValueError("Volc Ark returned empty or unsupported response content")

        if return_raw:
            return content, result
        return content

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
        started_at = time.monotonic()
        raw = await self._call_llm(
            system_prompt,
            user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            return_raw=True,
            text_format={
                "type": "json_schema",
                "name": "structured_result",
                "strict": True,
                "schema": output_model.model_json_schema(),
            },
        )
        return (
            build_text_call_result(
                raw,
                client=self,
                requested_model=requested_model,
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
                attempt_count=attempt_count,
            ),
            None,
        )

    async def stream_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
    ) -> AsyncIterator[AITextStreamChunk]:
        request_timeout = effective_ai_text_timeout_seconds(timeout)
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

        try:
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
                        timeout=request_timeout,
                    ) as response:
                        response.raise_for_status()
                        async for event in iter_sse_json_events(response.aiter_lines()):
                            chunk: AITextStreamChunk | None = volc_responses_stream_chunk(event)
                            if chunk:
                                yield chunk
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError(
                provider=self.provider_name,
                model=self.model_name,
                timeout_seconds=request_timeout,
            ) from exc

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
