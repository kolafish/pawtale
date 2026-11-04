from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import dataclass
from decimal import Decimal
import logging
from uuid import uuid4

from sqlalchemy import select

from app.database import async_session_maker
from app.models.ai_credits import AICreditAccount, AICreditTransaction
from app.models.ai_usage import AIUsageRecord
from app.services.ai_costs import AICostEstimate, NormalizedAIUsage
from app.services.ai_credits import USAGE_DEBIT, ai_usage_charge, ensure_ai_credit_account


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIUsageContext:
    operation_id: str
    operation: str
    user_id: str | None
    project_id: str | None = None


_CONTEXT: ContextVar[AIUsageContext | None] = ContextVar("ai_usage_context", default=None)


def set_ai_usage_context(
    *,
    user_id: str | None,
    operation: str,
    project_id: str | None = None,
    operation_id: str | None = None,
) -> Token[AIUsageContext | None]:
    return _CONTEXT.set(AIUsageContext(
        operation_id=operation_id or str(uuid4()),
        operation=operation[:160],
        user_id=user_id,
        project_id=project_id,
    ))


def reset_ai_usage_context(token: Token[AIUsageContext | None]) -> None:
    _CONTEXT.reset(token)


def current_ai_usage_context() -> AIUsageContext | None:
    return _CONTEXT.get()


async def record_ai_usage(
    *,
    provider: str,
    model: str,
    status: str,
    cost: AICostEstimate,
    usage: NormalizedAIUsage | None = None,
    input_characters: int | None = None,
    image_count: int | None = None,
    audio_duration_seconds: float | None = None,
    error_type: str | None = None,
    context: AIUsageContext | None = None,
    billable: bool | None = None,
) -> None:
    """Record the upstream estimate and debit the user at the current rate.

    Only successful calls are debited by default. Callers pass ``billable=True``
    for a failed or interrupted call whose usage is still exactly known (for
    example audio already streamed to ASR, or tokens of a rejected structured
    output), since the provider charges for it.
    """
    resolved_context = context or _CONTEXT.get()
    if resolved_context is None:
        logger.warning(
            "AI usage has no user context and is not billed provider=%s model=%s status=%s",
            provider,
            model,
            status,
        )
        return
    if billable is None:
        billable = status == "succeeded"
    if status == "succeeded" and cost.amount_rmb is None:
        logger.warning(
            "AI usage has no price and is not billed provider=%s model=%s reason=%s",
            provider,
            model,
            cost.reason,
        )
    try:
        usage = usage or NormalizedAIUsage()
        charge = (
            ai_usage_charge(cost.amount_rmb)
            if resolved_context.user_id and billable and cost.amount_rmb is not None else Decimal("0")
        )
        record = AIUsageRecord(
            operation_id=resolved_context.operation_id,
            operation=resolved_context.operation,
            user_id=resolved_context.user_id,
            project_id=resolved_context.project_id,
            provider=provider,
            model=model,
            status=status,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            cache_write_input_tokens=usage.cache_write_input_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            input_characters=input_characters,
            image_count=image_count,
            audio_duration_ms=round(audio_duration_seconds * 1000) if audio_duration_seconds else None,
            estimated_cost_rmb=(
                Decimal(str(cost.amount_rmb)) if cost.amount_rmb is not None else None
            ),
            charged_rmb=charge,
            cost_status=cost.status,
            error_type=error_type,
        )
        async with async_session_maker() as db:
            db.add(record)
            await db.flush()
            if resolved_context.user_id and charge > 0:
                await ensure_ai_credit_account(db, resolved_context.user_id, commit=False)
                account = await db.scalar(
                    select(AICreditAccount)
                    .where(AICreditAccount.user_id == resolved_context.user_id)
                    .with_for_update()
                )
                if account is None:
                    raise RuntimeError("AI credit account could not be initialized")
                db.add(
                    AICreditTransaction(
                        account_id=account.id,
                        user_id=resolved_context.user_id,
                        transaction_type=USAGE_DEBIT,
                        amount_rmb=-charge,
                        usage_record_id=record.id,
                        operation_id=resolved_context.operation_id,
                    )
                )
            await db.commit()
    except Exception:
        logger.exception(
            "AI usage record write failed operation_id=%s provider=%s model=%s",
            resolved_context.operation_id,
            provider,
            model,
        )


_background_records: set[asyncio.Task[None]] = set()


def record_ai_usage_in_background(**kwargs) -> None:
    """Record usage from a task that is being cancelled without awaiting the write."""
    kwargs.setdefault("context", _CONTEXT.get())
    task = asyncio.create_task(record_ai_usage(**kwargs))
    _background_records.add(task)
    task.add_done_callback(_background_records.discard)
