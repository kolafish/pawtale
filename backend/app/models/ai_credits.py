from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, text
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
    manual_grant_rmb: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class AICreditTransaction(Base):
    __tablename__ = "ai_credit_transactions"
    __table_args__ = (
        CheckConstraint("amount_rmb <> 0", name="ck_ai_credit_transactions_nonzero_amount"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    amount_rmb: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    usage_record_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("ai_usage_records.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    operation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive, index=True)


class AIFreeCreditClaim(Base):
    """Keyed hash of a WeChat identity whose account was deleted after receiving free credit."""

    __tablename__ = "ai_free_credit_claims"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class AICreditOrder(Base):
    """A credit pack bought with WeChat virtual payment; out_trade_no is our order number."""

    __tablename__ = "ai_credit_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'paid', 'refunded')", name="ck_ai_credit_orders_status"
        ),
    )

    out_trade_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    price_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_rmb: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    wx_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refunded_fen: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
