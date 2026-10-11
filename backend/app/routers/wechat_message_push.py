import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.services.ai_credit_orders import handle_xpay_event
from app.services.wechat_message_push import (
    WeChatMessageError,
    decrypt_message,
    encrypted_reply,
    signature_matches,
)


router = APIRouter(prefix="/api/wechat", tags=["wechat"])


def _push_settings() -> tuple[str, str, str]:
    settings = get_settings()
    token = settings.wechat_message_token.strip()
    if not token:
        raise HTTPException(status_code=404, detail="消息推送未配置")
    return token, settings.wechat_message_aes_key.strip(), settings.wechat_miniprogram_appid.strip()


@router.get("/message-push", response_class=PlainTextResponse)
async def verify_message_push(
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
) -> str:
    """URL check WeChat runs when the push URL is saved in the mini program admin."""
    token, _aes_key, _appid = _push_settings()
    if not signature_matches(signature, token, timestamp, nonce):
        raise HTTPException(status_code=403, detail="签名错误")
    return echostr


@router.post("/message-push")
async def receive_message_push(
    request: Request,
    timestamp: str = Query(...),
    nonce: str = Query(...),
    signature: str = Query(""),
    msg_signature: str = Query(""),
    db: AsyncSession = Depends(get_db),
):
    token, aes_key, appid = _push_settings()
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="消息格式错误") from exc
    encrypted = body.get("Encrypt") if isinstance(body, dict) else None
    if encrypted:
        if not signature_matches(msg_signature, token, timestamp, nonce, encrypted):
            raise HTTPException(status_code=403, detail="签名错误")
        try:
            event = json.loads(decrypt_message(aes_key, encrypted, appid))
        except (WeChatMessageError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="消息解密失败") from exc
    else:
        if not signature_matches(signature, token, timestamp, nonce):
            raise HTTPException(status_code=403, detail="签名错误")
        event = body

    reply = await handle_xpay_event(db, event)
    if reply is None:
        # Plain "success" needs no encryption and counts as ErrCode 0.
        return PlainTextResponse("success")
    if not encrypted:
        return JSONResponse(reply)
    return JSONResponse(encrypted_reply(
        token=token,
        encoding_aes_key=aes_key,
        appid=appid,
        message=json.dumps(reply, ensure_ascii=False),
        timestamp=int(time.time()),
        nonce=nonce,
    ))
