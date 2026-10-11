from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.ai_credits import AICreditAccount, AICreditIdentity, AICreditOrder, AICreditTransaction
from app.models.ai_usage import AIUsageRecord
from app.services.ai_credit_orders import (
    CREDIT_PACKS, create_credit_order, deliver_credit_order, refund_credit_order, ios_refund_decision,
)
from app.services.ai_credits import (
    USAGE_DEBIT, USAGE_REFUND, _free_credit_claim_hashes, add_ai_credit, ensure_ai_credit_account,
    get_ai_credit_summary,
)
from app.services.wechat_auth import WeChatSession


async def login(client, code="owner", miniprogram="laoyu"):
    client.cookies.clear()
    response = await client.post("/api/auth/wechat-login", json={"code": code, "miniprogram": miniprogram})
    assert response.status_code == 200, response.text
    return response.json()["user"]["id"]


def wechat_identity(monkeypatch):
    async def exchange(code, _miniprogram):
        return WeChatSession(openid=code)
    monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", exchange)


@pytest.mark.ci_smoke
@pytest.mark.parametrize("spent", [Decimal("3.602339"), Decimal("40")])
async def test_all_credit_and_usage_survive_repeated_account_deletion(
    anonymous_client, test_db, monkeypatch, spent,
):
    wechat_identity(monkeypatch)
    user_id = await login(anonymous_client)
    await add_ai_credit(test_db, user_id=user_id, amount_rmb=Decimal("20"), kind="recharge", actor_user_id=user_id)
    await add_ai_credit(test_db, user_id=user_id, amount_rmb=Decimal("5"), kind="grant", actor_user_id=user_id)
    account = await ensure_ai_credit_account(test_db, user_id)
    # Mix an existing debit/refund ledger with legacy priced usage.
    test_db.add_all([
        AICreditTransaction(account_id=account.id, user_id=user_id, transaction_type=USAGE_DEBIT, amount_rmb=-spent),
        AICreditTransaction(account_id=account.id, user_id=user_id, transaction_type=USAGE_REFUND, amount_rmb=Decimal("0.25")),
        AIUsageRecord(operation_id=str(uuid4()), operation="voice", user_id=user_id,
                      provider="deepseek", model="test", status="succeeded", cost_status="estimated",
                      estimated_cost_rmb=Decimal("0.25")),
    ])
    await test_db.commit()
    before = await get_ai_credit_summary(test_db, user_id=user_id)
    assert before.remaining_rmb == Decimal("35") - spent
    for _ in range(2):
        deleted = await anonymous_client.request("DELETE", "/api/auth/me", json={"code": "owner"})
        assert deleted.status_code == 204, deleted.text
        await test_db.refresh(account)
        assert account.user_id is None
        new_user_id = await login(anonymous_client)
        assert new_user_id != user_id
        user_id = new_user_id
        for _ in range(2):
            assert await login(anonymous_client) == user_id
            assert (await get_ai_credit_summary(test_db, user_id=user_id)) == before
        assert (await ensure_ai_credit_account(test_db, user_id)).id == account.id
        details = (await anonymous_client.get("/api/billing/ai-usage")).json()
        assert sum(Decimal(day["cost_rmb"]) for day in details["days"]) == Decimal("0.25")
    other_user = await login(anonymous_client, "someone-else")
    assert (await get_ai_credit_summary(test_db, user_id=other_user)).remaining_rmb == Decimal("10")


@pytest.mark.ci_smoke
async def test_payment_delivery_and_refund_work_while_account_is_deleted(anonymous_client, test_db, monkeypatch):
    wechat_identity(monkeypatch)
    user_id = await login(anonymous_client)
    payment = await create_credit_order(test_db, user_id=user_id, pack=CREDIT_PACKS["29_9_0"], session_key="test")
    assert (await anonymous_client.request("DELETE", "/api/auth/me", json={"code": "owner"})).status_code == 204
    # Use fresh ORM values, just as the webhook's independent request does.
    test_db.expire_all()
    for expected in ("credited", "already_paid"):
        assert await deliver_credit_order(test_db, out_trade_no=payment.order_no,
                                         product_id="29_9_0", wx_order_id="wx-order") == expected
    assert (await ios_refund_decision(test_db, pay_order_id=payment.order_no))[0] == 0
    for expected in ("credit_taken_back", "already_refunded"):
        assert await refund_credit_order(test_db, out_trade_no=payment.order_no, wx_order_id="wx-order",
                                         refund_id="refund-1", refund_fen=990) == expected
    new_user_id = await login(anonymous_client)
    summary = await get_ai_credit_summary(test_db, user_id=new_user_id)
    assert summary.free_credit_rmb == Decimal("10")
    assert summary.recharged_credit_rmb == Decimal("20")
    assert summary.remaining_rmb == Decimal("30")
    order = await test_db.get(AICreditOrder, payment.order_no, populate_existing=True)
    assert order.user_id == new_user_id
    assert (await anonymous_client.get(f"/api/billing/credit-orders/{payment.order_no}")).status_code == 200


async def test_late_unionid_recovers_balance_without_losing_new_charges(anonymous_client, test_db, monkeypatch):
    async def exchange(code, miniprogram):
        return WeChatSession(openid=miniprogram, unionid=None if code == "no-union" else "shared-union")
    monkeypatch.setattr("app.routers.auth.exchange_wechat_login_code", exchange)
    original = await login(anonymous_client)
    await add_ai_credit(test_db, user_id=original, amount_rmb=Decimal("20"), kind="recharge", actor_user_id=original)
    assert (await anonymous_client.request("DELETE", "/api/auth/me", json={"code": "owner"})).status_code == 204
    current = await login(anonymous_client, "no-union", "xiaoyu")
    account = await ensure_ai_credit_account(test_db, current)
    test_db.add(AICreditTransaction(account_id=account.id, user_id=current,
                                   transaction_type=USAGE_DEBIT, amount_rmb=Decimal("-2")))
    await test_db.commit()
    for _ in range(2):
        assert await login(anonymous_client, "with-union", "xiaoyu") == current
        summary = await get_ai_credit_summary(test_db, user_id=current)
        assert summary.total_credit_rmb == Decimal("30")
        assert summary.used_rmb == Decimal("2")
        assert summary.remaining_rmb == Decimal("28")
    assert len((await test_db.scalars(select(AICreditAccount))).all()) == 1


async def test_legacy_deletion_marker_does_not_erase_a_repaired_credit_on_later_login(
    anonymous_client, test_db, monkeypatch,
):
    wechat_identity(monkeypatch)
    test_db.add_all([AICreditIdentity(key_hash=key) for key in _free_credit_claim_hashes("wechat_miniprogram", "owner", None)])
    await test_db.commit()
    user_id = await login(anonymous_client)
    account = await ensure_ai_credit_account(test_db, user_id)
    assert account.free_credit_rmb == 0
    account.free_credit_rmb = Decimal("6.397661")
    await test_db.commit()
    assert await login(anonymous_client) == user_id
    assert (await get_ai_credit_summary(test_db, user_id=user_id)).remaining_rmb == Decimal("6.397661")
