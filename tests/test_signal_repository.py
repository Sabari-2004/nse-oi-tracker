from datetime import datetime, timedelta

from database.repository import SignalRepository
from utils.time import IST


def _signal(symbol: str = "RELIANCE", price: float = 100.0) -> dict:
    return {
        "symbol": symbol,
        "signal": "LONG_BUILDUP",
        "signal_direction": "BUY",
        "signal_label": "Long Buildup",
        "signal_emoji": "green",
        "confidence": 82,
        "confidence_tier": "HIGH",
        "ltp": price,
        "price_change_pct": 1.4,
        "oi_change_pct": 9.0,
    }


def test_snapshots_are_deduplicated_and_events_are_server_owned(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    captured_at = datetime(2026, 9, 10, 10, 0, 15, tzinfo=IST)

    first = repository.record_scan([_signal()], captured_at)
    duplicate = repository.record_scan([_signal()], captured_at)

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.snapshot_id == first.snapshot_id

    events, total = repository.history_for_date("2026-09-10")
    assert total == 1
    assert events[0]["symbol"] == "RELIANCE"
    assert events[0]["entry"] == 100.0
    assert events[0]["risk_source"] == "percentage_fallback_pending_atr"


def test_target_progression_and_market_close_are_recorded(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    captured_at = datetime(2026, 9, 10, 10, 0, tzinfo=IST)
    repository.record_scan([_signal("TARGET", 100.0)], captured_at)

    assert repository.update_open_events([_signal("TARGET", 100.6)], captured_at + timedelta(minutes=1)) == 0
    events, _ = repository.history_for_date("2026-09-10")
    assert events[0]["status"] == "TG1_HIT"
    assert events[0]["max_target_hit"] == 1

    assert repository.update_open_events([_signal("TARGET", 101.1)], captured_at + timedelta(minutes=2)) == 1
    events, _ = repository.history_for_date("2026-09-10")
    assert events[0]["status"] == "TG2_HIT"
    assert events[0]["max_target_hit"] == 2

    repository.record_scan([_signal("EXPIRE", 100.0)], captured_at + timedelta(minutes=3))
    assert repository.expire_open_events(captured_at + timedelta(hours=6)) == 1
    events, _ = repository.history_for_date("2026-09-10")
    expiry_event = next(event for event in events if event["symbol"] == "EXPIRE")
    assert expiry_event["status"] == "EXPIRED"
    assert expiry_event["exitPrice"] == 100.0


def test_previous_day_events_are_hidden_after_rollover(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    yesterday = datetime(2026, 9, 10, 10, 0, tzinfo=IST)
    today = yesterday + timedelta(days=1)
    repository.record_scan([_signal()], yesterday)
    repository.record_scan([_signal("TODAY")], today)

    assert repository.archive_previous_history("2026-09-11", retention_days=30) == 1
    old_events, old_total = repository.history_for_date("2026-09-10")
    current_events, current_total = repository.history_for_date("2026-09-11")
    assert old_events == []
    assert old_total == 0
    assert current_total == 1
    assert current_events[0]["symbol"] == "TODAY"
