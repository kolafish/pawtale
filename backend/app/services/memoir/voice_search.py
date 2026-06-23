from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from app.services.memoir.asr import StreamingASRClient, StreamingASREvent


MAX_VOICE_SEARCH_AUDIO_BYTES = 2 * 1024 * 1024
ASR_SEARCH_DRAIN_TIMEOUT_SECONDS = 10.0

StreamingASRClientFactory = Callable[[], StreamingASRClient]


class VoiceSearchTranscriptionError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


async def transcribe_voice_search_audio(
    audio: bytes,
    *,
    asr_client_factory: StreamingASRClientFactory,
) -> str:
    if not audio:
        raise VoiceSearchTranscriptionError("voice_search_audio_empty", "请先录一段搜索语音", 400)
    if len(audio) > MAX_VOICE_SEARCH_AUDIO_BYTES:
        raise VoiceSearchTranscriptionError("voice_search_audio_too_large", "搜索语音太长，请控制在 10 秒内", 413)

    asr_client = asr_client_factory()
    final_texts: list[str] = []
    latest_text = ""
    collect_task: asyncio.Task[None] | None = None

    async def collect_events() -> None:
        nonlocal latest_text
        async for event in asr_client.events():
            text = _event_text(event)
            if text:
                latest_text = text
            if event.type == "final" and text:
                final_texts.append(text)
            if event.type == "error":
                raise VoiceSearchTranscriptionError(
                    event.error or "asr_provider_error",
                    "语音识别服务暂时不可用",
                    502,
                )

    try:
        await asr_client.start()
        collect_task = asyncio.create_task(collect_events())
        await asr_client.send_audio(audio)
        await asr_client.finish()
        await asyncio.wait_for(asyncio.shield(collect_task), timeout=ASR_SEARCH_DRAIN_TIMEOUT_SECONDS)
    except VoiceSearchTranscriptionError:
        raise
    except asyncio.TimeoutError as exc:
        raise VoiceSearchTranscriptionError("asr_finish_timeout", "语音识别超时，请重试", 504) from exc
    except Exception as exc:
        raise VoiceSearchTranscriptionError(str(exc) or "asr_provider_error", "语音识别服务暂时不可用", 502) from exc
    finally:
        if collect_task is not None and not collect_task.done():
            collect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collect_task
        with contextlib.suppress(Exception):
            await asr_client.close()

    transcript = (final_texts[-1] if final_texts else latest_text).strip()
    if not transcript:
        raise VoiceSearchTranscriptionError("asr_empty_transcript", "没有识别到搜索内容，请换个说法再试", 422)
    return transcript


def _event_text(event: StreamingASREvent) -> str:
    return event.text.strip()
