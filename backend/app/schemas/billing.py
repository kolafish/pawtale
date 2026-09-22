from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class AICreditSummaryResponse(BaseModel):
    currency: str = "CNY"
    free_credit_rmb: Decimal
    recharged_credit_rmb: Decimal
    total_credit_rmb: Decimal
    used_rmb: Decimal
    remaining_rmb: Decimal
    unpriced_success_count: int

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "currency": "CNY",
            "free_credit_rmb": 10,
            "recharged_credit_rmb": 0,
            "total_credit_rmb": 10,
            "used_rmb": 0.12,
            "remaining_rmb": 9.88,
            "unpriced_success_count": 0,
        }
    })
