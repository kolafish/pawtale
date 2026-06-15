from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol


ASREventType = Literal["partial", "final", "error"]


@dataclass(frozen=True)
class StreamingASREvent:
    type: ASREventType
    text: str = ""
    start_ms: int | None = None
    end_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class StreamingASRClient(Protocol):
    async def start(self) -> None: ...

    async def send_audio(self, audio: bytes) -> None: ...

    async def finish(self) -> None: ...

    async def events(self) -> AsyncIterator[StreamingASREvent]: ...

    async def close(self) -> None: ...
