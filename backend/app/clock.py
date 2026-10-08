from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone


BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def utcnow_naive() -> datetime:
    """Return UTC without tzinfo for the database DateTime schema."""
    return datetime.now(UTC).replace(tzinfo=None)


def beijing_date(value: datetime) -> date:
    """Return the product calendar day; naive database timestamps represent UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BEIJING_TIMEZONE).date()
