from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AICreditSummaryResponse(BaseModel):
    currency: str = "CNY"
    free_credit_rmb: Decimal
    manual_grant_rmb: Decimal
    recharged_credit_rmb: Decimal
    total_credit_rmb: Decimal
    used_rmb: Decimal
    remaining_rmb: Decimal
    unpriced_success_count: int

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "currency": "CNY",
            "free_credit_rmb": 10,
            "manual_grant_rmb": 0,
            "recharged_credit_rmb": 0,
            "total_credit_rmb": 10,
            "used_rmb": 0.12,
            "remaining_rmb": 9.88,
            "unpriced_success_count": 0,
        }
    })


class AIUsageDetailItem(BaseModel):
    id: str
    purpose: str
    cost_rmb: Decimal
    created_at: datetime


class AIUsageDetailResponse(BaseModel):
    items: list[AIUsageDetailItem]
    has_more: bool


class AICreditGrantRequest(BaseModel):
    """Set a user's total available credit for the current manual-grant phase."""

    total_credit_rmb: Decimal = Field(..., ge=10, max_digits=14, decimal_places=6)
    note: str = Field(default="", max_length=255)
