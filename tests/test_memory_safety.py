from types import SimpleNamespace

import app.oi_analyzer as oi_analyzer


def test_angel_overlay_is_skipped_when_disabled(monkeypatch):
    row = {
        "underlying": "TEST",
        "ltp": 100,
        "pChange": 3.0,
        "oi": 200_000,
        "oiChangePct": 10.0,
    }
    monkeypatch.setattr(oi_analyzer, "fetch_all_fno_oi_change", lambda: [row])
    monkeypatch.setattr(
        oi_analyzer,
        "get_settings",
        lambda: SimpleNamespace(angel_overlay_enabled=False),
    )
    monkeypatch.setattr(
        type(oi_analyzer.angel_one),
        "configured",
        property(lambda _self: True),
    )

    def fail_if_called(_symbols):
        raise AssertionError("Angel overlay should be skipped")

    monkeypatch.setattr(oi_analyzer.angel_one, "quotes", fail_if_called)
    assert oi_analyzer.scan_all_fno_realtime() == []
    assert oi_analyzer.last_scan_data_status() == "RECEIVED"


def test_health_exposes_memory_rss_number():
    from fastapi.testclient import TestClient
    import app.main as main

    with TestClient(main.app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert isinstance(response.json()["memory_rss_mb"], (int, float))
