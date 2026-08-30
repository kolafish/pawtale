from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from embedding_service.app import create_app
from embedding_service.config import EmbeddingServiceSettings
from embedding_service.runtime import EmbeddedBatch


class FakeEmbeddingRuntime:
    model_id = "test-embedding-model"
    dimensions = 2
    ready = True

    def __init__(self) -> None:
        self.received_texts: list[str] = []

    def embed(self, texts: list[str]) -> EmbeddedBatch:
        self.received_texts = texts
        return EmbeddedBatch(
            embeddings=[[1.0, 0.0] for _ in texts],
            elapsed_ms=7,
        )


def test_embedding_service_reports_health_and_embeds_in_input_order():
    runtime = FakeEmbeddingRuntime()
    with TestClient(create_app(runtime=runtime)) as client:
        health = client.get("/health")
        response = client.post(
            "/v1/embeddings",
            json={
                "model": " test-embedding-model ",
                "texts": ["  外婆家的院子  ", "第一次离开故乡"],
            },
        )

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "ready": True,
        "model": "test-embedding-model",
        "dimensions": 2,
    }
    assert response.status_code == 200
    assert response.json() == {
        "model": "test-embedding-model",
        "dimensions": 2,
        "normalized": True,
        "embeddings": [[1.0, 0.0], [1.0, 0.0]],
        "elapsed_ms": 7,
    }
    assert runtime.received_texts == ["外婆家的院子", "第一次离开故乡"]


def test_embedding_service_rejects_a_model_that_is_not_loaded():
    with TestClient(create_app(runtime=FakeEmbeddingRuntime())) as client:
        response = client.post(
            "/v1/embeddings",
            json={"model": "other-model", "texts": ["一段回忆"]},
        )

    assert response.status_code == 409
    assert "other-model" in response.json()["detail"]


@pytest.mark.parametrize("texts", [[], ["  "]])
def test_embedding_service_rejects_empty_texts(texts: list[str]):
    with TestClient(create_app(runtime=FakeEmbeddingRuntime())) as client:
        response = client.post(
            "/v1/embeddings",
            json={"model": "test-embedding-model", "texts": texts},
        )

    assert response.status_code == 422


def test_embedding_service_settings_require_a_local_model_path(monkeypatch, tmp_path):
    monkeypatch.delenv("PAWTALE_EMBEDDING_MODEL_PATH", raising=False)
    with pytest.raises(RuntimeError, match="MODEL_PATH is required"):
        EmbeddingServiceSettings.from_environment()

    model_path = tmp_path / "model"
    monkeypatch.setenv("PAWTALE_EMBEDDING_MODEL_PATH", str(model_path))
    monkeypatch.setenv("PAWTALE_EMBEDDING_CPU_THREADS", "2")
    settings = EmbeddingServiceSettings.from_environment()

    assert settings.model_path == Path(model_path)
    assert settings.model_id == "BAAI/bge-small-zh-v1.5"
    assert settings.dimensions == 512
    assert settings.cpu_threads == 2
