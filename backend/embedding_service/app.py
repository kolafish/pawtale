from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from embedding_service.config import EmbeddingServiceSettings
from embedding_service.runtime import EmbeddingRuntimeProtocol, SentenceTransformerRuntime
from embedding_service.schemas import (
    EmbeddingHealthResponse,
    EmbeddingRequest,
    EmbeddingResponse,
)


logger = logging.getLogger(__name__)


def create_app(
    *,
    runtime: EmbeddingRuntimeProtocol | None = None,
    settings: EmbeddingServiceSettings | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active_runtime = runtime
        if active_runtime is None:
            active_settings = settings or EmbeddingServiceSettings.from_environment()
            sentence_runtime = SentenceTransformerRuntime(active_settings)
            await run_in_threadpool(sentence_runtime.load)
            active_runtime = sentence_runtime
        app.state.embedding_runtime = active_runtime
        app.state.embedding_lock = asyncio.Lock()
        logger.info(
            "embedding service ready model=%s dimensions=%d",
            active_runtime.model_id,
            active_runtime.dimensions,
        )
        yield

    application = FastAPI(
        title="Pawtale Local Embedding Service",
        version="1.0.0",
        lifespan=lifespan,
    )

    def current_runtime(request: Request) -> EmbeddingRuntimeProtocol:
        active_runtime = request.app.state.embedding_runtime
        if not active_runtime.ready:
            raise HTTPException(status_code=503, detail="embedding model is not ready")
        return active_runtime

    @application.get("/health", response_model=EmbeddingHealthResponse)
    async def health(request: Request) -> EmbeddingHealthResponse:
        active_runtime = current_runtime(request)
        return EmbeddingHealthResponse(
            status="ok",
            ready=True,
            model=active_runtime.model_id,
            dimensions=active_runtime.dimensions,
        )

    @application.post("/v1/embeddings", response_model=EmbeddingResponse)
    async def embed(payload: EmbeddingRequest, request: Request) -> EmbeddingResponse:
        active_runtime = current_runtime(request)
        if payload.model != active_runtime.model_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"requested model {payload.model!r} is not loaded; "
                    f"resolved model is {active_runtime.model_id!r}"
                ),
            )
        async with request.app.state.embedding_lock:
            result = await run_in_threadpool(active_runtime.embed, payload.texts)
        logger.info(
            "embedding request model=%s texts=%d characters=%d elapsed_ms=%d",
            active_runtime.model_id,
            len(payload.texts),
            sum(len(text) for text in payload.texts),
            result.elapsed_ms,
        )
        return EmbeddingResponse(
            model=active_runtime.model_id,
            dimensions=active_runtime.dimensions,
            normalized=True,
            embeddings=result.embeddings,
            elapsed_ms=result.elapsed_ms,
        )

    return application


app = create_app()
