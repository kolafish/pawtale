"""AI credit packs sold through mini program virtual payment (道具直购).

The mini program pays with wx.requestVirtualPayment using the signatures made
here; WeChat then pushes delivery, refund and iOS refund-query events to the
message push endpoint, which credits or refunds the user's recharge balance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow_naive
from app.config import get_settings
from app.models.ai_credits import AICreditOrder, AICreditTransaction
from app.services.ai_credits import RECHARGE_REFUND, add_ai_credit, get_ai_credit_summary


logger = logging.getLogger(__name__)

XPAY_MODE = "short_series_goods"
# Production; Apple payments have no sandbox.
XPAY_ENV = 0


@dataclass(frozen=True)
class CreditPack:
    """One 道具 configured in the mini program admin: same product id and price."""

    product_id: str
    name: str
    price_fen: int

    @property
    def credit_rmb(self) -> Decimal:
        # Credit equals the price paid on every platform.
        return Decimal(self.price_fen) / 100


CREDIT_PACKS = {
    pack.product_id: pack
    for pack in (
        CreditPack("ai_credit_10", "AI 额度 10 元", 1000),
        CreditPack("ai_credit_30", "AI 额度 30 元", 3000),
        CreditPack("ai_credit_100", "AI 额度 100 元", 10000),
    )
}


@dataclass(frozen=True)
class CreditOrderPayment:
    order_no: str
    mode: str
    sign_data: str
    pay_sig: str
    signature: str


def xpay_configured() -> bool:
    settings = get_settings()
    return bool(settings.wechat_xpay_offer_id.strip() and settings.wechat_xpay_app_key.strip())


def _hmac_sha256(key: str, message: str) -> str:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def pay_sig(uri: str, body: str, app_key: str) -> str:
    return _hmac_sha256(app_key, f"{uri}&{body}")


def user_signature(body: str, session_key: str) -> str:
    return _hmac_sha256(session_key, body)


async def create_credit_order(
    db: AsyncSession,
    *,
    user_id: str,
    pack: CreditPack,
    session_key: str,
) -> CreditOrderPayment:
    settings = get_settings()
    order = AICreditOrder(
        out_trade_no=uuid.uuid4().hex,
        user_id=user_id,
        product_id=pack.product_id,
        price_fen=pack.price_fen,
        credit_rmb=pack.credit_rmb,
        status="pending",
    )
    db.add(order)
    await db.commit()
    sign_data = json.dumps(
        {
            "offerId": settings.wechat_xpay_offer_id.strip(),
            "buyQuantity": 1,
            "env": XPAY_ENV,
            "currencyType": "CNY",
            "productId": pack.product_id,
            "goodsPrice": pack.price_fen,
            "outTradeNo": order.out_trade_no,
            "attach": order.out_trade_no,
        },
        separators=(",", ":"),
    )
    return CreditOrderPayment(
        order_no=order.out_trade_no,
        mode=XPAY_MODE,
        sign_data=sign_data,
        pay_sig=pay_sig("requestVirtualPayment", sign_data, settings.wechat_xpay_app_key.strip()),
        signature=user_signature(sign_data, session_key),
    )


async def _lock_order(db: AsyncSession, *conditions) -> AICreditOrder | None:
    return await db.scalar(
        select(AICreditOrder).where(or_(*conditions)).with_for_update().limit(1)
    )


async def deliver_credit_order(
    db: AsyncSession,
    *,
    out_trade_no: str,
    product_id: str,
    wx_order_id: str | None,
) -> None:
    """Credit a paid order once; repeated delivery pushes are no-ops."""
    order = await _lock_order(db, AICreditOrder.out_trade_no == out_trade_no)
    if order is None or order.product_id != product_id:
        logger.error("WeChat delivery for unknown order %s (%s)", out_trade_no, product_id)
        await db.rollback()
        return
    if order.status != "pending":
        await db.rollback()
        return
    order.status = "paid"
    order.wx_order_id = wx_order_id
    order.paid_at = utcnow_naive()
    pack = CREDIT_PACKS.get(order.product_id)
    # add_ai_credit commits the order update together with the credit.
    await add_ai_credit(
        db,
        user_id=order.user_id,
        amount_rmb=order.credit_rmb,
        kind="recharge",
        actor_user_id=order.user_id,
        note=pack.name if pack else order.product_id,
        operation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"wechat-xpay-order:{order.out_trade_no}")),
    )


async def refund_credit_order(
    db: AsyncSession,
    *,
    out_trade_no: str,
    wx_order_id: str,
    refund_id: str,
    refund_fen: int,
) -> None:
    """Take back the refunded share of an order's credit once per WeChat refund."""
    order = await _lock_order(
        db,
        AICreditOrder.out_trade_no == out_trade_no,
        AICreditOrder.wx_order_id == wx_order_id,
    )
    operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"wechat-xpay-refund:{refund_id}"))
    already_refunded = order is not None and await db.scalar(
        select(AICreditTransaction.id).where(
            AICreditTransaction.operation_id == operation_id,
            AICreditTransaction.transaction_type == RECHARGE_REFUND,
        )
    )
    if order is None or order.status == "pending" or already_refunded or refund_fen <= 0:
        if order is None:
            logger.error("WeChat refund %s for unknown order %s", refund_id, out_trade_no)
        await db.rollback()
        return
    order.refunded_fen += refund_fen
    if order.refunded_fen >= order.price_fen:
        order.status = "refunded"
    try:
        # A user who already spent the credit can go below zero; the balance shows it.
        await add_ai_credit(
            db,
            user_id=order.user_id,
            amount_rmb=-(order.credit_rmb * refund_fen / order.price_fen),
            kind="recharge_refund",
            actor_user_id=order.user_id,
            note=f"退款 {refund_fen / 100:.2f} 元",
            operation_id=operation_id,
        )
    except ValueError:
        # Only an earlier manual correction of recharged credit can cause this.
        logger.exception("WeChat refund %s exceeds recharged credit of user %s", refund_id, order.user_id)
        await db.rollback()


