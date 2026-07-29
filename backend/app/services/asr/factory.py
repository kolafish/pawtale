from __future__ import annotations

from app.config import get_settings
from app.services.asr.streaming import StreamingASRClient, StreamingASREvent
from app.services.asr.volc_streaming import VolcStreamingASRClient, VolcStreamingASRConfig

REALTIME_TEXT_ACCELERATE_SCORE = 10


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


def create_realtime_volc_streaming_asr_client(user_id: str, voice_session_id: str) -> StreamingASRClient:
    return VolcStreamingASRClient(
        config=volc_config_from_settings(),
        user_id=user_id,
        voice_session_id=voice_session_id,
        request_options={
            "enable_accelerate_text": True,
            "accelerate_score": REALTIME_TEXT_ACCELERATE_SCORE,
        },
    )


def create_speaker_transcription_asr_client(user_id: str, transcription_session_id: str) -> StreamingASRClient:
    return VolcStreamingASRClient(
        config=volc_config_from_settings(),
        user_id=user_id,
        voice_session_id=transcription_session_id,
        request_options={"enable_speaker_info": True},
    )


__all__ = [
    "StreamingASRClient",
    "StreamingASREvent",
    "VolcStreamingASRClient",
    "VolcStreamingASRConfig",
    "create_realtime_volc_streaming_asr_client",
    "create_speaker_transcription_asr_client",
    "create_volc_streaming_asr_client",
    "volc_config_from_settings",
]
