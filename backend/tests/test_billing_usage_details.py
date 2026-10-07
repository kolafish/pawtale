from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.clock import utcnow_naive
from app.main import app
from app.models.ai_credits import AICreditTransaction
from app.models.ai_usage import AIUsageRecord
from app.models.users import User
from app.services.ai_costs import estimate_audio_cost
from app.services.ai_credits import (
    USAGE_DEBIT, USAGE_REFUND, ensure_ai_credit_account, get_ai_credit_summary,
)
from app.services.ai_usage_records import AIUsageContext, record_ai_usage


async def test_usage_details_only_show_current_users_successful_charges(client, test_db):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    other_user = User(email="other@example.com", password_hash="unused", display_name="其他用户")
    test_db.add(other_user)
    await test_db.flush()
    now = utcnow_naive()

    def usage(*, owner_id, operation, provider, status, cost, created_at):
        return AIUsageRecord(
            operation_id=str(uuid4()),
            operation=operation,
            user_id=owner_id,
            provider=provider,
            model="test-model",
            status=status,
            estimated_cost_rmb=cost,
            cost_status="estimated",
            created_at=created_at,
        )

    test_db.add_all([
        usage(owner_id=user_id, operation="/api/mobile/memoir/voice-sessions/1/gateway",
              provider="volc_tts", status="succeeded", cost=Decimal("0.003"), created_at=now),
        usage(owner_id=user_id, operation="/api/mobile/memoir/voice-sessions/1/gateway",
              provider="deepseek", status="succeeded", cost=Decimal("0.02"),
              created_at=now - timedelta(minutes=1)),
        usage(owner_id=user_id, operation="/api/mobile/memoir/voice-sessions/1/gateway",
              provider="deepseek", status="failed", cost=Decimal("1"), created_at=now),
        usage(owner_id=other_user.id, operation="/api/mobile/memoir/voice-sessions/2/gateway",
              provider="volc_tts", status="succeeded", cost=Decimal("2"), created_at=now),
    ])
    await test_db.commit()

    page = (await client.get("/api/billing/ai-usage")).json()
    assert page["has_more"] is False
    [today] = page["days"]
    assert Decimal(today["cost_rmb"]) == Decimal("0.023")
    assert [(item["purpose"], item["count"], Decimal(item["cost_rmb"])) for item in today["purposes"]] == [
        ("语音访谈", 1, Decimal("0.02")),
        ("语音朗读", 1, Decimal("0.003")),
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        assert (await anonymous.get("/api/billing/ai-usage")).status_code == 401


async def test_usage_refund_and_recharge_correct_balance_and_billing_details(client, test_db):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    account = await ensure_ai_credit_account(test_db, user_id)
    account.recharged_credit_rmb += Decimal("10")
    charges = [AIUsageRecord(
        operation_id=str(uuid4()), operation="book-output/tts", user_id=user_id,
        provider="volc_tts", model="seed-tts-2.0", status="succeeded",
        input_characters=500, estimated_cost_rmb=cost, cost_status="estimated",
    ) for cost in (Decimal("0.15"), Decimal("0"))]
    legacy = AIUsageRecord(
        operation_id=str(uuid4()), operation="voice", user_id=user_id,
        provider="deepseek", model="test-model", status="succeeded",
        estimated_cost_rmb=Decimal("0.02"), cost_status="estimated",
    )
    test_db.add_all([*charges, legacy])
    await test_db.flush()
    debits = [AICreditTransaction(
        user_id=user_id, transaction_type=USAGE_DEBIT, amount_rmb=Decimal("-0.25"),
        usage_record_id=record.id, operation_id=record.operation_id,
    ) for record in charges]
    test_db.add_all([
        *debits,
        AICreditTransaction(user_id=user_id, transaction_type=USAGE_REFUND,
                            amount_rmb=Decimal("0.35"), note="Correct rate and duplicate generation"),
        AICreditTransaction(user_id=user_id, transaction_type="recharge", amount_rmb=Decimal("10")),
    ])
    await test_db.commit()

    summary = await get_ai_credit_summary(test_db, user_id=user_id)
    assert summary.recharged_credit_rmb == Decimal("10")
    assert summary.total_credit_rmb == Decimal("20")
    assert summary.used_rmb == Decimal("0.17")
    assert summary.remaining_rmb == Decimal("19.83")
    [day] = (await client.get("/api/billing/ai-usage")).json()["days"]
    assert {Decimal(item["cost_rmb"]) for item in day["purposes"]} == {Decimal("0.15"), Decimal("0.02")}
    assert [(entry["label"], Decimal(entry["amount_rmb"])) for entry in day["credits"]] == [("充值", Decimal("10"))]
    for debit in debits:
        await test_db.refresh(debit)
        assert debit.amount_rmb == Decimal("-0.25")


async def test_usage_details_paginate_whole_beijing_days(client, test_db):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    # 2026-10-06 15:30 UTC is 23:30 on the 6th and 16:30 UTC is 00:30 on the 7th in Beijing.
    base = datetime(2026, 10, 6, 15, 30)
    test_db.add_all([
        AIUsageRecord(
            operation_id=str(uuid4()), operation="voice", user_id=user_id,
            provider="deepseek", model="deepseek-flash", status="succeeded",
            estimated_cost_rmb=Decimal("0.01"), cost_status="estimated",
            created_at=base + timedelta(hours=hours),
        )
        for hours in (0, 0, 1, 1, 1)
    ])
    await test_db.commit()

    first = (await client.get("/api/billing/ai-usage?limit=1")).json()
    second = (await client.get("/api/billing/ai-usage?limit=1&offset=1")).json()
    assert [(day["day"], Decimal(day["cost_rmb"])) for day in first["days"]] == [("2026-10-07", Decimal("0.03"))]
    assert first["has_more"] is True
    assert [(day["day"], Decimal(day["cost_rmb"])) for day in second["days"]] == [("2026-10-06", Decimal("0.02"))]
    assert second["has_more"] is False


async def test_billable_failure_is_debited_and_listed_but_plain_failure_is_not(client, test_db):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    context = AIUsageContext(operation_id=str(uuid4()), operation="voice", user_id=user_id)
    cost = estimate_audio_cost(provider="volc_streaming_asr", model="volc.seedasr.sauc.duration", duration_seconds=60)
    await record_ai_usage(provider="volc_streaming_asr", model="volc.seedasr.sauc.duration",
                          status="failed", cost=cost, audio_duration_seconds=60, context=context, billable=True)
    await record_ai_usage(provider="volc_streaming_asr", model="volc.seedasr.sauc.duration",
                          status="failed", cost=cost, audio_duration_seconds=60, context=context)

    summary = await get_ai_credit_summary(test_db, user_id=user_id)
    assert summary.used_rmb == Decimal("0.075")
    [day] = (await client.get("/api/billing/ai-usage")).json()["days"]
    assert [(item["purpose"], item["count"]) for item in day["purposes"]] == [("语音识别", 1)]


async def test_admin_adds_recharge_and_grant_credit_and_sees_overdraft(admin_client, test_db):
    user_id = (await admin_client.get("/api/auth/me")).json()["id"]
    record = AIUsageRecord(
        operation_id=str(uuid4()), operation="book", user_id=user_id,
        provider="deepseek", model="deepseek-flash", status="succeeded",
        estimated_cost_rmb=Decimal("12"), cost_status="estimated",
    )
    test_db.add(record)
    await test_db.commit()

    before = (await admin_client.get(f"/api/billing/admin/users/{user_id}/ai-credit")).json()
    assert Decimal(before["summary"]["remaining_rmb"]) == Decimal("-2")

    response = await admin_client.post(
        f"/api/billing/admin/users/{user_id}/ai-credit",
        json={"amount_rmb": "20", "kind": "recharge", "note": "微信转账"},
    )
    assert response.status_code == 200
    response = await admin_client.post(
        f"/api/billing/admin/users/{user_id}/ai-credit",
        json={"amount_rmb": "5", "kind": "grant"},
    )
    body = response.json()
    assert Decimal(body["summary"]["recharged_credit_rmb"]) == Decimal("20")
    assert Decimal(body["summary"]["manual_grant_rmb"]) == Decimal("5")
    assert Decimal(body["summary"]["remaining_rmb"]) == Decimal("23")
    assert [(entry["label"], Decimal(entry["amount_rmb"]), entry["note"]) for entry in body["credits"]] == [
        ("赠送额度", Decimal("5"), ""),
        ("充值", Decimal("20"), "微信转账"),
    ]

    overdrawn = await admin_client.post(
        f"/api/billing/admin/users/{user_id}/ai-credit",
        json={"amount_rmb": "-6", "kind": "grant"},
    )
    assert overdrawn.status_code == 422
