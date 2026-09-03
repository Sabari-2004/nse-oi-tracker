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
