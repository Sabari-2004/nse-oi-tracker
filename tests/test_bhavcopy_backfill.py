from datetime import date

from collector.backfill import backfill_recent_bhavcopies, recent_nse_trading_dates


class FakeRepository:
    def __init__(self):
        self.existing: set[str] = set()
        self.saved: list[dict] = []

    def daily_equity_trade_dates(self):
        return self.existing

    def upsert_daily_equity_bars(self, bars):
        bars = list(bars)
        self.saved.extend(bars)
        self.existing.update(bar["trade_date"] for bar in bars)
        return len(bars)


def test_recent_dates_exclude_weekends_and_known_holidays():
    dates = recent_nse_trading_dates(date(2026, 1, 27), 2)
    assert dates == [date(2026, 1, 27), date(2026, 1, 23)]


def test_backfill_respects_existing_dates_and_download_limit(monkeypatch):
    repository = FakeRepository()
    repository.existing.add("2026-01-27")
    requested: list[date] = []

    def collect(day):
        requested.append(day)
        return [{
            "symbol": "TEST", "trade_date": day.isoformat(), "open": 1,
            "high": 2, "low": 1, "close": 1.5, "volume": 10,
        }]

    monkeypatch.setattr("collector.backfill.collect_equity_bhavcopy", collect)
    result = backfill_recent_bhavcopies(
        repository, end_date=date(2026, 1, 27), required_days=3,
        max_downloads=1, delay_seconds=0,
    )

    assert result == {"requested": 3, "downloaded": 1, "stored": 1, "skipped": 1}
    assert requested == [date(2026, 1, 23)]
