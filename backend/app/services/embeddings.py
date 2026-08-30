from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import time

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings, get_settings


logger = logging.getLogger(__name__)
MAX_BATCH_SIZE = 128
MAX_TEXT_LENGTH = 20_000


class EmbeddingServiceError(RuntimeError):
    """The local embedding service did not return a usable result."""


class _EmbeddingResponse(BaseModel):
    model: str = Field(min_length=1)
    dimensions: int = Field(gt=0)
    normalized: bool
    embeddings: list[list[float]]
    elapsed_ms: int = Field(ge=0)


@dataclass(frozen=True)
class EmbeddingBatchResult:
    requested_model: str
    resolved_model: str
    dimensions: int
    normalized: bool
    embeddings: tuple[tuple[float, ...], ...]
    service_elapsed_ms: int


class LocalEmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dimensions: int,
        timeout_seconds: float,
    ) -> None:
        if not base_url.strip():
            raise ValueError("embedding service base URL cannot be empty")
        if not model.strip():
            raise ValueError("embedding model cannot be empty")
        if dimensions <= 0:
            raise ValueError("embedding dimensions must be greater than zero")
        if timeout_seconds <= 0:
            raise ValueError("embedding timeout must be greater than zero")
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds

    async def embed(
        self,
        texts: list[str],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> EmbeddingBatchResult:
        normalized_texts = _validate_texts(texts)
        started = time.perf_counter()
        try:
            if client is not None:
                response = await self._request(client, normalized_texts)
            else:
                async with httpx.AsyncClient() as http_client:
                    response = await self._request(http_client, normalized_texts)
            result = self._parse_response(response, expected_count=len(normalized_texts))
        except EmbeddingServiceError as error:
            logger.warning(
                "embedding request failed model=%s texts=%d elapsed_ms=%d error_type=%s",
                self.model,
                len(normalized_texts),
                round((time.perf_counter() - started) * 1000),
                type(error.__cause__ or error).__name__,
            )
            raise

        logger.info(
            "embedding request completed requested_model=%s resolved_model=%s "
            "texts=%d dimensions=%d service_elapsed_ms=%d total_elapsed_ms=%d",
            result.requested_model,
            result.resolved_model,
            len(normalized_texts),
            result.dimensions,
            result.service_elapsed_ms,
            round((time.perf_counter() - started) * 1000),
        )
        return result

    async def _request(
        self,
        client: httpx.AsyncClient,
        texts: list[str],
    ) -> httpx.Response:
        try:
            response = await client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": self.model, "texts": texts},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            raise EmbeddingServiceError(
                f"embedding service request failed: HTTP {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise EmbeddingServiceError("embedding service request failed") from error

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        expected_count: int,
    ) -> EmbeddingBatchResult:
        try:
            parsed = _EmbeddingResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise EmbeddingServiceError("embedding service returned an invalid response") from error
        if parsed.model != self.model:
            raise EmbeddingServiceError(
                f"embedding model mismatch: expected {self.model}, got {parsed.model}"
            )
        if parsed.dimensions != self.dimensions:
            raise EmbeddingServiceError(
                "embedding dimensions mismatch: "
                f"expected {self.dimensions}, got {parsed.dimensions}"
            )
        if not parsed.normalized:
            raise EmbeddingServiceError("embedding service returned non-normalized vectors")
        if len(parsed.embeddings) != expected_count:
            raise EmbeddingServiceError(
                "embedding count mismatch: "
                f"expected {expected_count}, got {len(parsed.embeddings)}"
            )
        for embedding in parsed.embeddings:
            if len(embedding) != self.dimensions:
                raise EmbeddingServiceError("embedding vector has an unexpected dimension")
            norm = math.sqrt(sum(value * value for value in embedding))
            if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
                raise EmbeddingServiceError("embedding service returned a non-normalized vector")

        return EmbeddingBatchResult(
            requested_model=self.model,
            resolved_model=parsed.model,
            dimensions=parsed.dimensions,
            normalized=parsed.normalized,
            embeddings=tuple(tuple(vector) for vector in parsed.embeddings),
            service_elapsed_ms=parsed.elapsed_ms,
        )


def create_embedding_client(settings: Settings | None = None) -> LocalEmbeddingClient:
    current_settings = settings or get_settings()
    return LocalEmbeddingClient(
        base_url=current_settings.embedding_service_base_url,
        model=current_settings.embedding_model,
        dimensions=current_settings.embedding_dimensions,
        timeout_seconds=current_settings.embedding_timeout_seconds,
    )


def _validate_texts(texts: list[str]) -> list[str]:
    if not texts:
        raise ValueError("embedding texts cannot be empty")
    if len(texts) > MAX_BATCH_SIZE:
        raise ValueError(f"embedding batch cannot contain more than {MAX_BATCH_SIZE} texts")
    normalized = [text.strip() for text in texts]
    if any(not text for text in normalized):
        raise ValueError("embedding texts cannot contain empty values")
    if any(len(text) > MAX_TEXT_LENGTH for text in normalized):
        raise ValueError(f"each embedding text must contain at most {MAX_TEXT_LENGTH} characters")
    return normalized
