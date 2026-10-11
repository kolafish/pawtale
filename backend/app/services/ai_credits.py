from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application_errors import InsufficientAICreditError
from app.clock import utcnow_naive
from app.config import get_settings
from app.database import async_session_maker
from app.models.ai_credits import (
    AICreditAccount,
    AICreditTransaction,
    AIFreeCreditClaim,
    DEFAULT_FREE_CREDIT_RMB,
)
from app.models.ai_usage import AIUsageRecord
from app.models.users import AuthIdentity
from app.schemas.billing import AICreditSummaryResponse


MONEY_QUANTUM = Decimal("0.000001")
USAGE_DEBIT = "usage_debit"
USAGE_REFUND = "usage_refund"
# Historical admin edits of the total credit; new additions use the types below.
MANUAL_ADJUSTMENT = "manual_adjustment"
RECHARGE = "recharge"
RECHARGE_REFUND = "recharge_refund"
MANUAL_GRANT = "manual_grant"
CREDIT_TRANSACTION_TYPES = (MANUAL_ADJUSTMENT, RECHARGE, RECHARGE_REFUND, MANUAL_GRANT)
CreditKind = Literal["recharge", "recharge_refund", "grant"]
_CREDIT_KIND_TYPES = {"recharge": RECHARGE, "recharge_refund": RECHARGE_REFUND, "grant": MANUAL_GRANT}


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


def _free_credit_claim_hashes(provider: str, openid: str, unionid: str | None) -> set[str]:
    keys = [f"openid:{provider}:{openid}"]
    if unionid:
        keys.append(f"unionid:{unionid}")
    secret = get_settings().app_secret_key.encode("utf-8")
    return {
        hmac.new(secret, f"free-ai-credit:{key}".encode("utf-8"), hashlib.sha256).hexdigest()
        for key in keys
    }


async def record_free_credit_claims(db: AsyncSession, user_id: str) -> None:
    """Remember the user's WeChat identities so a re-created account gets no second free credit."""
    rows = (await db.execute(
        select(
            AuthIdentity.provider,
            AuthIdentity.provider_user_id,
            AuthIdentity.provider_union_id,
        ).where(AuthIdentity.user_id == user_id)
    )).all()
    hashes = set().union(*(_free_credit_claim_hashes(*row) for row in rows))
    if hashes:
        await db.execute(
            insert(AIFreeCreditClaim)
            .values([{"key_hash": key_hash} for key_hash in sorted(hashes)])
            .on_conflict_do_nothing(index_elements=[AIFreeCreditClaim.key_hash])
        )


async def withhold_claimed_free_credit(
    db: AsyncSession,
    user_id: str,
    *,
    provider: str,
    openid: str,
    unionid: str | None,
) -> None:
    """Withhold duplicate free credit when a WeChat identity is recognized, including on later logins."""
    claimed = await db.scalar(
        select(AIFreeCreditClaim.key_hash)
        .where(AIFreeCreditClaim.key_hash.in_(_free_credit_claim_hashes(provider, openid, unionid)))
        .limit(1)
    )
    if claimed:
        await db.execute(
            insert(AICreditAccount)
            .values(
                user_id=user_id,
                free_credit_rmb=Decimal("0"),
                recharged_credit_rmb=Decimal("0"),
                manual_grant_rmb=Decimal("0"),
            )
            .on_conflict_do_update(
                index_elements=[AICreditAccount.user_id],
                set_={"free_credit_rmb": Decimal("0"), "updated_at": utcnow_naive()},
                where=AICreditAccount.free_credit_rmb != Decimal("0"),
            )
        )


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
        remaining_rmb=_money(total_credit_rmb - used),
        unpriced_success_count=int(unpriced_success_count or 0),
    )


async def assert_ai_credit_available(user_id: str) -> None:
    """Stop a new billable operation once the user's balance is exhausted."""
    async with async_session_maker() as db:
        summary = await get_ai_credit_summary(db, user_id=user_id)
    if summary.remaining_rmb <= 0:
        raise InsufficientAICreditError("AI 额度已用尽")


async def assert_current_ai_credit_available() -> None:
    # Keep this import local: the usage context module records usage into this service.
    from app.services.ai_usage_records import current_ai_usage_context

    context = current_ai_usage_context()
    if context and context.user_id:
        await assert_ai_credit_available(context.user_id)


async def add_ai_credit(
    db: AsyncSession,
    *,
    user_id: str,
    amount_rmb: Decimal,
    kind: CreditKind,
    actor_user_id: str,
    note: str = "",
    operation_id: str | None = None,
) -> None:
    """Add (or, with a negative amount, correct or refund) paid recharge or free grant credit."""
    amount = _money(amount_rmb)
    if not amount:
        raise ValueError("amount_rmb must not be zero")

    await ensure_ai_credit_account(db, user_id, commit=False)
    account = await db.scalar(
        select(AICreditAccount)
        .where(AICreditAccount.user_id == user_id)
        .with_for_update()
    )
    if account is None:
        raise RuntimeError("AI credit account could not be initialized")
    if kind != "grant":
        new_balance = _money(account.recharged_credit_rmb) + amount
        if new_balance < 0:
            raise ValueError("recharged credit cannot become negative")
        account.recharged_credit_rmb = new_balance
    else:
        new_balance = _money(account.manual_grant_rmb) + amount
        if new_balance < 0:
            raise ValueError("granted credit cannot become negative")
        account.manual_grant_rmb = new_balance
    db.add(
        AICreditTransaction(
            user_id=user_id,
            actor_user_id=actor_user_id,
            transaction_type=_CREDIT_KIND_TYPES[kind],
            amount_rmb=amount,
            operation_id=operation_id,
            note=note.strip(),
        )
    )
    await db.commit()


async def list_credit_transactions(
    db: AsyncSession,
    *,
    user_id: str,
    limit: int = 50,
) -> list[AICreditTransaction]:
    return list((await db.scalars(
        select(AICreditTransaction)
        .where(
            AICreditTransaction.user_id == user_id,
            AICreditTransaction.transaction_type.in_(CREDIT_TRANSACTION_TYPES),
        )
        .order_by(AICreditTransaction.created_at.desc(), AICreditTransaction.id.desc())
        .limit(limit)
    )).all())
