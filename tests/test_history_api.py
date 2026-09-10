from datetime import datetime

from fastapi.testclient import TestClient

import app.main as main
from database.repository import SignalRepository
from utils.time import IST


def test_history_and_analytics_are_served_from_sqlite(monkeypatch, tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    captured_at = datetime.now(IST)
    repository.record_scan(
        [{
            "symbol": "API_TEST",
            "signal": "LONG_BUILDUP",
            "signal_direction": "BUY",
            "confidence": 80,
            "confidence_tier": "HIGH",
            "ltp": 100.0,
        }],
        captured_at,
    )
    monkeypatch.setattr(main, "repository", repository)

    with TestClient(main.app) as client:
        history = client.get("/api/history/today")
        analytics = client.get("/api/analytics/today")
        debug = client.get("/api/debug")

    assert history.status_code == 200
    assert history.json()["total_events"] == 1
    assert history.json()["events"][0]["symbol"] == "API_TEST"
    assert analytics.status_code == 200
    assert analytics.json()["metrics"]["signals_generated"] == 1
    assert debug.status_code == 404
