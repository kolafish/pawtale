"""Smoke check the realtime memoir ASR deployment without printing secrets."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.migrations.memoir_voice import assert_memoir_voice_schema
from app.services.asr.volc_protocol import VolcASRProtocolCodec
from app.services.asr.volc_streaming import VolcStreamingASRClient, VolcStreamingASRConfig


DEFAULT_PROVIDER_TIMEOUT_SECONDS = 20.0
PCM_CHUNK_BYTES = 3200


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pcm-file",
        type=Path,
        help="Optional 16kHz mono pcm_s16le file used for a real Volc ASR smoke call.",
    )
    parser.add_argument(
        "--provider-timeout",
        type=float,
        default=DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        help="Seconds to wait for a final or error provider event when --pcm-file is set.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    config = VolcStreamingASRConfig(
        ws_url=settings.volc_asr_ws_url,
        resource_id=settings.volc_asr_resource_id,
        api_key=settings.volc_asr_api_key,
        app_key=settings.volc_asr_app_key,
        access_key=settings.volc_asr_access_key,
        connect_timeout_seconds=settings.volc_asr_connect_timeout_seconds,
    )
    result: dict[str, Any] = {
        "env": _env_status(config),
        "schema": await _schema_status(settings.database_url),
        "codec": _codec_status(),
        "provider": {"status": "skipped"},
    }
    if args.pcm_file:
        result["provider"] = await _provider_status(config, args.pcm_file, args.provider_timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    failed = [
        name
        for name, value in result.items()
        if isinstance(value, dict) and value.get("status") == "failed"
    ]
    if failed:
        raise SystemExit(f"Realtime ASR smoke failed: {', '.join(failed)}")


def _env_status(config: VolcStreamingASRConfig) -> dict[str, Any]:
    credential_mode = "missing"
    if config.app_key and config.access_key:
        credential_mode = "app_access"
    elif config.api_key:
        credential_mode = "api_key"
    return {
        "status": "ok" if credential_mode != "missing" else "failed",
        "ws_url_configured": bool(config.ws_url),
        "resource_id_configured": bool(config.resource_id),
        "credential_mode": credential_mode,
    }


async def _schema_status(database_url: str) -> dict[str, str]:
    engine = create_async_engine(database_url, echo=False)
    try:
        await assert_memoir_voice_schema(engine)
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
    finally:
        await engine.dispose()


def _codec_status() -> dict[str, Any]:
    codec = VolcASRProtocolCodec()
    full_request = codec.build_full_request({"request": {"model_name": "bigmodel"}}, sequence=1)
    audio_request = codec.build_audio_request(b"abc", sequence=2)
    final_request = codec.build_audio_request(b"", is_last=True, sequence=3)
    return {
        "status": "ok",
        "full_request_sequence": int.from_bytes(full_request[4:8], "big", signed=True),
        "audio_request_sequence": int.from_bytes(audio_request[4:8], "big", signed=True),
        "final_request_sequence": int.from_bytes(final_request[4:8], "big", signed=True),
    }


async def _provider_status(
    config: VolcStreamingASRConfig,
    pcm_file: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    if not pcm_file.exists():
        return {"status": "failed", "error": f"pcm file not found: {pcm_file}"}
    if pcm_file.stat().st_size == 0:
        return {"status": "failed", "error": "pcm file is empty"}

    client = VolcStreamingASRClient(
        config=config,
        user_id="smoke-test",
        voice_session_id="smoke-test",
    )
    try:
        await client.start()
        collector = asyncio.create_task(_collect_provider_terminal_event(client))
        with pcm_file.open("rb") as handle:
            while chunk := handle.read(PCM_CHUNK_BYTES):
                await client.send_audio(chunk)
        await client.finish()
        event = await asyncio.wait_for(collector, timeout=timeout_seconds)
        if event["type"] == "error":
            return {"status": "failed", "error": event["error"]}
        return {
            "status": "ok",
            "final_text_chars": len(event["text"]),
        }
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
    finally:
        await client.close()


async def _collect_provider_terminal_event(client: VolcStreamingASRClient) -> dict[str, str]:
    async for event in client.events():
        if event.type == "error":
            return {"type": "error", "error": event.error or "asr_provider_error"}
        if event.type == "final" and event.text:
            return {"type": "final", "text": event.text}
    return {"type": "error", "error": "provider stream ended without final text"}


if __name__ == "__main__":
    asyncio.run(_main())
