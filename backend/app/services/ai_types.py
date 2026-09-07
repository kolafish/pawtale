from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

from app.services.ai_streaming import AITextStreamChunk


if TYPE_CHECKING:
    from app.services.ai_calls import AITextCallResult, AIStructuredCallResult, StructuredModel


class AITextClient(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> str:
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
        ...

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
        ...

    def _wrap_debug(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        content: str,
        response_json: dict | None,
    ) -> dict:
        ...

    def stream_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        timeout: float | None = None,
    ) -> AsyncIterator[AITextStreamChunk]:
        ...
