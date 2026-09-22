from __future__ import annotations

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
) -> None:
    resolved_context = context or _CONTEXT.get()
    if resolved_context is None:
        return
    try:
        usage = usage or NormalizedAIUsage()
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
            cost_status=cost.status,
            error_type=error_type,
        )
        async with async_session_maker() as db:
            db.add(record)
            await db.flush()
            if (
                resolved_context.user_id
                and status == "succeeded"
                and cost.amount_rmb is not None
                and Decimal(str(cost.amount_rmb)) > 0
            ):
                from app.services.ai_credits import ensure_ai_credit_account, USAGE_DEBIT

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
                        user_id=resolved_context.user_id,
                        transaction_type=USAGE_DEBIT,
                        amount_rmb=-Decimal(str(cost.amount_rmb)),
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
