from pydantic import BaseModel, ConfigDict, Field


class SpeakerProofreadContextLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_id: str | None = Field(default=None, max_length=100)
    text: str = Field(..., min_length=1, max_length=500)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)


class SpeakerProofreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=2000)
    context_lines: list[SpeakerProofreadContextLineRequest] = Field(default_factory=list, max_length=12)
    ai_model: str | None = Field(default=None, max_length=100)


class SpeakerProofreadResponse(BaseModel):
    text: str
    proofread_text: str
    changed: bool
    provider: str | None = None
    context_line_count: int
