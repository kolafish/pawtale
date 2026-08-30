from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_MODEL_ID = "BAAI/bge-small-zh-v1.5"
DEFAULT_DIMENSIONS = 512
DEFAULT_MAX_SEQUENCE_LENGTH = 512
DEFAULT_BATCH_SIZE = 32
DEFAULT_CPU_THREADS = 4


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class EmbeddingServiceSettings:
    model_path: Path
    model_id: str = DEFAULT_MODEL_ID
    dimensions: int = DEFAULT_DIMENSIONS
    max_sequence_length: int = DEFAULT_MAX_SEQUENCE_LENGTH
    batch_size: int = DEFAULT_BATCH_SIZE
    cpu_threads: int = DEFAULT_CPU_THREADS

    @classmethod
    def from_environment(cls) -> EmbeddingServiceSettings:
        model_path_value = os.getenv("PAWTALE_EMBEDDING_MODEL_PATH", "").strip()
        if not model_path_value:
            raise RuntimeError("PAWTALE_EMBEDDING_MODEL_PATH is required")
        model_id = os.getenv("PAWTALE_EMBEDDING_MODEL_ID", DEFAULT_MODEL_ID).strip()
        if not model_id:
            raise RuntimeError("PAWTALE_EMBEDDING_MODEL_ID cannot be empty")
        return cls(
            model_path=Path(model_path_value).resolve(),
            model_id=model_id,
            dimensions=_positive_int("PAWTALE_EMBEDDING_DIMENSIONS", DEFAULT_DIMENSIONS),
            max_sequence_length=_positive_int(
                "PAWTALE_EMBEDDING_MAX_SEQUENCE_LENGTH",
                DEFAULT_MAX_SEQUENCE_LENGTH,
            ),
            batch_size=_positive_int("PAWTALE_EMBEDDING_BATCH_SIZE", DEFAULT_BATCH_SIZE),
            cpu_threads=_positive_int("PAWTALE_EMBEDDING_CPU_THREADS", DEFAULT_CPU_THREADS),
        )
