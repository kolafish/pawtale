from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.clock import utcnow_naive
from app.main import app
from app.models.ai_usage import AIUsageRecord
from app.models.users import User


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