async def ios_refund_decision(db: AsyncSession, *, pay_order_id: str) -> tuple[int, str]:
    """Answer Apple's refund query: 0 suggests refunding, 1 refuses because the credit was used."""
    order = await db.scalar(
        select(AICreditOrder).where(
            or_(AICreditOrder.out_trade_no == pay_order_id, AICreditOrder.wx_order_id == pay_order_id)
        )
    )
    if order is None:
        return 0, "未找到对应订单"
    remaining = (await get_ai_credit_summary(db, user_id=order.user_id)).remaining_rmb
    if remaining >= order.credit_rmb:
        return 0, f"本单额度 {order.credit_rmb:.2f} 元尚未使用，当前余额 {remaining:.2f} 元"
    return 1, f"本单额度 {order.credit_rmb:.2f} 元已被使用，当前余额 {remaining:.2f} 元"


async def handle_xpay_event(db: AsyncSession, event: dict) -> dict | None:
    """Process one decrypted push; return a reply body, or None for a plain success."""
    name = event.get("Event")
    if name == "xpay_goods_deliver_notify":
        if event.get("Env", XPAY_ENV) != XPAY_ENV:
            return None
        goods = event.get("GoodsInfo") or {}
        await deliver_credit_order(
            db,
            out_trade_no=str(event.get("OutTradeNo") or ""),
            product_id=str(goods.get("ProductId") or ""),
            wx_order_id=(event.get("WeChatPayInfo") or {}).get("MchOrderNo"),
        )
    elif name == "xpay_refund_notify":
        if event.get("RetCode") != 0:
            return None
        await refund_credit_order(
            db,
            out_trade_no=str(event.get("MchOrderId") or ""),
            wx_order_id=str(event.get("WxOrderId") or ""),
            refund_id=str(event.get("WxRefundId") or event.get("MchRefundId") or ""),
            refund_fen=int(event.get("RefundFee") or 0),
        )
    elif name == "xpay_subscribe_ios_refund_query_notify":
        result_code, evidence = await ios_refund_decision(db, pay_order_id=str(event.get("pay_order_id") or ""))
        return {
            "ErrCode": 0,
            "ErrMsg": "success",
            "IosRefundQueryResponse": {"result_code": result_code, "evidence": evidence},
        }
    return None
