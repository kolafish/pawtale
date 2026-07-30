from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
import time

from pydantic import BaseModel

from app.services.ai_calls import (
    AIStructuredCallResult,
    AIStructuredOutputError,
    AITextCallResult,
    build_text_call_result,
    validate_structured_response,
    with_attempt_totals,
)
from app.services.ai_debug import build_debug_info
from app.services.ai_streaming import AITextStreamChunk


class AITextServiceBase(ABC):
    """Provider transport contract for AI text generation."""

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
        output_model: type[BaseModel],
        requested_model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4000,
        timeout: float | None = None,
        max_attempts: int = 1,
    ) -> AIStructuredCallResult:
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
