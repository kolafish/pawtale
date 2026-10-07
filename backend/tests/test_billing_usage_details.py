from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.clock import utcnow_naive
from app.main import app
from app.models.ai_credits import AICreditTransaction
from app.models.ai_usage import AIUsageRecord
from app.models.users import User
from app.services.ai_credits import (
    USAGE_DEBIT, USAGE_REFUND, ensure_ai_credit_account, get_ai_credit_summary,
)


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

    first = (await client.get("/api/billing/ai-usage?limit=1")).json()
    second = (await client.get("/api/billing/ai-usage?limit=1&offset=1")).json()
    assert first["has_more"] is True
    assert first["items"][0]["purpose"] == "语音朗读"
    assert Decimal(first["items"][0]["cost_rmb"]) == Decimal("0.003")
    assert second["has_more"] is False
    assert second["items"][0]["purpose"] == "语音访谈"
    assert Decimal(second["items"][0]["cost_rmb"]) == Decimal("0.02")

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
    details = (await client.get("/api/billing/ai-usage")).json()["items"]
    assert {Decimal(item["cost_rmb"]) for item in details} == {Decimal("0.15"), Decimal("0.02")}
    for debit in debits:
        await test_db.refresh(debit)
        assert debit.amount_rmb == Decimal("-0.25")
