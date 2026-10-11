from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application_errors import InsufficientAICreditError, InvalidRequestError
from app.config import get_settings
from app.database import async_session_maker
from app.models.ai_credits import (
    AICreditAccount,
    AICreditTransaction,
    AICreditIdentity,
    AICreditOrder,
    DEFAULT_FREE_CREDIT_RMB,
)
from app.models.ai_usage import AIUsageRecord
from app.models.users import AuthIdentity, User
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
    # Serialize writes with account deletion before taking the wallet lock.
    if await db.scalar(select(User.id).where(User.id == user_id).with_for_update(read=True, key_share=True)) is None:
        raise InvalidRequestError("账号不存在或已注销")
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


async def retain_ai_credit_account(db: AsyncSession, user_id: str) -> None:
    """Keep the credit ledger independently of the deleted user's personal data."""
    identities = (await db.execute(
        select(AuthIdentity.provider, AuthIdentity.provider_user_id, AuthIdentity.provider_union_id)
        .where(AuthIdentity.user_id == user_id)
    )).all()
    if not identities:
        return
    for provider, openid, unionid in identities:
        await reconnect_ai_credit_account(db, user_id, provider=provider, openid=openid, unionid=unionid)
    account = await ensure_ai_credit_account(db, user_id, commit=False)
    # Old priced usage predates the ledger. Materialize it before its user FK is cleared.
    legacy = (await db.scalars(select(AIUsageRecord).where(
        AIUsageRecord.user_id == user_id,
        AIUsageRecord.status == "succeeded",
        AIUsageRecord.estimated_cost_rmb > 0,
        ~exists(select(AICreditTransaction.id).where(
            AICreditTransaction.usage_record_id == AIUsageRecord.id,
        )),
    ))).all()
    for usage in legacy:
        db.add(AICreditTransaction(
            account_id=account.id, user_id=user_id, transaction_type=USAGE_DEBIT,
            amount_rmb=-usage.estimated_cost_rmb, usage_record_id=usage.id,
            operation_id=usage.operation_id, created_at=usage.created_at,
        ))


async def _bind_credit_identities(db: AsyncSession, hashes: set[str], account_id: str) -> None:
    await db.execute(insert(AICreditIdentity).values([
        {"key_hash": key_hash, "account_id": account_id} for key_hash in sorted(hashes)
    ]).on_conflict_do_update(
        index_elements=[AICreditIdentity.key_hash], set_={"account_id": account_id},
        where=AICreditIdentity.account_id.is_distinct_from(account_id),
    ))


async def reconnect_ai_credit_account(
    db: AsyncSession, user_id: str, *, provider: str, openid: str, unionid: str | None,
) -> None:
    """Reattach the same wallet after re-registration; never replenish the initial grant."""
    hashes = _free_credit_claim_hashes(provider, openid, unionid)
    identities = (await db.scalars(select(AICreditIdentity).where(
        AICreditIdentity.key_hash.in_(hashes),
    ))).all()
    account_ids = {identity.account_id for identity in identities if identity.account_id}
    current = await db.scalar(select(AICreditAccount).where(AICreditAccount.user_id == user_id))
    if current is not None and account_ids == {current.id} and all(identity.account_id for identity in identities):
        await _bind_credit_identities(db, hashes, current.id)
        return
    other_ids = account_ids - ({current.id} if current else set())
    if len(other_ids) > 1:
        raise InvalidRequestError("微信额度账户关联冲突，请联系客服核实")
    if account_ids:
        account_id = next(iter(other_ids or account_ids))
        locked_ids = {account_id} | ({current.id} if current else set())
        # Payment callbacks lock orders before wallets; follow the same order on reattachment.
        await db.execute(select(AICreditOrder.out_trade_no).where(
            AICreditOrder.account_id.in_(locked_ids),
        ).order_by(AICreditOrder.out_trade_no).with_for_update())
        await db.execute(select(User.id).where(User.id == user_id).with_for_update(read=True, key_share=True))
        locked = (await db.scalars(select(AICreditAccount).where(
            AICreditAccount.id.in_(locked_ids),
        ).order_by(AICreditAccount.id).with_for_update().execution_options(populate_existing=True))).all()
        retained = next(account for account in locked if account.id == account_id)
        if retained.user_id not in (None, user_id):
            raise InvalidRequestError("微信额度账户已关联其他账号，请联系客服核实")
        if current is not None and current.id != retained.id:
            # A later WeChat login can expose unionid after this new account has been used.
            # Keep its charges and additions, but not a second initial free grant.
            current.free_credit_rmb = retained.free_credit_rmb
            current.recharged_credit_rmb += retained.recharged_credit_rmb
            current.manual_grant_rmb += retained.manual_grant_rmb
            for model in (AICreditTransaction, AICreditOrder, AICreditIdentity):
                await db.execute(update(model).where(model.account_id == retained.id).values(account_id=current.id))
            await db.delete(retained)
        else:
            current = retained
            current.user_id = user_id
            if any(identity.account_id is None for identity in identities):
                current.free_credit_rmb = Decimal("0")
        for model in (AICreditTransaction, AICreditOrder):
            await db.execute(update(model).where(
                model.account_id == current.id, model.user_id.is_distinct_from(user_id),
            ).values(user_id=user_id))
    else:
        current = await ensure_ai_credit_account(db, user_id, commit=False)
        if identities:
            # Historical tombstones have no saved balance. Keep anti-abuse behavior until repaired.
            current.free_credit_rmb = Decimal("0")
    await _bind_credit_identities(db, hashes, current.id)


async def get_ai_credit_summary(
    db: AsyncSession,
    *,
    user_id: str,
) -> AICreditSummaryResponse:
    account = await ensure_ai_credit_account(db, user_id)
    return await credit_account_summary(db, account)


async def credit_account_summary(db: AsyncSession, account: AICreditAccount) -> AICreditSummaryResponse:
    debit_used_rmb = await db.scalar(
        select(func.coalesce(func.sum(-AICreditTransaction.amount_rmb), 0)).where(
            AICreditTransaction.account_id == account.id,
            AICreditTransaction.transaction_type.in_((USAGE_DEBIT, USAGE_REFUND)),
        )
    )
    legacy_used_rmb = await db.scalar(
        select(func.coalesce(func.sum(AIUsageRecord.estimated_cost_rmb), 0)).where(
            AIUsageRecord.user_id == account.user_id,
            account.user_id is not None,
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
            AIUsageRecord.user_id == account.user_id,
            account.user_id is not None,
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
        raise InsufficientAICreditError("AI 额度已用尽，可在「我的」页充值")


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
    account = await ensure_ai_credit_account(db, user_id, commit=False)
    await add_credit_to_account(
        db, account_id=account.id, amount_rmb=amount_rmb, kind=kind,
        actor_user_id=actor_user_id, note=note, operation_id=operation_id,
    )


async def add_credit_to_account(
    db: AsyncSession, *, account_id: str, amount_rmb: Decimal, kind: CreditKind,
    actor_user_id: str | None, note: str = "", operation_id: str | None = None,
) -> None:
    amount = _money(amount_rmb)
    if not amount:
        raise ValueError("amount_rmb must not be zero")
    account = await db.scalar(select(AICreditAccount).where(
        AICreditAccount.id == account_id,
    ).with_for_update().execution_options(populate_existing=True))
    if account is None:
        raise RuntimeError("AI credit account does not exist")
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
            account_id=account.id,
            user_id=account.user_id,
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
