import json
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.clock import beijing_date, utcnow_naive
from app.config import get_settings
from app.models.ai_credits import AICreditOrder
from app.models.ai_usage import AIUsageRecord
from app.services.ai_credit_orders import pay_sig, user_signature
from app.services.ai_credits import get_ai_credit_summary
from app.services.wechat_auth import WeChatSession
from app.services.wechat_message_push import (
    decrypt_message,
    encrypt_message,
    encrypted_reply,
    push_signature,
)


TOKEN = "AAAAA"
AES_KEY = "A" * 43
APPID = "wxba5fad812f8e6fb9"
APP_KEY = "test-app-key"
SESSION_KEY = "9hAb/NEYUlkaMBEsmFgzig=="


def test_signatures_match_official_vectors():
    body = '{"openid": "xxx", "user_ip": "127.0.0.1", "env": 0}'
    assert pay_sig("/xpay/query_user_balance", body, "12345") == (
        "c37809f27c6d7fd1837ad2500a04512b66b34fd793a39a385fade56dca89a4b5"
    )
    assert user_signature(body, SESSION_KEY) == (
        "089d9e8dc5d308977360c4b79ec600a93d736802802a807d634192328032f6c7"
    )
    assert push_signature(TOKEN, "1714036504", "1514711492") == "f464b24fc39322e44b38aa78f5edd27bd1441696"
    reply = encrypted_reply(
        token=TOKEN, encoding_aes_key=AES_KEY, appid=APPID, message='{"demo_resp":"good luck"}',
        timestamp=1713424427, nonce="415670741", random_prefix=b"707722b803182950",
    )
    assert reply == {
        "Encrypt": "ELGduP2YcVatjqIS+eZbp80MNLoAUWvzzyJxgGzxZO/5sAvd070Bs6qrLARC9nVHm48Y4hyRbtzve1L32tmxSQ==",
        "MsgSignature": "1b9339964ed2e271e7c7b6ff2b0ef902fc94dea1",
        "TimeStamp": 1713424427,
        "Nonce": "415670741",
    }
    assert decrypt_message(AES_KEY, reply["Encrypt"], APPID) == '{"demo_resp":"good luck"}'


@pytest.fixture
def xpay_settings(monkeypatch):
    settings = get_settings()
    for name, value in {
        "wechat_xpay_offer_id": "1450000000",
        "wechat_xpay_app_key": APP_KEY,
        "wechat_message_token": TOKEN,
        "wechat_message_aes_key": AES_KEY,
        "wechat_miniprogram_appid": APPID,
    }.items():
        monkeypatch.setattr(settings, name, value)

    async def exchange(code, miniprogram="laoyu"):
        return WeChatSession(openid="payer-openid", session_key=SESSION_KEY)

    monkeypatch.setattr("app.routers.billing.exchange_wechat_login_code", exchange)


async def _push(client, event: dict):
    encrypted = encrypt_message(AES_KEY, json.dumps(event), APPID)
    timestamp, nonce = "1714112445", "415670741"
    return await client.post(
        "/api/wechat/message-push",
        params={
            "timestamp": timestamp,
            "nonce": nonce,
            "encrypt_type": "aes",
            "msg_signature": push_signature(TOKEN, timestamp, nonce, encrypted),
        },
        json={"ToUserName": "gh_test", "Encrypt": encrypted},
    )


def _deliver_event(order_no: str, product_id: str = "29_9_0") -> dict:
    return {
        "MsgType": "event",
        "Event": "xpay_goods_deliver_notify",
        "OpenId": "payer-openid",
        "OutTradeNo": order_no,
        "Env": 0,
        "WeChatPayInfo": {"MchOrderNo": "wx-order-1", "TransactionId": "tx-1", "PaidTime": 1714112445},
        "GoodsInfo": {"ProductId": product_id, "Quantity": 1, "OrigPrice": 2990, "ActualPrice": 2990,
                      "Attach": order_no},
    }


