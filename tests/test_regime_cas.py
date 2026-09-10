from analytics.cas import confidence_analysis
from analytics.regime import classify_market_regime


def test_regime_prefers_public_vix_panic_context_over_daily_trend():
    result = classify_market_regime(
        {"regime": "TREND_UP", "atr14_pct": 1.2},
        {"indices": {"INDIA_VIX": {"last": 28}}},
    )
    assert result["regime"] == "PANIC_MODE"
    assert result["india_vix"] == 28.0


def test_cas_exposes_missing_option_inputs_and_avoids_high_event_risk():
    result = confidence_analysis(
        {"regime": "TREND_UP", "atr14_pct": 2, "relative_volume20": 1.5},
        {"event_risk": "HIGH_EVENT_RISK"},
        {"indices": {"INDIA_VIX": {"last": 14}}},
    )
    assert result["recommendation"] == "AVOID_TRADE"
    assert result["expected_daily_move_pct"] is not None
    assert "live option IV" in result["missing_inputs"]
