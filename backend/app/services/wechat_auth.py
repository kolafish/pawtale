from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import get_settings


class WeChatAuthError(Exception):
    pass


@dataclass(frozen=True)
class WeChatSession:
    openid: str
    unionid: str | None = None


async def exchange_wechat_login_code(code: str) -> WeChatSession:
    settings = get_settings()
    appid = settings.wechat_miniprogram_appid.strip()
    secret = settings.wechat_miniprogram_secret.strip()
    if not appid or not secret:
        raise WeChatAuthError("微信小程序登录未配置")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                settings.wechat_jscode2session_url,
                params={
                    "appid": appid,
                    "secret": secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise WeChatAuthError("微信登录服务暂不可用") from exc
    except ValueError as exc:
        raise WeChatAuthError("微信登录响应格式错误") from exc
    openid = payload.get("openid")
    if not isinstance(openid, str) or not openid:
        error_code = payload.get("errcode")
        error_message = payload.get("errmsg") or "微信登录失败"
        raise WeChatAuthError(f"{error_message} ({error_code})")

    unionid = payload.get("unionid")
    return WeChatSession(
        openid=openid,
        unionid=unionid if isinstance(unionid, str) and unionid else None,
    )
