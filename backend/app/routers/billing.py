from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.billing import AICreditSummaryResponse
from app.services.ai_credits import get_ai_credit_summary


router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/ai-credit-summary", response_model=AICreditSummaryResponse)
async def ai_credit_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AICreditSummaryResponse:
    return await get_ai_credit_summary(db, user_id=current_user.id)
