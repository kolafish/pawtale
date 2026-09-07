from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from app.services.memoir.tts_alignment import TTSAlignmentSegment


TTS_UNAVAILABLE_MESSAGE = "语音合成服务暂时不可用，请稍后重试。"
TTS_ALIGNMENT_METADATA_KEY = "tts_alignment"
TTS_TIMING_SOURCE_METADATA_KEY = "tts_timing_source"
TTS_SPEED_RATIO_METADATA_KEY = "tts_speed_ratio"
DEFAULT_TTS_SPEED_RATIO = 1.0
DEFAULT_STREAM_TTS_RESPONSE_FORMAT = "pcm"
DEFAULT_STREAM_TTS_SAMPLE_RATE = 16000
DEFAULT_STREAM_TTS_CHANNELS = 1
DEFAULT_STREAM_TTS_BITS_PER_SAMPLE = 16


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    mime_type: str
    extension: str
    provider: str
    model: str
    voice: str
    speed_ratio: float = DEFAULT_TTS_SPEED_RATIO
    alignment: list[TTSAlignmentSegment] = field(default_factory=list)
    duration_seconds: float | None = None


@dataclass(frozen=True)
class StreamingTTSChunk:
    audio: bytes
    mime_type: str
    provider: str
    model: str
    voice: str
    speed_ratio: float = DEFAULT_TTS_SPEED_RATIO
    sequence: int = 0
    alignment: list[TTSAlignmentSegment] = field(default_factory=list)
    duration_seconds: float | None = None
    sample_rate: int | None = None
    channels: int = DEFAULT_STREAM_TTS_CHANNELS
    bits_per_sample: int = DEFAULT_STREAM_TTS_BITS_PER_SAMPLE
    is_final: bool = False
    raw: dict[str, object] = field(default_factory=dict)


class TTSClient(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> str:
        ...

    @property
    def default_voice(self) -> str:
        ...

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        speed_ratio: float | None = None,
    ) -> TTSResult:
        ...

    def stream_synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        speed_ratio: float | None = None,
    ) -> AsyncIterator[StreamingTTSChunk]:
        raise NotImplementedError(f"{self.provider_name} does not support streaming TTS")

    def stream_input_synthesize(
        self,
        text_chunks: AsyncIterator[str],
        *,
        voice: str | None = None,
        speed_ratio: float | None = None,
        response_format: str | None = None,
        sample_rate: int | None = None,
    ) -> AsyncGenerator[StreamingTTSChunk, None]:
        raise NotImplementedError(f"{self.provider_name} does not support bidirectional streaming TTS")
