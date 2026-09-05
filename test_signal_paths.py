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
        "pChange": 0.25,
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
        "pChange": -0.35,
        "change": -180,
    })
    assert result is not None
    assert result["signal"] == SIGNAL_LONG_UNWINDING
    assert result["signal_direction"] == "SELL"
    assert result["oi_change_pct"] < 0


def test_scan_all_fno_realtime_returns_medium_tier_not_just_high(monkeypatch):
    """
    Regression test: scan_all_fno_realtime()'s own docstring always claimed
    it returns HIGH + MEDIUM confidence signals, but the filter used to
    require confidence_tier == "HIGH" exactly, silently discarding every
    MEDIUM row. On calm trading days this produced zero signals even when
    real (if less extreme) OI activity existed.
    """
    import app.oi_analyzer as oi_analyzer
    oi_analyzer = importlib.reload(oi_analyzer)

    # price 2.0% -> 28pts, oi 7% -> 22pts, oi_abs 100_000 -> 12pts = 62 => MEDIUM (60-74)
    medium_row = {
        "symbol": "MEDIUMCO", "underlyingValue": 500,
        "pChange": 2.0, "change": 10,
        "oi": 100_000, "oiChange": 6500, "oiChangePct": 7.0,
    }
    monkeypatch.setattr(oi_analyzer, "fetch_all_fno_oi_change", lambda: [medium_row])

    results = oi_analyzer.scan_all_fno_realtime()

    assert len(results) == 1
    assert results[0]["symbol"] == "MEDIUMCO"
    assert results[0]["confidence_tier"] == "MEDIUM"
