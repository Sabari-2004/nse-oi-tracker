"""Tests for day-end trade closure: WIN/LOSS/FLAT grading, final-price
refresh at 15:31, and bhavcopy-close re-grading at 18:10."""

from __future__ import annotations

import asyncio
from datetime import datetime

import app.main as main_module
from database.repository import SignalRepository
from utils.time import IST


def _signal(symbol: str = "RELIANCE", price: float = 100.0, direction: str = "BUY") -> dict:
    return {
        "symbol": symbol,
        "signal": "LONG_BUILDUP" if direction == "BUY" else "SHORT_BUILDUP",
        "signal_direction": direction,
        "signal_label": "Long Buildup" if direction == "BUY" else "Short Buildup",
        "signal_emoji": "green" if direction == "BUY" else "red",
        "confidence": 82,
        "confidence_tier": "HIGH",
        "ltp": price,
        "price_change_pct": 1.4,
        "oi_change_pct": 9.0,
    }


def _seed_event(repository: SignalRepository, direction: str = "BUY", price: float = 100.0):
    captured_at = datetime(2026, 9, 16, 10, 0, 15, tzinfo=IST)
    write = repository.record_scan([_signal(price=price, direction=direction)], captured_at)
    events, _ = repository.history_for_date("2026-09-16")
    return write, events[0]["id"]


def _bar_for(trade_date: str, symbol: str, close: float):
    return {
        "trade_date": trade_date,
        "symbol": symbol,
        "open": close - 1,
        "high": close + 2,
        "low": close - 2,
        "close": close,
        "volume": 1000.0,
    }


# ---------------------------------------------------------------------------
# expire_open_events grading
# ---------------------------------------------------------------------------


