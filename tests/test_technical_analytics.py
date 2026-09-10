from analytics.technical import (
    daily_vwap_proxy,
    efficiency_ratio,
    ema,
    relative_volume,
    technical_context,
    wilder_atr,
)


def _candles(count=60):
    return [
        {
            "trade_date": f"2026-01-{index + 1:02d}",
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "volume": 1_000 if index < count - 1 else 2_000,
        }
        for index in range(count)
    ]


def test_indicators_are_calculated_from_chronological_daily_bars():
    candles = _candles()
    assert ema([candle["close"] for candle in candles], 20) is not None
    assert wilder_atr(candles, 14) == 3.0
    assert efficiency_ratio([candle["close"] for candle in candles], 10) == 1.0
    assert daily_vwap_proxy(candles, 20) is not None
    assert relative_volume(candles, 20) == 2.0


def test_context_is_explicit_when_there_is_not_enough_history():
    context = technical_context(_candles(20))
    assert context["validation_ready"] is False
    assert context["ema50"] is None
    assert context["regime"] is None
    assert context["data_frequency"] == "daily"
    assert "not an intraday VWAP" in context["vwap_note"]


def test_context_detects_uptrend_from_sufficient_history():
    context = technical_context(_candles())
    assert context["validation_ready"] is True
    assert context["regime"] == "TREND_UP"
    assert context["relative_volume20"] == 2.0
