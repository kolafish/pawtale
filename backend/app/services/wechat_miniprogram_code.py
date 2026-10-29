from __future__ import annotations

import asyncio
import time

import httpx

from app.config import get_settings

_API_BASE_URL = "https://api.weixin.qq.com"
# Refresh a little early so a token never expires between fetch and use.
_TOKEN_REFRESH_MARGIN_SECONDS = 300

_token_lock = asyncio.Lock()
_cached_token: tuple[str, str, float] | None = None  # appid, token, expires_at


class WeChatMiniProgramCodeError(Exception):
    pass


def miniprogram_code_configured() -> bool:
    settings = get_settings()
    return bool(settings.wechat_miniprogram_appid.strip() and settings.wechat_miniprogram_secret.strip())


async def _access_token(client: httpx.AsyncClient) -> str:
    global _cached_token
    settings = get_settings()
    appid = settings.wechat_miniprogram_appid.strip()
    async with _token_lock:
        if _cached_token and _cached_token[0] == appid and _cached_token[2] > time.monotonic():
            return _cached_token[1]
        # The stable token endpoint does not revoke tokens other services hold.
        response = await client.post(f"{_API_BASE_URL}/cgi-bin/stable_token", json={
            "grant_type": "client_credential",
            "appid": appid,
            "secret": settings.wechat_miniprogram_secret.strip(),
        })
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise WeChatMiniProgramCodeError(f"微信 access_token 获取失败 ({payload.get('errcode')})")
        lifetime = int(payload.get("expires_in") or 7200)
        _cached_token = (appid, token, time.monotonic() + max(60, lifetime - _TOKEN_REFRESH_MARGIN_SECONDS))
        return token


async def fetch_miniprogram_code(*, scene: str = "", page: str = "", path: str = "", width: int = 280) -> bytes:
    """Return a transparent code for a scene, or an existing route with a long token."""
    if path:
        if scene or page or len(path.encode("utf-8")) > 128:
            raise ValueError("A code path must be at most 128 bytes and cannot include scene/page")
    elif not scene or not page:
        raise ValueError("A code requires scene/page or path")
    if not miniprogram_code_configured():
        raise WeChatMiniProgramCodeError("微信小程序未配置")
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token = await _access_token(client)
            response = await client.post(
                f"{_API_BASE_URL}/wxa/{'getwxacode' if path else 'getwxacodeunlimit'}",
                params={"access_token": token},
                json={
                    **({"path": path} if path else {
                        "scene": scene, "page": page,
                        # The page may ship in a release after the code is printed.
                        "check_path": False,
                    }),
                    "env_version": settings.wechat_miniprogram_code_env_version,
                    "width": width,
                    "is_hyaline": True,
                },
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise WeChatMiniProgramCodeError("微信小程序码服务暂不可用") from exc
    if response.headers.get("content-type", "").startswith("image/"):
        return response.content
    try:
        payload = response.json()
    except ValueError as exc:
        raise WeChatMiniProgramCodeError("微信小程序码响应格式错误") from exc
    raise WeChatMiniProgramCodeError(f"{payload.get('errmsg') or '微信小程序码生成失败'} ({payload.get('errcode')})")
