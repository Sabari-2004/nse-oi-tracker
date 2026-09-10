# market_calendar.py ? NSE trading-holiday awareness.
#
# is_market_open() previously only checked weekday + clock window, so an
# exchange holiday falling Mon?Fri was reported as "market open" and the
# poller would happily scan a dead market. This module fixes that and gives
# callers a specific reason (weekend / holiday / outside hours / open)
# instead of a bare boolean, so "no signals" and "market's shut" no longer
# look identical to the end user.
#
# IMPORTANT ? MAINTENANCE: NSE publishes this list once a year (usually
# December for the following year). Update NSE_TRADING_HOLIDAYS every
# December or this will silently go stale, the same way the old
# weekday-only check silently went stale the day it was written.
# Verify against the official NSE circular, not just this file, before
# trusting it blindly: https://www.nseindia.com/resources/exchange-communication-holidays

from __future__ import annotations
from datetime import date, datetime
import logging
from zoneinfo import ZoneInfo

from app.config import (
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
)

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

# Full-day NSE equity & equity-derivatives trading holidays that fall on a
# weekday (weekend holidays are irrelevant to this check and omitted).
# Source: NSE's F&O holiday circular.  The runtime refresh below uses NSE's
# public holiday-master API, while this list remains the fail-safe fallback.
NSE_TRADING_HOLIDAYS: dict[int, set[date]] = {
    2026: {
        date(2026, 1, 15),   # Municipal Corporation General Election
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
        date(2026, 11, 10),  # Diwali ? Balipratipada
        date(2026, 11, 24),  # Guru Nanak Jayanti
        date(2026, 12, 25),  # Christmas
    },
}

_holiday_calendar_source = "bundled"
_holiday_calendar_refreshed_at: datetime | None = None


def has_holiday_calendar_for_year(year: int) -> bool:
    """Whether a calendar for `year` is loaded (bundled or refreshed)."""
    return year in NSE_TRADING_HOLIDAYS


def refresh_holiday_calendar() -> dict[str, object]:
    """Load the public NSE F&O calendar without removing the local fallback.

    A failed network request deliberately leaves the bundled calendar intact.
    The NSE client is imported lazily so the pure calendar module remains easy
    to use in tests and does not create an HTTP session at import time.
    """
    global _holiday_calendar_source, _holiday_calendar_refreshed_at
    try:
        from app.nse_fetcher import fetch_fno_holiday_calendar

        fetched = fetch_fno_holiday_calendar()
    except Exception:
        logger.exception("Could not refresh the NSE F&O holiday calendar")
        return {
            "updated": False,
            "source": _holiday_calendar_source,
            "refreshed_at": _holiday_calendar_refreshed_at,
            "years": sorted(NSE_TRADING_HOLIDAYS),
        }

    if not fetched:
        logger.warning("NSE F&O holiday calendar refresh returned no dates")
        return {
            "updated": False,
            "source": _holiday_calendar_source,
            "refreshed_at": _holiday_calendar_refreshed_at,
            "years": sorted(NSE_TRADING_HOLIDAYS),
        }

    for year, holidays in fetched.items():
        NSE_TRADING_HOLIDAYS[year] = set(holidays)
    _holiday_calendar_source = "nse_holiday_master"
    _holiday_calendar_refreshed_at = datetime.now(IST)
    return {
        "updated": True,
        "source": _holiday_calendar_source,
        "refreshed_at": _holiday_calendar_refreshed_at,
        "years": sorted(fetched),
    }


def holiday_calendar_metadata() -> dict[str, object]:
    """Small diagnostics payload safe to expose from the health endpoint."""
    return {
        "source": _holiday_calendar_source,
        "refreshed_at_ist": (
            _holiday_calendar_refreshed_at.isoformat()
            if _holiday_calendar_refreshed_at else None
        ),
        "loaded_years": sorted(NSE_TRADING_HOLIDAYS),
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
    MARKET_STATUS_CLOSED_HOLIDAY:     "Closed ? NSE trading holiday",
    MARKET_STATUS_CLOSED_BEFORE_OPEN: "Closed ? before market open (09:15 IST)",
    MARKET_STATUS_CLOSED_AFTER_HOURS: "Closed ? after market hours (15:30 IST)",
    MARKET_STATUS_UNKNOWN_YEAR:       "Closed ? holiday calendar not maintained for this year, treating as closed to be safe",
}


def is_trading_holiday(d: date) -> bool | None:
    """
    True/False if we have a holiday calendar for d.year, else None
    (caller decides how to treat an unmaintained year ? see get_market_status).
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
        # We don't have this year's calendar maintained ? fail closed rather
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
