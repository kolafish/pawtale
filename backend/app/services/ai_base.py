from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
import time

import httpx
from pydantic import BaseModel

from app.services.ai_calls import (
    AIStructuredCallResult,
    AIStructuredOutputError,
    AITextCallResult,
    StructuredModel,
    build_text_call_result,
    validate_structured_response,
    with_attempt_totals,
)
from app.services.ai_debug import build_debug_info
from app.services.ai_streaming import AITextStreamChunk, iter_sse_json_events
from app.services.ai_timeouts import AIProviderTimeoutError, effective_ai_text_timeout_seconds
from app.services.provider_limits import provider_concurrency_limiter


class AITextServiceBase(ABC):
    """Provider transport contract for AI text generation."""

    api_key: str
    max_concurrent_requests: int

    async def _request_json(self, url: str, payload: dict, timeout: float | None) -> dict:
        request_timeout = effective_ai_text_timeout_seconds(timeout)

        async def request() -> dict:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
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

        return await provider_concurrency_limiter.run(
            "ai_text",
            self.max_concurrent_requests,
            request,
        )

    async def _stream_request(
        self, url: str, payload: dict, timeout: float | None,
        parse_event: Callable[[dict], AITextStreamChunk | None],
    ) -> AsyncIterator[AITextStreamChunk]:
        request_timeout = effective_ai_text_timeout_seconds(timeout)
        try:
            async with provider_concurrency_limiter.slot("ai_text", self.max_concurrent_requests):
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST",
                        url,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=request_timeout,
                    ) as response:
                        response.raise_for_status()
                        async for event in iter_sse_json_events(response.aiter_lines()):
                            chunk = parse_event(event)
                            if chunk:
                                yield chunk
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError(
                provider=self.provider_name,
                model=self.model_name,
                timeout_seconds=request_timeout,
            ) from exc

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


    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @abstractmethod
    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
        return_raw: bool = False,
    ) -> str | tuple[str, dict]:
        ...

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        requested_model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
        attempt_count: int = 1,
    ) -> AITextCallResult:
        started_at = time.monotonic()
        raw = await self._call_llm(
            system_prompt,
            user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            return_raw=True,
        )
        return build_text_call_result(
            raw,
            client=self,
            requested_model=requested_model,
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
            attempt_count=attempt_count,
        )

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
        response = await self.generate_text(
            system_prompt,
            user_prompt,
            requested_model=requested_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            attempt_count=attempt_count,
        )
        return response, None

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        output_model: type[StructuredModel],
        requested_model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4000,
        timeout: float | None = None,
        max_attempts: int = 1,
    ) -> AIStructuredCallResult[StructuredModel]:
        last_error: AIStructuredOutputError | None = None
        started_at = time.monotonic()
        responses: list[AITextCallResult] = []
        for attempt in range(1, max(1, max_attempts) + 1):
            attempt_prompt = user_prompt
            if last_error is not None:
                attempt_prompt += (
                    "\n上次输出未通过结构校验。"
                    "请重新生成完整结果，确保所有字符串和字段都符合约定结构。"
                )
            response, payload = await self._generate_structured_once(
                system_prompt,
                attempt_prompt,
                output_model=output_model,
                requested_model=requested_model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                attempt_count=attempt,
            )
            responses.append(response)
            total_response = with_attempt_totals(
                response,
                attempts=responses,
                elapsed_ms=round(
                    (time.monotonic() - started_at) * 1000
                ),
            )
            try:
                validated = validate_structured_response(
                    total_response,
                    output_model=output_model,
                    payload=payload,
                )
                return validated
            except AIStructuredOutputError as exc:
                last_error = type(exc)(
                    str(exc),
                    response=exc.response,
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("structured generation did not run")

    def stream_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
    ) -> AsyncIterator[AITextStreamChunk]:
        raise NotImplementedError(f"{self.provider_name} does not support streaming text generation")

    def _wrap_debug(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        content: str,
        response_json: dict | None,
    ) -> dict:
        return build_debug_info(
            request_info={
                "provider": self.provider_name,
                "model": self.model_name,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
            response_text=content,
            response_json=response_json,
        )
