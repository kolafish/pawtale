from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application_errors import InsufficientAICreditError
from app.database import async_session_maker
from app.models.ai_credits import (
    AICreditAccount,
    AICreditTransaction,
    DEFAULT_FREE_CREDIT_RMB,
)
from app.models.ai_usage import AIUsageRecord
from app.schemas.billing import AICreditSummaryResponse


MONEY_QUANTUM = Decimal("0.000001")
USAGE_DEBIT = "usage_debit"
USAGE_REFUND = "usage_refund"
MANUAL_ADJUSTMENT = "manual_adjustment"


def _money(value: Decimal | int | float | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


async def ensure_ai_credit_account(
    db: AsyncSession,
    user_id: str,
    *,
    commit: bool = True,
) -> AICreditAccount:
    statement = insert(AICreditAccount).values(
        user_id=user_id,
        free_credit_rmb=DEFAULT_FREE_CREDIT_RMB,
        recharged_credit_rmb=Decimal("0"),
        manual_grant_rmb=Decimal("0"),
    )
    await db.execute(statement.on_conflict_do_nothing(index_elements=[AICreditAccount.user_id]))
    if commit:
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
    debit_used_rmb = await db.scalar(
        select(func.coalesce(func.sum(-AICreditTransaction.amount_rmb), 0)).where(
            AICreditTransaction.user_id == user_id,
            AICreditTransaction.transaction_type.in_((USAGE_DEBIT, USAGE_REFUND)),
        )
    )
    legacy_used_rmb = await db.scalar(
        select(func.coalesce(func.sum(AIUsageRecord.estimated_cost_rmb), 0)).where(
            AIUsageRecord.user_id == user_id,
            AIUsageRecord.status == "succeeded",
            AIUsageRecord.estimated_cost_rmb.is_not(None),
            ~exists(
                select(AICreditTransaction.id).where(
                    AICreditTransaction.usage_record_id == AIUsageRecord.id
                )
            ),
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
    manual_grant_rmb = _money(account.manual_grant_rmb)
    recharged_credit_rmb = _money(account.recharged_credit_rmb)
    total_credit_rmb = _money(free_credit_rmb + manual_grant_rmb + recharged_credit_rmb)
    used = _money(_money(debit_used_rmb) + _money(legacy_used_rmb))
    return AICreditSummaryResponse(
        free_credit_rmb=free_credit_rmb,
        manual_grant_rmb=manual_grant_rmb,
        recharged_credit_rmb=recharged_credit_rmb,
        total_credit_rmb=total_credit_rmb,
        used_rmb=used,
        remaining_rmb=_money(max(Decimal("0"), total_credit_rmb - used)),
        unpriced_success_count=int(unpriced_success_count or 0),
    )


async def assert_ai_credit_available(user_id: str) -> None:
    """Stop a new billable operation once the user's balance is exhausted."""
    async with async_session_maker() as db:
        summary = await get_ai_credit_summary(db, user_id=user_id)
    if summary.remaining_rmb <= 0:
        raise InsufficientAICreditError("AI 额度已用尽，请添加微信联系管理员")


async def assert_current_ai_credit_available() -> None:
    # Keep this import local: the usage context module records usage into this service.
    from app.services.ai_usage_records import current_ai_usage_context

    context = current_ai_usage_context()
    if context and context.user_id:
        await assert_ai_credit_available(context.user_id)


async def set_manual_credit_total(
    db: AsyncSession,
    *,
    user_id: str,
    total_credit_rmb: Decimal,
    actor_user_id: str,
    note: str = "",
) -> AICreditSummaryResponse:
    total = _money(total_credit_rmb)
    if total < DEFAULT_FREE_CREDIT_RMB:
        raise ValueError("total_credit_rmb must be at least the default free credit")

    await ensure_ai_credit_account(db, user_id, commit=False)
    account = await db.scalar(
        select(AICreditAccount)
        .where(AICreditAccount.user_id == user_id)
        .with_for_update()
    )
    if account is None:
        raise RuntimeError("AI credit account could not be initialized")
    previous_total = _money(
        account.free_credit_rmb
        + account.manual_grant_rmb
        + account.recharged_credit_rmb
    )
    minimum_total = _money(account.free_credit_rmb) + _money(account.recharged_credit_rmb)
    if total < minimum_total:
        raise ValueError("total_credit_rmb cannot be below existing non-manual credit")
    new_manual_grant = _money(
        total - _money(account.free_credit_rmb) - _money(account.recharged_credit_rmb)
    )
    delta = _money(total - previous_total)
    account.manual_grant_rmb = new_manual_grant
    if delta:
        db.add(
            AICreditTransaction(
                user_id=user_id,
                actor_user_id=actor_user_id,
                transaction_type=MANUAL_ADJUSTMENT,
                amount_rmb=delta,
                note=note.strip(),
            )
        )
    await db.commit()
    return await get_ai_credit_summary(db, user_id=user_id)
