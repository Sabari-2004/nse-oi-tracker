from analytics.intelligence import build_market_intelligence


def test_market_intelligence_keeps_candidate_and_source_boundaries_visible():
    report = build_market_intelligence(
        {"indices": {"INDIA_VIX": {"last": 14}}, "market_breadth": {"advances": 4}},
        {"regime": "RANGE_BOUND"},
        [{"event_risk": "HIGH_EVENT_RISK", "symbol": "TEST"}],
        [{"signal": "LONG_BUILDUP"}, {"signal": "LONG_BUILDUP"}],
    )
    assert report["candidate_counts"]["LONG_BUILDUP"] == 2
    assert report["high_event_disclosures"][0]["symbol"] == "TEST"
    assert "crude oil" in report["coverage"]["not_inferred"]
    assert "NO_TRADE" in report["caveat"]
