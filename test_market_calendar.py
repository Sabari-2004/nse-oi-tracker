from datetime import datetime
from zoneinfo import ZoneInfo

from app.market_calendar import (
    get_market_status,
    is_market_open,
    MARKET_STATUS_OPEN,
    MARKET_STATUS_CLOSED_WEEKEND,
    MARKET_STATUS_CLOSED_HOLIDAY,
    MARKET_STATUS_CLOSED_BEFORE_OPEN,
    MARKET_STATUS_CLOSED_AFTER_HOURS,
    MARKET_STATUS_UNKNOWN_YEAR,
    refresh_holiday_calendar,
)

IST = ZoneInfo("Asia/Kolkata")


def test_weekday_trading_hours_is_open():
    # Wednesday 2026-09-09, 11:00 IST ? ordinary trading day/time
    dt = datetime(2026, 9, 9, 11, 0, tzinfo=IST)
    assert get_market_status(dt) == MARKET_STATUS_OPEN
    assert is_market_open(dt) is True


def test_weekend_is_closed_even_during_trading_hours():
    # Saturday 2026-09-05, 11:00 IST
    dt = datetime(2026, 9, 5, 11, 0, tzinfo=IST)
    assert get_market_status(dt) == MARKET_STATUS_CLOSED_WEEKEND


def test_nse_holiday_on_a_weekday_is_reported_as_closed():
    # Ganesh Chaturthi 2026 ? Monday, would pass the old weekday+clock-only
    # check as "open" since it's a weekday within trading hours. This is
    # exactly the bug this module exists to fix.
    dt = datetime(2026, 9, 14, 11, 0, tzinfo=IST)
    assert get_market_status(dt) == MARKET_STATUS_CLOSED_HOLIDAY
    assert is_market_open(dt) is False


def test_special_fno_holiday_is_reported_as_closed():
    # NSE's Jan 2026 F&O circular added this municipal-election closure.
    dt = datetime(2026, 1, 15, 11, 0, tzinfo=IST)
    assert get_market_status(dt) == MARKET_STATUS_CLOSED_HOLIDAY


def test_runtime_refresh_can_load_a_future_calendar(monkeypatch):
    import app.nse_fetcher as nse_fetcher
    from app import market_calendar

    future_holiday = datetime(2031, 6, 10, tzinfo=IST).date()
    old_calendar = {year: set(days) for year, days in market_calendar.NSE_TRADING_HOLIDAYS.items()}
    monkeypatch.setattr(
        nse_fetcher,
        "fetch_fno_holiday_calendar",
        lambda: {2031: {future_holiday}},
    )
    try:
        result = refresh_holiday_calendar()
        assert result["updated"] is True
        assert get_market_status(datetime(2031, 6, 10, 11, 0, tzinfo=IST)) == MARKET_STATUS_CLOSED_HOLIDAY
    finally:
        market_calendar.NSE_TRADING_HOLIDAYS.clear()
        market_calendar.NSE_TRADING_HOLIDAYS.update(old_calendar)


def test_before_and_after_hours_on_a_trading_day():
    before = datetime(2026, 9, 9, 9, 0, tzinfo=IST)
    after  = datetime(2026, 9, 9, 16, 0, tzinfo=IST)
    assert get_market_status(before) == MARKET_STATUS_CLOSED_BEFORE_OPEN
    assert get_market_status(after)  == MARKET_STATUS_CLOSED_AFTER_HOURS


def test_unmaintained_year_fails_closed_instead_of_assuming_open():
    dt = datetime(2031, 6, 10, 11, 0, tzinfo=IST)  # weekday, trading hours
    assert get_market_status(dt) == MARKET_STATUS_UNKNOWN_YEAR
    assert is_market_open(dt) is False
