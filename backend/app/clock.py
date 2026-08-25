from __future__ import annotations

from datetime import UTC, datetime


def utcnow_naive() -> datetime:
    """Return UTC without tzinfo for the database DateTime schema."""
    return datetime.now(UTC).replace(tzinfo=None)