async def test_buying_a_pack_credits_once_and_refund_takes_it_back(client, test_db, xpay_settings):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    packs = (await client.get("/api/billing/credit-packs")).json()
    assert packs["enabled"] is True
    assert [pack["product_id"] for pack in packs["packs"]] == ["9_9_0", "29_9_0", "198_0"]

    response = await client.post("/api/billing/credit-orders", json={"product_id": "29_9_0", "code": "c"})
    assert response.status_code == 200
    payment = response.json()
    sign_data = json.loads(payment["sign_data"])
    assert sign_data == {
        "offerId": "1450000000", "buyQuantity": 1, "env": 0, "currencyType": "CNY",
        "productId": "29_9_0", "goodsPrice": 2990,
        "outTradeNo": payment["order_no"], "attach": payment["order_no"],
    }
    assert payment["mode"] == "short_series_goods"
    assert payment["pay_sig"] == pay_sig("requestVirtualPayment", payment["sign_data"], APP_KEY)
    assert payment["signature"] == user_signature(payment["sign_data"], SESSION_KEY)
    status_url = f"/api/billing/credit-orders/{payment['order_no']}"
    assert (await client.get(status_url)).json()["status"] == "pending"

    # WeChat retries pushes; only the first delivery adds credit.
    for _ in range(2):
        delivered = await _push(client, _deliver_event(payment["order_no"]))
        assert delivered.status_code == 200
        assert delivered.text == "success"
    assert (await client.get(status_url)).json()["status"] == "paid"
    summary = await get_ai_credit_summary(test_db, user_id=user_id)
    assert summary.recharged_credit_rmb == Decimal("29.9")
    assert summary.remaining_rmb == Decimal("34.9")

    refund = {
        "MsgType": "event", "Event": "xpay_refund_notify", "OpenId": "payer-openid",
        "WxRefundId": "wx-refund-1", "MchRefundId": "mch-refund-1",
        "WxOrderId": "wx-order-1", "MchOrderId": payment["order_no"],
        "RefundFee": 2990, "RetCode": 0, "RetMsg": "", "RetryTimes": 0,
    }
    for _ in range(2):
        assert (await _push(client, refund)).status_code == 200
    test_db.expire_all()
    assert (await client.get(status_url)).json()["status"] == "refunded"
    summary = await get_ai_credit_summary(test_db, user_id=user_id)
    assert summary.recharged_credit_rmb == Decimal("0")
    assert summary.remaining_rmb == Decimal("5")

    [day] = (await client.get("/api/billing/ai-usage")).json()["days"]
    assert sorted((entry["label"], Decimal(entry["amount_rmb"])) for entry in day["credits"]) == [
        ("充值", Decimal("29.9")), ("充值退款", Decimal("-29.9")),
    ]


async def test_delivery_for_unknown_order_or_wrong_pack_adds_nothing(client, test_db, xpay_settings):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    payment = (await client.post(
        "/api/billing/credit-orders", json={"product_id": "9_9_0", "code": "c"}
    )).json()

    assert (await _push(client, _deliver_event("0" * 32))).status_code == 200
    assert (await _push(client, _deliver_event(payment["order_no"], "198_0"))).status_code == 200

    order = await test_db.scalar(select(AICreditOrder).where(AICreditOrder.out_trade_no == payment["order_no"]))
    assert order.status == "pending"
    assert (await get_ai_credit_summary(test_db, user_id=user_id)).recharged_credit_rmb == Decimal("0")


