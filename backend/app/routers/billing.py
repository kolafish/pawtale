from datetime import UTC
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin_user, get_current_user
from app.database import get_db
from app.models.ai_usage import AIUsageRecord
from app.models.users import User
from app.schemas.billing import (
    AICreditGrantRequest,
    AICreditSummaryResponse,
    AIUsageDetailItem,
    AIUsageDetailResponse,
)
from app.services.ai_credits import get_ai_credit_summary, set_manual_credit_total


router = APIRouter(prefix="/api/billing", tags=["billing"])


def _usage_purpose(record: AIUsageRecord) -> str:
    operation = record.operation.lower()
    if "offline_asr" in operation:
        return "加强语音识别"
    if record.provider in {"volc_streaming_asr", "volc_streaming_asr_reprocess", "stepfun"}:
        return "语音识别"
    if record.provider == "volc_tts":
        return "语音朗读"
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
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AIUsageDetailResponse:
    records = (await db.scalars(
        select(AIUsageRecord)
        .where(
            AIUsageRecord.user_id == current_user.id,
            AIUsageRecord.status == "succeeded",
            AIUsageRecord.estimated_cost_rmb > 0,
        )
        .order_by(AIUsageRecord.created_at.desc(), AIUsageRecord.id.desc())
        .offset(offset)
        .limit(limit + 1)
    )).all()
    return AIUsageDetailResponse(
        items=[
            AIUsageDetailItem(
                id=record.id,
                purpose=_usage_purpose(record),
                cost_rmb=record.estimated_cost_rmb or Decimal("0"),
                created_at=record.created_at.replace(tzinfo=UTC),
            )
            for record in records[:limit]
        ],
        has_more=len(records) > limit,
    )


@router.post(
    "/admin/users/{user_id}/ai-credit",
    response_model=AICreditSummaryResponse,
)
async def set_user_ai_credit(
    user_id: str,
    req: AICreditGrantRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user),
) -> AICreditSummaryResponse:
    target_user = await db.scalar(select(User).where(User.id == user_id))
    if target_user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        return await set_manual_credit_total(
            db,
            user_id=user_id,
            total_credit_rmb=req.total_credit_rmb,
            actor_user_id=admin_user.id,
            note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
