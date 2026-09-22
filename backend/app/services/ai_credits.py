from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_credits import AICreditAccount, DEFAULT_FREE_CREDIT_RMB
from app.models.ai_usage import AIUsageRecord
from app.schemas.billing import AICreditSummaryResponse


MONEY_QUANTUM = Decimal("0.000001")


def _money(value: Decimal | int | float | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


async def ensure_ai_credit_account(db: AsyncSession, user_id: str) -> AICreditAccount:
    statement = insert(AICreditAccount).values(
        user_id=user_id,
        free_credit_rmb=DEFAULT_FREE_CREDIT_RMB,
        recharged_credit_rmb=Decimal("0"),
    )
    await db.execute(statement.on_conflict_do_nothing(index_elements=[AICreditAccount.user_id]))
    await db.commit()
    account = await db.scalar(
        select(AICreditAccount).where(AICreditAccount.user_id == user_id)
    )
    if account is None:
        raise RuntimeError("AI credit account could not be initialized")
    return account


async def get_ai_credit_summary(
    db: AsyncSession,
    *,
    user_id: str,
) -> AICreditSummaryResponse:
    account = await ensure_ai_credit_account(db, user_id)
    used_rmb = await db.scalar(
        select(func.coalesce(func.sum(AIUsageRecord.estimated_cost_rmb), 0)).where(
            AIUsageRecord.user_id == user_id,
            AIUsageRecord.status == "succeeded",
            AIUsageRecord.estimated_cost_rmb.is_not(None),
        )
    )
    unpriced_success_count = await db.scalar(
        select(func.count(AIUsageRecord.id)).where(
            AIUsageRecord.user_id == user_id,
            AIUsageRecord.status == "succeeded",
            AIUsageRecord.estimated_cost_rmb.is_(None),
        )
    )
    free_credit_rmb = _money(account.free_credit_rmb)
    recharged_credit_rmb = _money(account.recharged_credit_rmb)
    total_credit_rmb = _money(free_credit_rmb + recharged_credit_rmb)
    used = _money(used_rmb)
    return AICreditSummaryResponse(
        free_credit_rmb=free_credit_rmb,
        recharged_credit_rmb=recharged_credit_rmb,
        total_credit_rmb=total_credit_rmb,
        used_rmb=used,
        remaining_rmb=_money(max(Decimal("0"), total_credit_rmb - used)),
        unpriced_success_count=int(unpriced_success_count or 0),
    )
