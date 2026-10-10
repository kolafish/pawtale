from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, literal_column, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin_user, get_current_user
from app.database import get_db
from app.models.ai_credits import AICreditTransaction
from app.models.ai_usage import AIUsageRecord
from app.models.users import User
from app.schemas.billing import (
    AdminAICreditResponse,
    AICreditEntry,
    AICreditGrantRequest,
    AICreditSummaryResponse,
    AIUsageDay,
    AIUsageDetailResponse,
    AIUsagePurposeTotal,
)
from app.services.ai_credits import (
    CREDIT_TRANSACTION_TYPES,
    MANUAL_GRANT,
    RECHARGE,
    USAGE_DEBIT,
    add_ai_credit,
    get_ai_credit_summary,
    list_credit_transactions,
)


router = APIRouter(prefix="/api/billing", tags=["billing"])

# Users are in China, so ledger days follow Beijing time.
BEIJING_OFFSET = timedelta(hours=8)
_CREDIT_LABELS = {RECHARGE: "充值", MANUAL_GRANT: "赠送额度"}


def _beijing_day(column):
    return func.date(column + literal_column("interval '8 hours'"))


def _credit_entry(transaction: AICreditTransaction) -> AICreditEntry:
    return AICreditEntry(
        id=transaction.id,
        kind=transaction.transaction_type,
        label=_CREDIT_LABELS.get(transaction.transaction_type, "额度调整"),
        amount_rmb=transaction.amount_rmb,
        note=transaction.note,
        created_at=transaction.created_at.replace(tzinfo=UTC),
    )


def _usage_purpose(record: AIUsageRecord) -> str:
    operation = record.operation.lower()
    if "offline_asr" in operation:
        return "加强语音识别"
    if record.provider in {"volc_streaming_asr", "volc_streaming_asr_reprocess", "stepfun"}:
        return "语音识别"
    if record.provider == "volc_tts":
        return "访谈语音回复" if "/voice-sessions/" in operation else "语音朗读"
    if operation == "memoir.job.evaluate_share_video_candidate":
        return "分享片段筛选"
    if operation == "memoir.job.evaluate_interview_policy_shadow":
        return "访谈策略评测"
    if "share_video" in operation:
        return "分享视频生成"
    if record.image_count or "image_generation" in operation:
        return "图片生成"
    if "book-plan" in operation or "prepare_chapter" in operation:
        return "目录与章节生成"
    if any(part in operation for part in ("book", "chapter", "section")):
        return "书稿生成与编辑"
    if "voice" in operation or "interview" in operation:
        return "语音访谈"
    if any(part in operation for part in ("transcript", "material", "story")):
        return "讲述整理"
    return "AI 文本处理"


@router.get("/ai-credit-summary", response_model=AICreditSummaryResponse)
async def ai_credit_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AICreditSummaryResponse:
    return await get_ai_credit_summary(db, user_id=current_user.id)


@router.get("/ai-usage", response_model=AIUsageDetailResponse)
async def ai_usage_details(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=7, ge=1, le=31),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AIUsageDetailResponse:
    """Charges grouped by day and purpose, plus credit additions; paginated by day."""
    charged = (
        AIUsageRecord.user_id == current_user.id,
        AIUsageRecord.estimated_cost_rmb > 0,
        # Same rule as the balance: debited records, plus successful records
        # from before the ledger existed.
        or_(
            AIUsageRecord.status == "succeeded",
            exists(select(AICreditTransaction.id).where(
                AICreditTransaction.usage_record_id == AIUsageRecord.id,
                AICreditTransaction.transaction_type == USAGE_DEBIT,
            )),
        ),
    )
    credited = (
        AICreditTransaction.user_id == current_user.id,
        AICreditTransaction.transaction_type.in_(CREDIT_TRANSACTION_TYPES),
    )
    day_rows = union(
        select(_beijing_day(AIUsageRecord.created_at).label("day")).where(*charged),
        select(_beijing_day(AICreditTransaction.created_at).label("day")).where(*credited),
    ).subquery()
    days: list[date] = list((await db.scalars(
        select(day_rows.c.day).order_by(day_rows.c.day.desc()).offset(offset).limit(limit + 1)
    )).all())
    has_more = len(days) > limit
    days = days[:limit]
    if not days:
        return AIUsageDetailResponse(days=[], has_more=False)
    start = datetime.combine(min(days), time.min) - BEIJING_OFFSET
    end = datetime.combine(max(days) + timedelta(days=1), time.min) - BEIJING_OFFSET

    purposes: dict[date, dict[str, list]] = defaultdict(dict)
    records = (await db.scalars(
        select(AIUsageRecord)
        .where(*charged, AIUsageRecord.created_at >= start, AIUsageRecord.created_at < end)
        .order_by(AIUsageRecord.created_at.desc())
    )).all()
    for record in records:
        day = (record.created_at + BEIJING_OFFSET).date()
        totals = purposes[day].setdefault(_usage_purpose(record), [0, Decimal("0")])
        totals[0] += 1
        totals[1] += record.estimated_cost_rmb or Decimal("0")

    credits: dict[date, list[AICreditEntry]] = defaultdict(list)
    transactions = (await db.scalars(
        select(AICreditTransaction)
        .where(
            *credited,
            AICreditTransaction.created_at >= start,
            AICreditTransaction.created_at < end,
        )
        .order_by(AICreditTransaction.created_at.desc())
    )).all()
    for transaction in transactions:
        credits[(transaction.created_at + BEIJING_OFFSET).date()].append(_credit_entry(transaction))

    return AIUsageDetailResponse(
        days=[
            AIUsageDay(
                day=day,
                cost_rmb=sum((cost for _count, cost in purposes[day].values()), Decimal("0")),
                purposes=sorted(
                    (
                        AIUsagePurposeTotal(purpose=purpose, count=count, cost_rmb=cost)
                        for purpose, (count, cost) in purposes[day].items()
                    ),
                    key=lambda item: item.cost_rmb,
                    reverse=True,
                ),
                credits=credits[day],
            )
            for day in days
        ],
        has_more=has_more,
    )


@router.get(
    "/admin/users/{user_id}/ai-credit",
    response_model=AdminAICreditResponse,
)
async def get_user_ai_credit(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(get_current_admin_user),
) -> AdminAICreditResponse:
    if await db.scalar(select(User.id).where(User.id == user_id)) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return AdminAICreditResponse(
        summary=await get_ai_credit_summary(db, user_id=user_id),
        credits=[
            _credit_entry(transaction)
            for transaction in await list_credit_transactions(db, user_id=user_id)
        ],
    )


@router.post(
    "/admin/users/{user_id}/ai-credit",
    response_model=AdminAICreditResponse,
)
async def add_user_ai_credit(
    user_id: str,
    req: AICreditGrantRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user),
) -> AdminAICreditResponse:
    target_user = await db.scalar(select(User).where(User.id == user_id))
    if target_user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        await add_ai_credit(
            db,
            user_id=user_id,
            amount_rmb=req.amount_rmb,
            kind=req.kind,
            actor_user_id=admin_user.id,
            note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await get_user_ai_credit(user_id, db, admin_user)
