from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.clock import utcnow_naive
from app.main import app
from app.models.ai_credits import AICreditTransaction
from app.models.ai_usage import AIUsageRecord
from app.models.users import User
from app.services.ai_costs import AICostEstimate, estimate_ai_cost, estimate_audio_cost, normalize_ai_usage
from app.services.ai_credits import (
    USAGE_DEBIT, USAGE_REFUND, ensure_ai_credit_account, get_ai_credit_summary,
)
from app.services.ai_usage_records import AIUsageContext, record_ai_usage


@pytest.mark.parametrize(("at", "expected_used", "unpriced"), [
    (datetime(2026, 10, 7, 2, 0, tzinfo=UTC), Decimal("0.001408"), 0),
    (datetime(2027, 1, 4, 2, 0, tzinfo=UTC), Decimal("0"), 1),
])
async def test_deepseek_holiday_or_unknown_calendar_charges_no_peak_price(client, test_db, at, expected_used, unpriced):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    usage = normalize_ai_usage({"prompt_tokens": 1_000, "prompt_cache_hit_tokens": 400, "completion_tokens": 200})
    cost = estimate_ai_cost(provider="deepseek", model="deepseek-flash", usage=usage, at=at)
    await record_ai_usage(
        provider="deepseek", model="deepseek-flash", status="succeeded", cost=cost, usage=usage,
        context=AIUsageContext(operation_id=str(uuid4()), operation="voice", user_id=user_id),
    )
    summary = await get_ai_credit_summary(test_db, user_id=user_id)
    assert summary.used_rmb == expected_used
    assert summary.remaining_rmb == Decimal("5") - expected_used
    assert summary.unpriced_success_count == unpriced
    [record] = (await test_db.scalars(select(AIUsageRecord).where(AIUsageRecord.user_id == user_id))).all()
    assert record.input_tokens == 1_000
    assert record.cached_input_tokens == 400
    assert record.output_tokens == 200
    assert record.estimated_cost_rmb == (None if unpriced else expected_used)


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
        ("访谈语音回复", 1, Decimal("0.003")),
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
        account_id=account.id, user_id=user_id, transaction_type=USAGE_DEBIT, amount_rmb=Decimal("-0.25"),
        usage_record_id=record.id, operation_id=record.operation_id,
    ) for record in charges]
    test_db.add_all([
        *debits,
        AICreditTransaction(account_id=account.id, user_id=user_id, transaction_type=USAGE_REFUND,
                            amount_rmb=Decimal("0.35"), note="Correct rate and duplicate generation"),
        AICreditTransaction(account_id=account.id, user_id=user_id, transaction_type="recharge", amount_rmb=Decimal("10")),
    ])
    await test_db.commit()

    summary = await get_ai_credit_summary(test_db, user_id=user_id)
    assert summary.recharged_credit_rmb == Decimal("10")
    assert summary.total_credit_rmb == Decimal("15")
    assert summary.used_rmb == Decimal("0.17")
    assert summary.remaining_rmb == Decimal("14.83")
    [day] = (await client.get("/api/billing/ai-usage")).json()["days"]
    assert {Decimal(item["cost_rmb"]) for item in day["purposes"]} == {Decimal("0.15"), Decimal("0.02")}
    assert [(entry["label"], Decimal(entry["amount_rmb"])) for entry in day["credits"]] == [("充值", Decimal("10"))]
    for debit in debits:
        await test_db.refresh(debit)
        assert debit.amount_rmb == Decimal("-0.25")


async def test_usage_categories_distinguish_interview_audio_and_share_candidates_without_changing_charges(client, test_db):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    # The paths and job names come from the October 10 production investigation.
    session_path = "/api/mobile/memoir/voice-sessions/session"
    samples = [
        (f"{session_path}/assistant-turn-stream", "volc_tts", "seed-tts-2.0", "0.021300"),
        (f"{session_path}/messages/message/tts", "volc_tts", "seed-tts-2.0", "0.018900"),
        ("memoir.job.generate_book_audio_chunk", "volc_tts", "seed-tts-2.0", "0.150000"),
        ("memoir.job.evaluate_share_video_candidate", "deepseek", "deepseek-flash", "0.001984"),
        ("memoir.share_video_generation", "volc_ark", "doubao-seedream-5-0-260128", "0.880000"),
        ("memoir.share_video_title", "deepseek", "deepseek-flash", "0.000410"),
    ]
    for operation, provider, model, amount in samples:
        await record_ai_usage(
            provider=provider, model=model, status="succeeded",
            cost=AICostEstimate(float(amount), "estimated", provider, model),
            context=AIUsageContext(operation_id=str(uuid4()), operation=operation, user_id=user_id),
        )
    expected_cost = Decimal("1.072594")
    [day] = (await client.get("/api/billing/ai-usage")).json()["days"]
    assert Decimal(day["cost_rmb"]) == expected_cost
    assert {item["purpose"]: (item["count"], Decimal(item["cost_rmb"])) for item in day["purposes"]} == {
        "访谈语音回复": (2, Decimal("0.040200")),
        "语音朗读": (1, Decimal("0.150000")),
        "分享片段筛选": (1, Decimal("0.001984")),
        "分享视频生成": (2, Decimal("0.880410")),
    }
    summary = (await client.get("/api/billing/ai-credit-summary")).json()
    assert Decimal(summary["used_rmb"]) == expected_cost



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
    assert summary.used_rmb == Decimal("0.016667")
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
    assert Decimal(before["summary"]["remaining_rmb"]) == Decimal("-7")

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
    assert Decimal(body["summary"]["remaining_rmb"]) == Decimal("18")
    assert [(entry["label"], Decimal(entry["amount_rmb"]), entry["note"]) for entry in body["credits"]] == [
        ("赠送额度", Decimal("5"), ""),
        ("充值", Decimal("20"), "微信转账"),
    ]

    overdrawn = await admin_client.post(
        f"/api/billing/admin/users/{user_id}/ai-credit",
        json={"amount_rmb": "-6", "kind": "grant"},
    )
    assert overdrawn.status_code == 422


async def test_stepfun_narration_is_not_counted_as_speech_recognition(client, test_db):
    user_id = (await client.get("/api/auth/me")).json()["id"]
    for provider, model, operation, amount in [
        ("stepfun", "step-tts-mini", "memoir.job.generate_book_audio_chunk", "0.168885"),
        ("volc_streaming_asr", "volc.seedasr.sauc.duration", "voice/gateway", "0.086393"),
        ("stepfun", "stepaudio-3-asr-max", "transcribe", "0.01"),
    ]:
        test_db.add(AIUsageRecord(operation_id=str(uuid4()), operation=operation, user_id=user_id,
            provider=provider, model=model, status="succeeded", cost_status="estimated",
            estimated_cost_rmb=Decimal(amount)))
    await test_db.commit()
    day = (await client.get("/api/billing/ai-usage")).json()["days"][0]
    assert {item["purpose"]: Decimal(item["cost_rmb"]) for item in day["purposes"]} == {
        "语音朗读": Decimal("0.168885"), "语音识别": Decimal("0.096393"),
    }
    assert Decimal(day["cost_rmb"]) == Decimal("0.265278")
