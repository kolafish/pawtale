from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Protocol

from embedding_service.config import EmbeddingServiceSettings


@dataclass(frozen=True)
class EmbeddedBatch:
    embeddings: list[list[float]]
    elapsed_ms: int


class EmbeddingRuntimeProtocol(Protocol):
    model_id: str
    dimensions: int

    @property
    def ready(self) -> bool: ...

    def embed(self, texts: list[str]) -> EmbeddedBatch: ...


class SentenceTransformerRuntime:
    def __init__(self, settings: EmbeddingServiceSettings):
        self.settings = settings
        self.model_id = settings.model_id
        self.dimensions = settings.dimensions
        self._model = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if not self.settings.model_path.is_dir():
            raise RuntimeError(
                f"embedding model directory does not exist: {self.settings.model_path}"
            )

        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(self.settings.cpu_threads)
        torch.set_num_interop_threads(1)
        model = SentenceTransformer(
            str(self.settings.model_path),
            device="cpu",
            local_files_only=True,
        )
        model.max_seq_length = self.settings.max_sequence_length
        warmup = model.encode(
            ["本地向量服务启动检查"],
            batch_size=1,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        if tuple(warmup.shape) != (1, self.dimensions):
            raise RuntimeError(
                "embedding model dimensions mismatch: "
                f"expected {self.dimensions}, got {tuple(warmup.shape)}"
            )
        self._model = model

    def embed(self, texts: list[str]) -> EmbeddedBatch:
        if self._model is None:
            raise RuntimeError("embedding model is not loaded")
        started = time.perf_counter()
        encoded = self._model.encode(
            texts,
            batch_size=self.settings.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        embeddings = [[float(value) for value in row] for row in encoded.tolist()]
        for embedding in embeddings:
            if len(embedding) != self.dimensions:
                raise RuntimeError("embedding model returned an unexpected dimension")
            norm = math.sqrt(sum(value * value for value in embedding))
            if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
                raise RuntimeError("embedding model returned a non-normalized vector")
        return EmbeddedBatch(
            embeddings=embeddings,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )
