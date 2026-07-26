from __future__ import annotations

from enum import StrEnum

from app.config import Settings, get_settings


class AITextTimeoutProfile(StrEnum):
    REALTIME = "realtime"
    INTERACTIVE = "interactive"
    GENERATION = "generation"


def ai_text_timeout_seconds(
    profile: AITextTimeoutProfile,
    *,
    settings: Settings | None = None,
) -> float:
    current = settings or get_settings()
    values = {
        AITextTimeoutProfile.REALTIME: current.ai_text_realtime_timeout_seconds,
        AITextTimeoutProfile.INTERACTIVE: current.ai_text_interactive_timeout_seconds,
        AITextTimeoutProfile.GENERATION: current.ai_text_generation_timeout_seconds,
    }
    return max(1.0, float(values[profile]))


class AIProviderTimeoutError(TimeoutError):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self.provider = provider
        self.model = model
        self.timeout_seconds = timeout_seconds
        seconds = (
            int(timeout_seconds)
            if float(timeout_seconds).is_integer()
            else timeout_seconds
        )
        super().__init__(f"模型处理超过 {seconds} 秒，请稍后重试")


def ai_error_metadata(
    error: Exception,
    *,
    requested_model: str | None,
    provider: str | None,
    resolved_model: str | None,
    timeout_seconds: float,
    elapsed_ms: int,
) -> dict[str, object]:
    return {
        "requested_model": requested_model,
        "provider": provider,
        "model": resolved_model,
        "timeout_seconds": timeout_seconds,
        "elapsed_ms": elapsed_ms,
        "error_type": error.__class__.__name__,
    }


def ai_user_error_message(error: Exception, fallback: str) -> str:
    message = str(error).strip()
    if any("\u4e00" <= character <= "\u9fff" for character in message):
        return message
    return fallback
