from datetime import date, datetime
from decimal import Decimal
from typing import Literal

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


class AIUsagePurposeTotal(BaseModel):
    purpose: str
    count: int
    cost_rmb: Decimal


class AICreditEntry(BaseModel):
    id: str
    kind: str
    label: str
    amount_rmb: Decimal
    note: str
    created_at: datetime


class AIUsageDay(BaseModel):
    """One Beijing-time calendar day of charges and credit additions."""

    day: date
    cost_rmb: Decimal
    purposes: list[AIUsagePurposeTotal]
    credits: list[AICreditEntry]


class AIUsageDetailResponse(BaseModel):
    days: list[AIUsageDay]
    has_more: bool


class AICreditGrantRequest(BaseModel):
    """Add paid recharge or free grant credit; a negative amount corrects a mistake."""

    amount_rmb: Decimal = Field(..., max_digits=14, decimal_places=6)
    kind: Literal["recharge", "grant"]
    note: str = Field(default="", max_length=255)


class AdminAICreditResponse(BaseModel):
    summary: AICreditSummaryResponse
    credits: list[AICreditEntry]


class CreditPackResponse(BaseModel):
    product_id: str
    name: str
    price_rmb: Decimal
    credit_rmb: Decimal


class CreditPacksResponse(BaseModel):
    enabled: bool
    packs: list[CreditPackResponse]


class CreditOrderCreateRequest(BaseModel):
    product_id: str = Field(..., max_length=64)
    # A fresh wx.login code: its session_key signs the payment for this WeChat user.
    code: str = Field(..., min_length=1, max_length=128)


class CreditOrderPaymentResponse(BaseModel):
    """Arguments for wx.requestVirtualPayment, passed through unchanged."""

    order_no: str
    mode: str
    sign_data: str
    pay_sig: str
    signature: str


class CreditOrderStatusResponse(BaseModel):
    order_no: str
    status: Literal["pending", "paid", "refunded"]
    credit_rmb: Decimal
