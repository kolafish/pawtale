import httpx
import pytest
from fastapi import HTTPException

from app.services.ai_base import AITextServiceBase
from app.services.ai_calls import AIInvalidStructuredOutputError, AIOutputTruncatedError
from app.services.ai_timeouts import AIProviderTimeoutError
from app.services.memoir import book_manuscript_ai


class FailingAI(AITextServiceBase):
    provider_name = "test"
    model_name = "test-model"

    def __init__(self, error):
        self.error = error

    async def _call_llm(self, *args, **kwargs):
        raise self.error


@pytest.fixture(params=["generate_manuscript_editorial_copy", "generate_title_suggestions"])
def generate(request):
    async def call(client):
        kwargs = {"count": 3} if request.param == "generate_title_suggestions" else {}
        return await getattr(book_manuscript_ai, request.param)(
            title="童年", snapshot=[], ai_model=None, ai_client=client, **kwargs,
        )
    return call


@pytest.mark.parametrize("error", [TypeError("invalid call"), AttributeError("missing field"), ValueError("bug")])
async def test_programming_errors_do_not_become_optional_ai_success(generate, error):
    with pytest.raises(type(error), match=str(error)) as raised:
        await generate(FailingAI(error))
    assert raised.value is error


@pytest.mark.parametrize("error", [
    HTTPException(503, "provider unavailable"),
    AIProviderTimeoutError(provider="test", model="test-model", timeout_seconds=60),
    httpx.ReadTimeout("provider timeout"),
    httpx.ConnectError("connection refused"),
    AIInvalidStructuredOutputError("invalid output"),
    AIOutputTruncatedError("output truncated"),
])
async def test_expected_provider_failures_allow_existing_optional_fallback(generate, error):
    assert await generate(FailingAI(error)) is None


async def test_client_errors_are_not_hidden(generate):
    error = HTTPException(403, "model not allowed")
    with pytest.raises(HTTPException) as raised:
        await generate(FailingAI(error))
    assert raised.value is error


async def test_unconfigured_ai_remains_optional(generate, monkeypatch):
    monkeypatch.setattr(book_manuscript_ai, "_text_ai_configured", lambda model: False)
    assert await generate(None) is None
