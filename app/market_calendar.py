# market_calendar.py — NSE trading-holiday awareness.
#
# is_market_open() previously only checked weekday + clock window, so an
# exchange holiday falling Mon–Fri was reported as "market open" and the
# poller would happily scan a dead market. This module fixes that and gives
# callers a specific reason (weekend / holiday / outside hours / open)
# instead of a bare boolean, so "no signals" and "market's shut" no longer
# look identical to the end user.
#
# IMPORTANT — MAINTENANCE: NSE publishes this list once a year (usually
# December for the following year). Update NSE_TRADING_HOLIDAYS every
# December or this will silently go stale, the same way the old
# weekday-only check silently went stale the day it was written.
# Verify against the official NSE circular, not just this file, before
# trusting it blindly: https://www.nseindia.com/resources/exchange-communication-holidays

from __future__ import annotations
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import (
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
)

IST = ZoneInfo("Asia/Kolkata")

# Full-day NSE equity & equity-derivatives trading holidays that fall on a
# weekday (weekend holidays are irrelevant to this check and omitted).
# Source: NSE 2026 holiday circular, cross-checked across multiple market
# calendars in September 2026.
NSE_TRADING_HOLIDAYS: dict[int, set[date]] = {
    2026: {
        date(2026, 1, 26),   # Republic Day
        date(2026, 3, 3),    # Holi
        date(2026, 3, 26),   # Shri Ram Navami
        date(2026, 3, 31),   # Shri Mahavir Jayanti
        date(2026, 4, 3),    # Good Friday
        date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
        date(2026, 5, 1),    # Maharashtra Day
        date(2026, 5, 28),   # Bakri Id
        date(2026, 6, 26),   # Muharram
        date(2026, 9, 14),   # Ganesh Chaturthi
        date(2026, 10, 2),   # Mahatma Gandhi Jayanti
        date(2026, 10, 20),  # Dussehra
        date(2026, 11, 10),  # Diwali – Balipratipada
        date(2026, 11, 24),  # Guru Nanak Jayanti
        date(2026, 12, 25),  # Christmas
    },
}

MARKET_STATUS_OPEN               = "OPEN"
MARKET_STATUS_CLOSED_WEEKEND     = "CLOSED_WEEKEND"
MARKET_STATUS_CLOSED_HOLIDAY     = "CLOSED_HOLIDAY"
MARKET_STATUS_CLOSED_BEFORE_OPEN = "CLOSED_BEFORE_OPEN"
MARKET_STATUS_CLOSED_AFTER_HOURS = "CLOSED_AFTER_HOURS"
MARKET_STATUS_UNKNOWN_YEAR       = "CLOSED_UNKNOWN_HOLIDAY_CALENDAR"

MARKET_STATUS_LABELS = {
    MARKET_STATUS_OPEN:               "Market open",
    MARKET_STATUS_CLOSED_WEEKEND:     "Closed for the weekend",
    MARKET_STATUS_CLOSED_HOLIDAY:     "Closed — NSE trading holiday",
    MARKET_STATUS_CLOSED_BEFORE_OPEN: "Closed — before market open (09:15 IST)",
    MARKET_STATUS_CLOSED_AFTER_HOURS: "Closed — after market hours (15:30 IST)",
    MARKET_STATUS_UNKNOWN_YEAR:       "Closed — holiday calendar not maintained for this year, treating as closed to be safe",
}


def is_trading_holiday(d: date) -> bool | None:
    """
    True/False if we have a holiday calendar for d.year, else None
    (caller decides how to treat an unmaintained year — see get_market_status).
    """
    year_holidays = NSE_TRADING_HOLIDAYS.get(d.year)
    if year_holidays is None:
        return None
    return d in year_holidays


def get_market_status(now: datetime | None = None) -> str:
    """
    Single source of truth for "is the market open right now, and if not, why".
    `now` should be timezone-aware; if naive or omitted, IST current time is used.
    """
    if now is None:
        now = datetime.now(IST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)

    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return MARKET_STATUS_CLOSED_WEEKEND

    holiday = is_trading_holiday(now.date())
    if holiday is None:
        # We don't have this year's calendar maintained — fail closed rather
        # than silently telling the poller the market is open on a day we
        # actually have no idea about.
        return MARKET_STATUS_UNKNOWN_YEAR
    if holiday:
        return MARKET_STATUS_CLOSED_HOLIDAY

    open_time  = (MARKET_OPEN_HOUR, MARKET_OPEN_MIN)
    close_time = (MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN)
    now_time   = (now.hour, now.minute)

    if now_time < open_time:
        return MARKET_STATUS_CLOSED_BEFORE_OPEN
    if now_time > close_time:
        return MARKET_STATUS_CLOSED_AFTER_HOURS
    return MARKET_STATUS_OPEN


def is_market_open(now: datetime | None = None) -> bool:
    """Backward-compatible boolean wrapper around get_market_status()."""
    return get_market_status(now) == MARKET_STATUS_OPEN
