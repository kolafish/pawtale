from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import json


@dataclass(frozen=True)
class AITextStreamChunk:
    text_delta: str = ""
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


async def iter_sse_json_events(lines: AsyncIterator[str]) -> AsyncIterator[dict]:
    data_lines: list[str] = []
    async for raw_line in lines:
        line = raw_line.strip()
        if not line:
            event = _parse_sse_data_lines(data_lines)
            data_lines = []
            if event is None:
                continue
            if event == "[DONE]":
                return
            yield event
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())

    event = _parse_sse_data_lines(data_lines)
    if isinstance(event, dict):
        yield event


def _parse_sse_data_lines(data_lines: list[str]) -> dict | str | None:
    if not data_lines:
        return None
    data = "\n".join(data_lines).strip()
    if not data:
        return None
    if data == "[DONE]":
        return data
    parsed = json.loads(data)
    return parsed if isinstance(parsed, dict) else None


def openai_chat_stream_chunk(event: dict) -> AITextStreamChunk | None:
    choices = event.get("choices")
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    if not isinstance(choices, list) or not choices:
        return AITextStreamChunk(usage=usage, raw=event) if usage else None

    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    text_delta = _text_from_delta_content(delta.get("content"))
    finish_reason = choice.get("finish_reason")
    finish_reason = finish_reason if isinstance(finish_reason, str) else None
    if not text_delta and not finish_reason and not usage:
        return None
    return AITextStreamChunk(
        text_delta=text_delta,
        finish_reason=finish_reason,
        usage=usage,
        raw=event,
    )


def volc_responses_stream_chunk(event: dict) -> AITextStreamChunk | None:
    openai_chunk = openai_chat_stream_chunk(event)
    if openai_chunk:
        return openai_chunk

    event_type = event.get("type")
    if event_type in {"response.output_text.delta", "response.refusal.delta"}:
        delta = event.get("delta")
        text_delta = delta if isinstance(delta, str) else ""
        return AITextStreamChunk(text_delta=text_delta, raw=event) if text_delta else None

    if event_type in {"response.completed", "response.incomplete"}:
        response = event.get("response")
        response = response if isinstance(response, dict) else event
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        finish_reason = None
        if event_type == "response.incomplete":
            finish_reason = "incomplete"
            incomplete = response.get("incomplete_details")
            if isinstance(incomplete, dict) and isinstance(incomplete.get("reason"), str):
                finish_reason = incomplete["reason"]
        elif isinstance(response.get("status"), str):
            finish_reason = response["status"]
        return AITextStreamChunk(finish_reason=finish_reason, usage=usage, raw=event)

    if event_type in {"response.failed", "error"}:
        message = event.get("message") or event.get("error") or "Volc Ark streaming response failed"
        raise ValueError(str(message))

    return None


def _text_from_delta_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return content["text"]
    return ""
