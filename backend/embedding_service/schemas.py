from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


MAX_TEXT_LENGTH = 20_000


class EmbeddingRequest(BaseModel):
    model: str = Field(min_length=1)
    texts: list[str] = Field(min_length=1, max_length=128)

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return value.strip()

    @field_validator("texts")
    @classmethod
    def validate_texts(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("texts cannot contain empty values")
        if any(len(value) > MAX_TEXT_LENGTH for value in normalized):
            raise ValueError(f"each text must contain at most {MAX_TEXT_LENGTH} characters")
        return normalized


class EmbeddingResponse(BaseModel):
    model: str
    dimensions: int
    normalized: bool
    embeddings: list[list[float]]
    elapsed_ms: int


class EmbeddingHealthResponse(BaseModel):
    status: str
    ready: bool
    model: str
    dimensions: int
