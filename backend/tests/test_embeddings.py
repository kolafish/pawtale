from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.services.embeddings import (
    EmbeddingServiceError,
    LocalEmbeddingClient,
    create_embedding_client,
)


def _service() -> LocalEmbeddingClient:
    return LocalEmbeddingClient(
        base_url="http://embedding.test/",
        model="test-embedding-model",
        dimensions=2,
        timeout_seconds=3,
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://embedding.test",
    )


async def test_local_embedding_client_builds_request_and_validates_result():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "test-embedding-model",
                "dimensions": 2,
                "normalized": True,
                "embeddings": [[1.0, 0.0], [0.0, 1.0]],
                "elapsed_ms": 8,
            },
        )

    async with _client(handler) as client:
        result = await _service().embed(["  外婆家的院子  ", "第一次离开故乡"], client=client)

    assert captured == {
        "url": "http://embedding.test/v1/embeddings",
        "payload": {
            "model": "test-embedding-model",
            "texts": ["外婆家的院子", "第一次离开故乡"],
        },
    }
    assert result.requested_model == "test-embedding-model"
    assert result.resolved_model == "test-embedding-model"
    assert result.dimensions == 2
    assert result.normalized is True
    assert result.embeddings == ((1.0, 0.0), (0.0, 1.0))
    assert result.service_elapsed_ms == 8


@pytest.mark.parametrize(
    ("response_update", "message"),
    [
        ({"model": "wrong-model"}, "model mismatch"),
        ({"dimensions": 3}, "dimensions mismatch"),
        ({"normalized": False}, "non-normalized"),
        ({"embeddings": [[1.0, 0.0]]}, "count mismatch"),
        ({"embeddings": [[2.0, 0.0], [0.0, 1.0]]}, "non-normalized"),
    ],
)
async def test_local_embedding_client_rejects_invalid_contract(response_update, message):
    payload = {
        "model": "test-embedding-model",
        "dimensions": 2,
        "normalized": True,
        "embeddings": [[1.0, 0.0], [0.0, 1.0]],
        "elapsed_ms": 8,
    }
    payload.update(response_update)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _client(handler) as client:
        with pytest.raises(EmbeddingServiceError, match=message):
            await _service().embed(["第一段", "第二段"], client=client)


async def test_local_embedding_client_reports_http_status_without_response_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "sensitive upstream detail"})

    async with _client(handler) as client:
        with pytest.raises(EmbeddingServiceError, match="HTTP 503") as captured:
            await _service().embed(["一段回忆"], client=client)

    assert "sensitive" not in str(captured.value)


def test_create_embedding_client_uses_shared_backend_settings():
    settings = Settings(
        embedding_service_base_url="http://127.0.0.1:18102",
        embedding_model="configured-model",
        embedding_dimensions=384,
        embedding_timeout_seconds=2.5,
    )

    service = create_embedding_client(settings)

    assert service.base_url == "http://127.0.0.1:18102"
    assert service.model == "configured-model"
    assert service.dimensions == 384
    assert service.timeout_seconds == 2.5
