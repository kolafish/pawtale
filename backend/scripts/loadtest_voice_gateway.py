"""Load test the memoir voice gateway with synthetic logged-in users."""

from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import websockets

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.auth import SESSION_COOKIE_NAME


DEFAULT_PASSWORD = "Pawtale-loadtest-123"
DEFAULT_READY_TIMEOUT_SECONDS = 10.0
DEFAULT_STOP_TIMEOUT_SECONDS = 10.0
DEFAULT_ASR_FRAME_BYTES = 3200
DEFAULT_ASR_FRAMES = 5
DEFAULT_ASR_FRAME_INTERVAL_SECONDS = 0.1


@dataclass(frozen=True)
class TestUserSpec:
    index: int
    email: str
    password: str
    display_name: str


@dataclass(frozen=True)
class RegisteredUser:
    index: int
    email: str
    user_id: str
    cookie_header: str


@dataclass(frozen=True)
class VoiceSession:
    id: str
    user: RegisteredUser


@dataclass
class GatewayResult:
    index: int
    email: str
    session_id: str
    status: str
    ready_ms: int | None = None
    stopped: bool = False
    error: str | None = None
    first_event: dict[str, Any] | None = None
    final_event: dict[str, Any] | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8101",
        help="HTTP(S) base URL for the Pawtale backend.",
    )
    parser.add_argument("--users", type=int, default=10, help="Number of concurrent synthetic users.")
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=120.0,
        help="Seconds to keep capacity-mode WebSockets open after all users are ready.",
    )
    parser.add_argument(
        "--mode",
        choices=["capacity", "asr"],
        default="capacity",
        help="capacity keeps sockets open without starting ASR; asr sends generated silent PCM frames.",
    )
    parser.add_argument(
        "--expect-eleventh-rejected",
        action="store_true",
        help="After the configured users are ready, open one extra gateway and require capacity rejection.",
    )
    parser.add_argument(
        "--email-prefix",
        default="",
        help="Email prefix. Defaults to a unique UTC timestamp prefixed with loadtest.",
    )
    parser.add_argument("--email-domain", default="pawtale.local", help="Email domain for synthetic users.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Password for synthetic users.")
    parser.add_argument(
        "--trust-env",
        action="store_true",
        help="Let httpx read proxy and SSL settings from environment variables.",
    )
    parser.add_argument("--ready-timeout", type=float, default=DEFAULT_READY_TIMEOUT_SECONDS)
    parser.add_argument("--stop-timeout", type=float, default=DEFAULT_STOP_TIMEOUT_SECONDS)
    parser.add_argument(
        "--asr-frames",
        type=int,
        default=DEFAULT_ASR_FRAMES,
        help="Generated silent PCM frame count for --mode asr.",
    )
    parser.add_argument(
        "--asr-frame-bytes",
        type=int,
        default=DEFAULT_ASR_FRAME_BYTES,
        help="Bytes per generated PCM frame for --mode asr.",
    )
    parser.add_argument(
        "--asr-frame-interval",
        type=float,
        default=DEFAULT_ASR_FRAME_INTERVAL_SECONDS,
        help="Seconds between generated PCM frames for --mode asr.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if args.users < 1:
        raise SystemExit("--users must be at least 1")
    if args.hold_seconds < 0:
        raise SystemExit("--hold-seconds cannot be negative")

    started_at = time.perf_counter()
    base_url = normalize_base_url(args.base_url)
    specs = build_user_specs(args.users + (1 if args.expect_eleventh_rejected else 0), args)
    async with httpx.AsyncClient(base_url=base_url, timeout=20.0, trust_env=args.trust_env) as client:
        sessions = await prepare_voice_sessions(client, specs)

    release = asyncio.Event()
    ready_queue: asyncio.Queue[GatewayResult] = asyncio.Queue()
    primary_sessions = sessions[: args.users]
    tasks = [
        asyncio.create_task(run_gateway_client(session, args, release, ready_queue))
        for session in primary_sessions
    ]
    ready_results = await collect_ready_results(ready_queue, args.users, args.ready_timeout)
    ready_count = sum(1 for result in ready_results if result.status == "ready")

    eleventh_result: GatewayResult | None = None
    if ready_count == args.users and args.expect_eleventh_rejected:
        eleventh_result = await expect_capacity_rejection(sessions[args.users], args)

    if args.mode == "capacity" and ready_count == args.users:
        await asyncio.sleep(args.hold_seconds)
    release.set()
    results = await asyncio.gather(*tasks)

    summary = {
        "status": "ok" if loadtest_passed(results, ready_results, eleventh_result, args) else "failed",
        "mode": args.mode,
        "base_url": base_url,
        "users": args.users,
        "ready_users": ready_count,
        "duration_seconds": round(time.perf_counter() - started_at, 3),
        "expect_eleventh_rejected": args.expect_eleventh_rejected,
        "eleventh_result": asdict(eleventh_result) if eleventh_result else None,
        "results": [asdict(result) for result in results],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "ok":
        raise SystemExit("voice gateway load test failed")


def normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ValueError("base URL cannot be empty")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be an absolute http(s) URL")
    return normalized


def gateway_ws_url(base_url: str, voice_session_id: str) -> str:
    parsed = urlparse(normalize_base_url(base_url))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    prefix = parsed.path.rstrip("/")
    path = f"{prefix}/api/mobile/memoir/voice-sessions/{voice_session_id}/gateway"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def build_user_specs(count: int, args: argparse.Namespace) -> list[TestUserSpec]:
    prefix = args.email_prefix.strip().lower()
    if not prefix:
        prefix = f"loadtest-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    domain = args.email_domain.strip().lower()
    password = args.password
    return [
        TestUserSpec(
            index=index,
            email=f"{prefix}-{index}@{domain}",
            password=password,
            display_name=f"压测用户 {index}",
        )
        for index in range(count)
    ]


async def prepare_voice_sessions(client: httpx.AsyncClient, specs: list[TestUserSpec]) -> list[VoiceSession]:
    sessions: list[VoiceSession] = []
    for spec in specs:
        user = await register_or_login(client, spec)
        response = await client.post(
            "/api/mobile/memoir/voice-sessions",
            json={"guidance_mode": "free_narration"},
        )
        response.raise_for_status()
        payload = response.json()
        voice_session_id = payload["session"]["id"]
        sessions.append(VoiceSession(id=voice_session_id, user=user))
        client.cookies.clear()
    return sessions


async def register_or_login(client: httpx.AsyncClient, spec: TestUserSpec) -> RegisteredUser:
    client.cookies.clear()
    response = await client.post(
        "/api/auth/register",
        json={
            "email": spec.email,
            "password": spec.password,
            "display_name": spec.display_name,
        },
    )
    if response.status_code == 409:
        client.cookies.clear()
        response = await client.post(
            "/api/auth/login",
            json={"email": spec.email, "password": spec.password},
        )
    response.raise_for_status()
    payload = response.json()
    return RegisteredUser(
        index=spec.index,
        email=spec.email,
        user_id=payload["user"]["id"],
        cookie_header=session_cookie_header(client.cookies),
    )


def session_cookie_header(cookies: httpx.Cookies) -> str:
    for cookie in cookies.jar:
        if cookie.name == SESSION_COOKIE_NAME:
            return f"{cookie.name}={cookie.value}"
    raise RuntimeError(f"{SESSION_COOKIE_NAME} cookie was not set")


async def collect_ready_results(
    ready_queue: asyncio.Queue[GatewayResult],
    count: int,
    timeout_seconds: float,
) -> list[GatewayResult]:
    results: list[GatewayResult] = []
    for _ in range(count):
        try:
            results.append(await asyncio.wait_for(ready_queue.get(), timeout=timeout_seconds))
        except asyncio.TimeoutError:
            break
    return results


async def run_gateway_client(
    session: VoiceSession,
    args: argparse.Namespace,
    release: asyncio.Event,
    ready_queue: asyncio.Queue[GatewayResult],
) -> GatewayResult:
    url = gateway_ws_url(args.base_url, session.id)
    result = GatewayResult(
        index=session.user.index,
        email=session.user.email,
        session_id=session.id,
        status="failed",
    )
    queued = False
    started_at = time.perf_counter()
    try:
        async with websockets.connect(url, additional_headers={"Cookie": session.user.cookie_header}) as websocket:
            first_event = await receive_json(websocket, args.ready_timeout)
            result.first_event = first_event
            if first_event.get("type") != "session.ready":
                result.error = first_event.get("error") or f"expected session.ready, got {first_event.get('type')}"
                await ready_queue.put(result)
                queued = True
                return result

            result.status = "ready"
            result.ready_ms = round((time.perf_counter() - started_at) * 1000)
            await ready_queue.put(
                GatewayResult(
                    index=result.index,
                    email=result.email,
                    session_id=result.session_id,
                    status="ready",
                    ready_ms=result.ready_ms,
                    first_event=result.first_event,
                )
            )
            queued = True

            if args.mode == "asr":
                result.final_event = await run_asr_probe(websocket, args)
            else:
                await release.wait()

            await websocket.send(json.dumps({"type": "session.stop"}))
            stopped = await wait_for_event(websocket, "session.stopped", args.stop_timeout)
            result.stopped = stopped is not None
            result.final_event = stopped or result.final_event
            if result.final_event and result.final_event.get("status") == "failed":
                result.status = "failed"
                result.error = result.final_event.get("error") or "gateway failed"
            else:
                result.status = "ok"
            return result
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        if not queued:
            await ready_queue.put(result)
        return result


async def run_asr_probe(websocket: websockets.ClientConnection, args: argparse.Namespace) -> dict[str, Any] | None:
    await websocket.send(json.dumps({"type": "session.start"}))
    started = await wait_for_event(websocket, "session.started", args.ready_timeout)
    if started is None:
        return {"type": "session.error", "error": "session_started_timeout"}

    frame = base64.b64encode(b"\0" * args.asr_frame_bytes).decode("ascii")
    for _ in range(args.asr_frames):
        await websocket.send(json.dumps({"type": "audio.frame", "audio": frame}))
        if args.asr_frame_interval > 0:
            await asyncio.sleep(args.asr_frame_interval)
    await websocket.send(json.dumps({"type": "audio.end"}))
    return await wait_for_any_event(
        websocket,
        {"audio.ended", "session.error"},
        max(args.stop_timeout, args.ready_timeout),
    )


async def expect_capacity_rejection(session: VoiceSession, args: argparse.Namespace) -> GatewayResult:
    url = gateway_ws_url(args.base_url, session.id)
    result = GatewayResult(
        index=session.user.index,
        email=session.user.email,
        session_id=session.id,
        status="failed",
    )
    try:
        async with websockets.connect(url, additional_headers={"Cookie": session.user.cookie_header}) as websocket:
            event = await receive_json(websocket, args.ready_timeout)
            result.first_event = event
            if event.get("type") == "session.error" and event.get("error") == "voice_gateway_capacity_reached":
                result.status = "rejected"
                result.error = event.get("error")
            else:
                result.status = "unexpected"
                result.error = event.get("error") or f"expected capacity rejection, got {event.get('type')}"
            return result
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        return result


async def receive_json(websocket: websockets.ClientConnection, timeout_seconds: float) -> dict[str, Any]:
    message = await asyncio.wait_for(websocket.recv(), timeout=timeout_seconds)
    payload = json.loads(message)
    if not isinstance(payload, dict):
        raise RuntimeError("gateway returned non-object JSON")
    return payload


async def wait_for_event(
    websocket: websockets.ClientConnection,
    event_type: str,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    return await wait_for_any_event(websocket, {event_type}, timeout_seconds)


async def wait_for_any_event(
    websocket: websockets.ClientConnection,
    event_types: set[str],
    timeout_seconds: float,
) -> dict[str, Any] | None:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        remaining = deadline - time.perf_counter()
        try:
            event = await receive_json(websocket, remaining)
        except asyncio.TimeoutError:
            return None
        if event.get("type") in event_types:
            return event
        if event.get("type") == "session.error":
            return event
    return None


def loadtest_passed(
    results: list[GatewayResult],
    ready_results: list[GatewayResult],
    eleventh_result: GatewayResult | None,
    args: argparse.Namespace,
) -> bool:
    if len(ready_results) != args.users:
        return False
    if any(result.status != "ready" for result in ready_results):
        return False
    if any(result.status != "ok" for result in results):
        return False
    if args.expect_eleventh_rejected and (eleventh_result is None or eleventh_result.status != "rejected"):
        return False
    return True


if __name__ == "__main__":
    asyncio.run(main())
