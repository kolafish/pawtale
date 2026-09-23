from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin_user, get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.billing import AICreditGrantRequest, AICreditSummaryResponse
from app.services.ai_credits import get_ai_credit_summary, set_manual_credit_total


router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/ai-credit-summary", response_model=AICreditSummaryResponse)
async def ai_credit_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AICreditSummaryResponse:
    return await get_ai_credit_summary(db, user_id=current_user.id)


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
