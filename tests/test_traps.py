from analytics.traps import trap_risk


def test_trap_risk_needs_all_daily_context_not_just_a_price_oi_label():
    result = trap_risk({"ltp": 101, "oi_change_pct": -2}, {})
    assert result["risk_label"] == "INSUFFICIENT_DATA"
    assert result["trade_recommendation"] == "NO_TRADE"


def test_bull_trap_needs_three_independent_daily_warning_conditions():
    result = trap_risk(
        {"ltp": 101, "oi_change_pct": -2},
        {"daily_resistance20": 100, "daily_support20": 90, "daily_vwap_proxy20": 102, "relative_volume20": 0.7},
    )
    assert result["risk_label"] == "BULL_TRAP_RISK"
    assert len(result["evidence"]) == 4
