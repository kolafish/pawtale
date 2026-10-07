from __future__ import annotations

from app.services.ai_costs import estimate_audio_cost
from app.services.ai_usage_records import record_ai_usage


VOLC_STREAMING_ASR_PROVIDER = "volc_streaming_asr"
VOLC_STREAMING_ASR_MODEL = "volc.seedasr.sauc.duration"


async def record_volc_streaming_asr_usage(
    *,
    audio_duration_seconds: float,
    status: str,
    error_type: str | None = None,
    provider: str = VOLC_STREAMING_ASR_PROVIDER,
    billable: bool | None = None,
) -> None:
    if audio_duration_seconds <= 0:
        return
    await record_ai_usage(
        provider=provider,
        model=VOLC_STREAMING_ASR_MODEL,
        status=status,
        cost=estimate_audio_cost(
            provider=provider,
            model=VOLC_STREAMING_ASR_MODEL,
            duration_seconds=audio_duration_seconds,
        ),
        audio_duration_seconds=audio_duration_seconds,
        error_type=error_type,
        billable=billable,
    )
