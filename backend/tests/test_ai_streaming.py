import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from app.services.ai_text import DeepSeekTextService
from app.services.ai_volc import VolcArkTextService
from app.services.ai_timeouts import AIProviderTimeoutError
from app.services.ai_streaming import (
    iter_sse_json_events,
    openai_chat_stream_chunk,
    volc_responses_stream_chunk,
)
from app.services.provider_limits import ProviderConcurrencyLimiter


async def _lines(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_iter_sse_json_events_parses_data_frames():
    events = [
        event
        async for event in iter_sse_json_events(
            _lines([
                ": keep-alive",
                "data: {\"a\": 1}",
                "",
                "data: {\"b\":",
                "data:  2}",
                "",
                "data: [DONE]",
                "",
            ])
        )
    ]

    assert events == [{"a": 1}, {"b": 2}]


def test_openai_chat_stream_chunk_extracts_text_and_finish_reason():
    chunk = openai_chat_stream_chunk(
        {
            "choices": [
                {
                    "delta": {"content": "你好"},
                    "finish_reason": None,
                }
            ]
        }
    )

    assert chunk is not None
    assert chunk.text_delta == "你好"
    assert chunk.finish_reason is None

    done = openai_chat_stream_chunk(
        {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 3},
        }
    )

    assert done is not None
    assert done.text_delta == ""
    assert done.finish_reason == "stop"
    assert done.usage == {"completion_tokens": 3}


def test_volc_responses_stream_chunk_extracts_delta_and_completion_usage():
    chunk = volc_responses_stream_chunk(
        {
            "type": "response.output_text.delta",
            "delta": "接着说",
        }
    )

    assert chunk is not None
    assert chunk.text_delta == "接着说"

    done = volc_responses_stream_chunk(
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "usage": {"output_tokens": 5},
            },
        }
    )

    assert done is not None
    assert done.finish_reason == "completed"
    assert done.usage == {"output_tokens": 5}


@pytest.mark.asyncio
async def test_provider_concurrency_limiter_slot_holds_until_context_exits():
    limiter = ProviderConcurrencyLimiter()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = False

    async def first() -> str:
        async with limiter.slot("ai_text", 1):
            first_entered.set()
            await release_first.wait()
            return "first"

    async def second() -> str:
        nonlocal second_entered
        async with limiter.slot("ai_text", 1):
            second_entered = True
            return "second"

    first_task = asyncio.create_task(first())
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0.01)

    assert not second_entered

    release_first.set()
    assert await first_task == "first"
    assert await second_task == "second"
    assert second_entered


@pytest.mark.parametrize("service_type", [DeepSeekTextService, VolcArkTextService])
@pytest.mark.parametrize("stop", ["close", "timeout"])
async def test_provider_stream_releases_http_connection_and_slot(monkeypatch, service_type, stop):
    service = service_type()
    service.max_concurrent_requests = 1
    limiter = ProviderConcurrencyLimiter()
    monkeypatch.setattr("app.services.ai_base.provider_concurrency_limiter", limiter)
    closed = False

    class ResponseStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            raise httpx.ReadTimeout("stream interrupted")

        async def aclose(self):
            nonlocal closed
            closed = True

    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=ResponseStream()))
    monkeypatch.setattr("httpx.AsyncClient", lambda: client_type(transport=transport))
    stream = service.stream_llm("system", "user", timeout=12)
    assert (await anext(stream)).text_delta == "hello"
    if stop == "close":
        await stream.aclose()
    else:
        with pytest.raises(AIProviderTimeoutError) as error:
            await anext(stream)
        assert error.value.provider == service.provider_name
        assert error.value.model == service.model_name
        assert error.value.timeout_seconds == 12
    assert closed
    # A new call must not remain blocked after cancellation or a network timeout.
    assert await asyncio.wait_for(limiter.run("ai_text", 1, lambda: asyncio.sleep(0, result=True)), 1)
