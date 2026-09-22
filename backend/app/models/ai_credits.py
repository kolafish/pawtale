from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow_naive
from app.database import Base


DEFAULT_FREE_CREDIT_RMB = Decimal("10.000000")


class AICreditAccount(Base):
    __tablename__ = "ai_credit_accounts"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    free_credit_rmb: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, default=DEFAULT_FREE_CREDIT_RMB, server_default=text("10")
    )
    recharged_credit_rmb: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
