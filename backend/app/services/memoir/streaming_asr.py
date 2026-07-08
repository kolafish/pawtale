from __future__ import annotations

from app.config import get_settings
from app.services.memoir.asr import StreamingASRClient, StreamingASREvent
from app.services.memoir.volc_streaming_asr import VolcStreamingASRClient, VolcStreamingASRConfig


def volc_config_from_settings() -> VolcStreamingASRConfig:
    settings = get_settings()
    return VolcStreamingASRConfig(
        ws_url=settings.volc_asr_ws_url,
        resource_id=settings.volc_asr_resource_id,
        api_key=settings.volc_asr_api_key,
        app_key=settings.volc_asr_app_key,
        access_key=settings.volc_asr_access_key,
        connect_timeout_seconds=settings.volc_asr_connect_timeout_seconds,
    )


def create_volc_streaming_asr_client(user_id: str, voice_session_id: str) -> StreamingASRClient:
    return VolcStreamingASRClient(
        config=volc_config_from_settings(),
        user_id=user_id,
        voice_session_id=voice_session_id,
    )


def create_debug_speaker_streaming_asr_client(user_id: str, debug_session_id: str) -> StreamingASRClient:
    return VolcStreamingASRClient(
        config=volc_config_from_settings(),
        user_id=user_id,
        voice_session_id=debug_session_id,
        request_options={"enable_speaker_info": True},
    )


__all__ = [
    "StreamingASRClient",
    "StreamingASREvent",
    "VolcStreamingASRClient",
    "VolcStreamingASRConfig",
    "create_debug_speaker_streaming_asr_client",
    "create_volc_streaming_asr_client",
    "volc_config_from_settings",
]
