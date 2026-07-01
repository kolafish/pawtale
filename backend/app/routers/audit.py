from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin_user, get_current_user
from app.database import get_db
from app.models.users import User
from app.schemas.audit import (
    AuditDashboardResponse,
    AuditEventsIngestRequest,
    AuditEventsIngestResponse,
    AuditUserDayResponse,
)
from app.services import audit as audit_service


router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.post("/events", response_model=AuditEventsIngestResponse)
async def ingest_audit_events(
    req: AuditEventsIngestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await audit_service.ingest_events(db, current_user.id, req)


@router.get("/dashboard", response_model=AuditDashboardResponse)
async def audit_dashboard(
    from_date: date = Query(alias="from"),
    to_date: date = Query(alias="to"),
    user_id: str | None = None,
    platform: str | None = None,
    feature_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(get_current_admin_user),
):
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="from 不能晚于 to")
    return await audit_service.dashboard(
        db,
        start=from_date,
        end=to_date,
        user_id=user_id,
        platform=platform,
        feature_code=feature_code,
    )


@router.get("/users/{user_id}/days/{day}", response_model=AuditUserDayResponse)
async def audit_user_day(
    user_id: str,
    day: date,
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(get_current_admin_user),
):
    response = await audit_service.user_day(db, user_id, day)
    if response is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return response
