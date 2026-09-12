import importlib

from app.oi_analyzer import (
    SIGNAL_LONG_BUILDUP,
    SIGNAL_SHORT_BUILDUP,
    SIGNAL_SHORT_COVERING,
    SIGNAL_LONG_UNWINDING,
    SIGNAL_NEUTRAL,
    _parse_row,
    classify_signal,
)


def test_classification_matrix():
    assert classify_signal(1.0, 5.0) == SIGNAL_LONG_BUILDUP
    assert classify_signal(-1.0, 5.0) == SIGNAL_SHORT_BUILDUP
    assert classify_signal(1.0, -5.0) == SIGNAL_SHORT_COVERING
    assert classify_signal(-1.0, -5.0) == SIGNAL_LONG_UNWINDING
    assert classify_signal(0.05, 0.2) == SIGNAL_NEUTRAL


def test_live_nse_field_names_produce_buy_signal():
    result = _parse_row({
        "symbol": "NIFTY",
        "underlyingValue": 23822,
        "latestOI": 8021474,
        "prevOI": 6099628,
        "changeInOI": 1921846,
        "pChange": 0.50,
        "change": 59.5,
    })
    assert result is not None
    assert result["signal"] == SIGNAL_LONG_BUILDUP
    assert result["signal_direction"] == "BUY"
    assert result["oi"] == 8021474
    assert result["oi_change"] == 1921846
    assert result["oi_change_pct"] > 0


def test_live_nse_field_names_produce_sell_signal():
    result = _parse_row({
        "underlying": "BANKNIFTY",
        "underlyingValue": 51000,
        "latestOI": 950000,
        "prevOI": 1000000,
        "changeInOI": -50000,
        "pChange": -0.50,
        "change": -180,
    })
    assert result is not None
    assert result["signal"] == SIGNAL_LONG_UNWINDING
    assert result["signal_direction"] == "SELL"
    assert result["oi_change_pct"] < 0


def test_scan_all_fno_realtime_filters_medium_quality_noise(monkeypatch):
    """Medium rows stay diagnostic and do not become dashboard signal spam."""
    import app.oi_analyzer as oi_analyzer
    oi_analyzer = importlib.reload(oi_analyzer)

    # price 2.0% -> 28pts, oi 7% -> 22pts, oi_abs 100_000 -> 12pts = 62 => MEDIUM
    medium_row = {
        "symbol": "MEDIUMCO", "underlyingValue": 500,
        "pChange": 2.0, "change": 10,
        "oi": 100_000, "oiChange": 6500, "oiChangePct": 7.0,
    }
    monkeypatch.setattr(oi_analyzer, "fetch_all_fno_oi_change", lambda: [medium_row])

    results = oi_analyzer.scan_all_fno_realtime()

    assert results == []


def test_modest_directional_move_is_not_published(monkeypatch):
    import app.oi_analyzer as oi_analyzer
    oi_analyzer = importlib.reload(oi_analyzer)

    # A 0.25% move is routine noise for the live dashboard even with elevated OI.
    row = {
        "symbol": "MODESTCO", "underlyingValue": 500,
        "pChange": 0.25, "change": 1.25,
        "oi": 100_000, "oiChange": 23_000, "oiChangePct": 30.0,
    }
    monkeypatch.setattr(oi_analyzer, "fetch_all_fno_oi_change", lambda: [row])

    results = oi_analyzer.scan_all_fno_realtime()

    assert results == []


def test_quality_gate_requires_high_confidence_for_publication(monkeypatch):
    import app.oi_analyzer as oi_analyzer
    oi_analyzer = importlib.reload(oi_analyzer)
    noisy = _parse_row({
        "symbol": "NOISECO", "underlyingValue": 500,
        "pChange": 0.49, "change": 2.45,
        "oi": 200_000, "oiChange": 20_000, "oiChangePct": 10.0,
    })
    assert noisy is not None
    assert noisy["confidence"] < 75
    quality = _parse_row({
        "symbol": "QUALITYCO", "underlyingValue": 500,
        "pChange": 3.0, "change": 15.0,
        "oi": 200_000, "oiChange": 20_000, "oiChangePct": 10.0,
    })
    assert quality["signal"] == SIGNAL_LONG_BUILDUP
    monkeypatch.setattr(oi_analyzer, "fetch_all_fno_oi_change", lambda: [{}, {}])
    parsed = iter([noisy, quality])
    monkeypatch.setattr(oi_analyzer, "_parse_row", lambda row: next(parsed))
    assert [row["symbol"] for row in oi_analyzer.scan_all_fno_realtime()] == ["QUALITYCO"]


def test_high_score_signal_is_actionable_and_exposes_factor_audit():
    result = _parse_row({
        "symbol": "STRONGCO", "underlyingValue": 500,
        "pChange": 3.0, "change": 15,
        "oi": 1_000_000, "oiChange": 200_000, "oiChangePct": 20.0,
    })
    assert result["score"] == 100
    assert result["actionable"] is True
    assert result["trade_recommendation"] == "TRADE"
    assert result["confirmed_factors"]
    assert result["data_source"] == "NSE live-analysis-oi-spurts-underlyings"


def test_medium_score_signal_remains_no_trade_but_explains_missing_factors():
    result = _parse_row({
        "symbol": "WEAKCO", "underlyingValue": 500,
        "pChange": 0.25, "change": 1.25,
        "oi": 100_000, "oiChange": 23_000, "oiChangePct": 30.0,
    })
    assert result is not None
    assert result["score"] < 75
    assert result["actionable"] is False
    assert result["trade_recommendation"] == "NO_TRADE"
    assert isinstance(result["missing_factors"], list)


def test_angel_one_adapter_is_read_only():
    from pathlib import Path
    source = (Path(__file__).parent / "app" / "angel_one.py").read_text(encoding="utf-8")
    assert "market/v1/quote/" in source
    assert "placeOrder" not in source
    assert "modifyOrder" not in source
    assert "cancelOrder" not in source
