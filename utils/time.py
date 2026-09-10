"""Timezone-safe market-time helpers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """Return the current timezone-aware India Standard Time."""
    return datetime.now(IST)


def as_ist(value: datetime) -> datetime:
    """Normalize a datetime to timezone-aware India Standard Time."""
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def ist_trade_date(value: datetime | None = None) -> str:
    """Return an ISO trading date in IST."""
    return (as_ist(value) if value is not None else now_ist()).date().isoformat()
