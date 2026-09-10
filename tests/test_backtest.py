from analytics.backtest import summarize_candidate_backtest


def test_candidate_backtest_reports_metrics_and_methodology_boundary():
    result = summarize_candidate_backtest([
        {"captured_at_ist": "2026-09-10T10:00:00+05:30", "signal": "LONG_BUILDUP", "direction": "BUY", "entry": 100, "exit_price": 105, "max_target_hit": 2, "status": "TG2_HIT"},
        {"captured_at_ist": "2026-09-10T11:00:00+05:30", "signal": "SHORT_BUILDUP", "direction": "SELL", "entry": 100, "exit_price": 102, "max_target_hit": 0, "status": "SL_HIT"},
    ])

    assert result["events"] == 2
    assert result["accuracy"] == 50.0
    assert result["pnl_points"] == 3.0
    assert result["max_drawdown_points"] == 2.0
    assert result["per_signal"]["LONG_BUILDUP"]["accuracy"] == 100.0
    assert "not a validated" in result["methodology_caveat"]


def test_candidate_backtest_breaks_results_out_by_sector_and_stock():
    result = summarize_candidate_backtest([{
        "symbol": "SECTORTEST", "sector": "Financials", "signal": "LONG_BUILDUP",
        "captured_at_ist": "2026-01-01T10:00:00+05:30", "status": "TG2_HIT",
        "max_target_hit": 2, "entry": 100, "exit_price": 102, "direction": "BUY",
    }])
    assert result["sector_accuracy"]["Financials"]["accuracy"] == 100.0
    assert result["stock_accuracy"]["SECTORTEST"]["pnl_points"] == 2.0
