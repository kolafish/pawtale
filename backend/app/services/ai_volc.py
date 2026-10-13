from collections.abc import AsyncIterator
import time

from pydantic import BaseModel

from app.config import get_settings
from app.services.ai_base import AITextServiceBase
from app.services.ai_calls import AITextCallResult, build_text_call_result
from app.services.ai_streaming import (
    AITextStreamChunk,
    volc_responses_stream_chunk,
)

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

    def _request_payload(
        self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int,
    ) -> dict:
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

        return payload

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
        payload = self._request_payload(system_prompt, user_prompt, temperature, max_tokens)
        if text_format is not None:
            payload["text"] = {"format": text_format}

        result = await self._request_json(f"{self.base_url}/responses", payload, timeout)

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
        return self._stream_request(
            f"{self.base_url}/responses", payload, timeout, volc_responses_stream_chunk,
        )

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