async def test_ios_refund_query_refuses_once_the_credit_is_spent(client, test_db, xpay_settings):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    payment = (await client.post(
        "/api/billing/credit-orders", json={"product_id": "29_9_0", "code": "c"}
    )).json()
    await _push(client, _deliver_event(payment["order_no"]))
    query = {
        "MsgType": "event", "Event": "xpay_subscribe_ios_refund_query_notify",
        "refund_time": "1714112445", "order_time": "1714112400", "channel_bill": "apple-1",
        "bundleid": "com.tencent.xin", "product_id": "29_9_0", "p_count": "1",
        "refund_request_reason": "1", "provide_status": "1", "pay_order_id": payment["order_no"],
    }

    async def decision():
        reply = (await _push(client, query)).json()
        assert reply["MsgSignature"] == push_signature(TOKEN, str(reply["TimeStamp"]), reply["Nonce"],
                                                       reply["Encrypt"])
        body = json.loads(decrypt_message(AES_KEY, reply["Encrypt"], APPID))
        assert body["ErrCode"] == 0
        assert body["IosRefundQueryResponse"]["result_info"]
        return body["IosRefundQueryResponse"]["result_code"]

    assert await decision() == 0
    test_db.add(AIUsageRecord(
        operation_id=str(uuid4()), operation="memoir.voice", user_id=user_id, provider="deepseek",
        model="test-model", status="succeeded", estimated_cost_rmb=Decimal("15"), cost_status="estimated",
    ))
    await test_db.commit()
    assert await decision() == 1


async def test_message_push_rejects_bad_signatures(client, xpay_settings):
    verify = {"timestamp": "1714036504", "nonce": "1514711492", "echostr": "4375120948345356249"}
    ok = await client.get("/api/wechat/message-push",
                          params={**verify, "signature": "f464b24fc39322e44b38aa78f5edd27bd1441696"})
    assert ok.status_code == 200
    assert ok.text == "4375120948345356249"
    assert (await client.get("/api/wechat/message-push", params={**verify, "signature": "0" * 40})).status_code == 403

    encrypted = encrypt_message(AES_KEY, json.dumps(_deliver_event("x" * 32)), APPID)
    forged = await client.post(
        "/api/wechat/message-push",
        params={"timestamp": "1", "nonce": "2", "msg_signature": "0" * 40},
        json={"Encrypt": encrypted},
    )
    assert forged.status_code == 403


async def test_admin_lists_paid_orders_with_totals(admin_client, test_db, xpay_settings):
    paid = (await admin_client.post(
        "/api/billing/credit-orders", json={"product_id": "29_9_0", "code": "c"}
    )).json()
    await _push(admin_client, _deliver_event(paid["order_no"]))
    await admin_client.post("/api/billing/credit-orders", json={"product_id": "9_9_0", "code": "c"})
    refund = {
        "MsgType": "event", "Event": "xpay_refund_notify", "WxRefundId": "wx-refund-2",
        "WxOrderId": "wx-order-1", "MchOrderId": paid["order_no"], "RefundFee": 1000, "RetCode": 0,
    }
    await _push(admin_client, refund)
    test_db.expire_all()

    today = beijing_date(utcnow_naive()).isoformat()
    body = (await admin_client.get(
        "/api/billing/admin/credit-orders", params={"from": today, "to": today}
    )).json()
    assert body["pending_count"] == 1
    assert [order["order_no"] for order in body["orders"]] == [paid["order_no"]]
    order = body["orders"][0]
    assert (order["status"], order["pack_name"], order["wx_order_id"]) == ("paid", "AI 额度 29.9 元", "wx-order-1")
    assert (Decimal(order["price_rmb"]), Decimal(order["refunded_rmb"])) == (Decimal("29.9"), Decimal("10"))
    assert body["totals"]["paid_count"] == 1
    assert Decimal(body["totals"]["net_rmb"]) == Decimal("19.9")
    assert [item["product_id"] for item in body["products"]] == ["29_9_0"]

    with_pending = (await admin_client.get(
        "/api/billing/admin/credit-orders", params={"from": today, "to": today, "include_pending": "true"}
    )).json()
    assert [order["status"] for order in with_pending["orders"]] == ["pending", "paid"]


async def test_credit_order_list_is_admin_only(client):
    response = await client.get("/api/billing/admin/credit-orders", params={"from": "2026-10-01", "to": "2026-10-11"})
    assert response.status_code == 403