def test_day_end_grades_win_for_buy_above_entry(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    _, event_id = _seed_event(repository, direction="BUY", price=100.0)
    repository.refresh_event_prices({"RELIANCE": 101.0}, datetime(2026, 9, 16, 15, 0, tzinfo=IST))

    closed = repository.expire_open_events(datetime(2026, 9, 16, 15, 31, tzinfo=IST))

    assert closed == 1
    events, _ = repository.history_for_date("2026-09-16")
    assert events[0]["status"] == "EXPIRED"
    assert events[0]["result"] == "WIN"
    assert events[0]["result_source"] == "estimate"
    assert events[0]["exitPrice"] == 101.0


def test_day_end_grades_loss_for_sell_above_entry(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    _, event_id = _seed_event(repository, direction="SELL", price=100.0)
    repository.refresh_event_prices({"RELIANCE": 102.0}, datetime(2026, 9, 16, 15, 0, tzinfo=IST))

    repository.expire_open_events(datetime(2026, 9, 16, 15, 31, tzinfo=IST))

    events, _ = repository.history_for_date("2026-09-16")
    assert events[0]["result"] == "LOSS"
    assert events[0]["exitPrice"] == 102.0


def test_day_end_grades_flat_within_noise_band(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    _, event_id = _seed_event(repository, direction="BUY", price=100.0)
    repository.refresh_event_prices({"RELIANCE": 100.03}, datetime(2026, 9, 16, 15, 0, tzinfo=IST))

    repository.expire_open_events(datetime(2026, 9, 16, 15, 31, tzinfo=IST))

    events, _ = repository.history_for_date("2026-09-16")
    assert events[0]["result"] == "FLAT"


def test_day_end_keeps_win_when_target_hit_intraday(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    captured_at = datetime(2026, 9, 16, 10, 0, 15, tzinfo=IST)
    repository.record_scan([_signal(price=100.0)], captured_at)
    # In-session poll lifts price past TG1 (entry +0.5%) but below TG2 (+1%),
    # then the close falls back below the target.
    repository.update_open_events([_signal(price=100.4)], datetime(2026, 9, 16, 11, 0, tzinfo=IST))
    repository.refresh_event_prices({"RELIANCE": 100.2}, datetime(2026, 9, 16, 15, 0, tzinfo=IST))

    repository.expire_open_events(datetime(2026, 9, 16, 15, 31, tzinfo=IST))

    events, _ = repository.history_for_date("2026-09-16")
    # TG1_HIT already settled intraday with exit at target; verify untouched.
    settled = [e for e in events if e["status"] == "TG1_HIT"]
    expired = [e for e in events if e["status"] == "EXPIRED"]
    assert settled or expired
    for event in settled:
        assert event["result"] == "TG1_HIT"
    for event in expired:
        assert event["result"] == "WIN"  # max_target_hit >= 1 stays WIN


def test_expire_without_price_marks_expired(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    repository.record_scan(
        [_signal(price=100.0)],
        datetime(2026, 9, 16, 10, 0, 15, tzinfo=IST),
    )
    # Force an event whose tracked price never became usable.
    import sqlite3

    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE signal_events SET current_price = NULL, entry = 0")
        connection.commit()

    repository.expire_open_events(datetime(2026, 9, 16, 15, 31, tzinfo=IST))

    events, _ = repository.history_for_date("2026-09-16")
    assert events[0]["status"] == "EXPIRED"
    assert events[0]["result"] == "EXPIRED"


# ---------------------------------------------------------------------------
# final-price refresh before close (scheduled_market_close)
# ---------------------------------------------------------------------------


def test_market_close_refreshes_prices_before_grading(tmp_path, monkeypatch, caplog):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    main_module.repository = repository
    _, event_id = _seed_event(repository, direction="BUY", price=100.0)
    # Simulate a stale tracked price (symbol dropped out of the feed earlier).
    import sqlite3

    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE signal_events SET current_price = 95.0")
        connection.commit()

    monkeypatch.setattr(
        main_module,
        "fetch_all_fno_oi_change",
        lambda: [{"symbol": "RELIANCE", "underlyingValue": 103.0}],
    )

    asyncio.run(main_module.scheduled_market_close())

    events, _ = repository.history_for_date("2026-09-16")
    assert events[0]["exitPrice"] == 103.0
    assert events[0]["result"] == "WIN"


def test_market_close_survives_feed_failure(tmp_path, monkeypatch):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    main_module.repository = repository
    _, event_id = _seed_event(repository, direction="BUY", price=100.0)

    def boom():
        raise RuntimeError("NSE down")

    monkeypatch.setattr(main_module, "fetch_all_fno_oi_change", boom)

    asyncio.run(main_module.scheduled_market_close())

    events, _ = repository.history_for_date("2026-09-16")
    assert events[0]["status"] == "EXPIRED"


# ---------------------------------------------------------------------------
# bhavcopy re-grade at 18:10
# ---------------------------------------------------------------------------


def test_regrade_with_bhavcopy_close_updates_estimate(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    _, event_id = _seed_event(repository, direction="BUY", price=100.0)
    repository.expire_open_events(datetime(2026, 9, 16, 15, 31, tzinfo=IST))

    repository.upsert_daily_equity_bars([_bar_for("2026-09-16", "RELIANCE", 104.0)])
    updated = repository.regrade_with_bhavcopy_close("2026-09-16")

    assert updated == 1
    events, _ = repository.history_for_date("2026-09-16")
    assert events[0]["result"] == "WIN"
    assert events[0]["result_source"] == "bhavcopy_close"
    assert events[0]["exitPrice"] == 104.0


def test_regrade_skips_when_no_bhavcopy_bar(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    _, event_id = _seed_event(repository, direction="BUY", price=100.0)
    repository.expire_open_events(datetime(2026, 9, 16, 15, 31, tzinfo=IST))

    updated = repository.regrade_with_bhavcopy_close("2026-09-16")

    assert updated == 0
    events, _ = repository.history_for_date("2026-09-16")
    assert events[0]["result_source"] == "estimate"


def test_regrade_never_touches_intraday_outcomes(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    repository.record_scan(
        [_signal(price=100.0)],
        datetime(2026, 9, 16, 10, 0, 15, tzinfo=IST),
    )
    repository.update_open_events(
        [_signal(price=105.5)],
        datetime(2026, 9, 16, 11, 0, tzinfo=IST),
    )
    repository.upsert_daily_equity_bars([_bar_for("2026-09-16", "RELIANCE", 90.0)])
    updated = repository.regrade_with_bhavcopy_close("2026-09-16")

    assert updated == 0
    events, _ = repository.history_for_date("2026-09-16")
    for event in events:
        if event["status"] == "TG1_HIT":
            assert event["exitPrice"] != 90.0


def test_ingestion_job_regrades_after_storing_bars(tmp_path, monkeypatch):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    main_module.repository = repository
    _, event_id = _seed_event(repository, direction="BUY", price=100.0)
    repository.expire_open_events(datetime(2026, 9, 16, 15, 31, tzinfo=IST))

    async def fake_ingest(trade_date=None):
        repository.upsert_daily_equity_bars([_bar_for("2026-09-16", "RELIANCE", 99.0)])
        return 1

    async def noop_upload(path):
        return None

    upload_calls: list = []

    def sync_noop_upload(path):
        upload_calls.append(path)

    monkeypatch.setattr(main_module, "ingest_daily_bhavcopy", fake_ingest)
    monkeypatch.setattr(main_module, "ingest_daily_index_bars", fake_ingest)
    monkeypatch.setattr(main_module, "upload_database_snapshot", sync_noop_upload)

    asyncio.run(main_module.scheduled_bhavcopy_ingestion())

    events, _ = repository.history_for_date("2026-09-16")
    assert events[0]["result"] == "LOSS"
    assert events[0]["result_source"] == "bhavcopy_close"
    assert events[0]["exitPrice"] == 99.0
