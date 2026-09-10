"""Bounded backfill for public NSE daily bhavcopy archives."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta

from collector.bhavcopy import collect_equity_bhavcopy
from database.repository import SignalRepository
from app.market_calendar import is_trading_holiday
from config.settings import get_settings

logger = logging.getLogger(__name__)


def recent_nse_trading_dates(end_date: date, count: int) -> list[date]:
    """Return at most `count` known weekday NSE dates, newest first."""
    if count <= 0:
        return []
    dates: list[date] = []
    cursor = end_date
    while len(dates) < count:
        holiday = is_trading_holiday(cursor)
        if cursor.weekday() < 5 and holiday is not True:
            # Unknown future years are deliberately omitted: no unverified
            # archives are requested just to satisfy a history count.
            if holiday is not None:
                dates.append(cursor)
        cursor -= timedelta(days=1)
    return dates


def backfill_recent_bhavcopies(
    repository: SignalRepository,
    *,
    end_date: date,
    required_days: int = 60,
    max_downloads: int = 60,
    delay_seconds: float = 0.25,
) -> dict[str, int]:
    """Fetch missing public daily files with a strict download/rate limit."""
    if required_days <= 0 or max_downloads <= 0:
        return {"requested": 0, "downloaded": 0, "stored": 0, "skipped": 0}
    candidates = recent_nse_trading_dates(end_date, required_days)
    existing = repository.daily_equity_trade_dates()
    missing = [candidate for candidate in candidates if candidate.isoformat() not in existing]
    downloaded = stored = 0
    for candidate in missing[:max_downloads]:
        bars = collect_equity_bhavcopy(candidate)
        downloaded += 1
        stored += repository.upsert_daily_equity_bars(bars)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return {
        "requested": len(candidates),
        "downloaded": downloaded,
        "stored": stored,
        "skipped": len(candidates) - len(missing),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill free public NSE EQ bhavcopy bars.")
    parser.add_argument("--days", type=int, default=60, help="Recent NSE trading days required (default: 60)")
    parser.add_argument("--max-downloads", type=int, default=60, help="Strict cap for this run (default: 60)")
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = backfill_recent_bhavcopies(
        SignalRepository(get_settings().database_path),
        end_date=arguments.end_date,
        required_days=arguments.days,
        max_downloads=arguments.max_downloads,
    )
    print(result)


if __name__ == "__main__":
    main()
