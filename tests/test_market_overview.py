from analytics.market_overview import normalize_market_overview


def test_market_overview_normalizes_indices_breadth_vix_and_activity():
    payload = {
        "timestamp": "10-Sep-2026 15:30:00",
        "advances": "24", "declines": "12", "unchanged": "4",
        "data": [
            {"index": "NIFTY 50", "last": "23000", "variation": "20", "percentChange": "0.09"},
            {"index": "INDIA VIX", "last": "12.4", "variation": "-1.2", "percentChange": "-8.8"},
        ],
    }
    result = normalize_market_overview(payload, [
        {"category": "FII/FPI", "date": "10-Sep-2026", "buyValue": "100", "sellValue": "110", "netValue": "-10"},
        {"category": "DII", "date": "10-Sep-2026", "buyValue": "120", "sellValue": "100", "netValue": "20"},
    ])

    assert result["indices"]["NIFTY"]["last"] == 23000.0
    assert result["indices"]["BANKNIFTY"] is None
    assert result["indices"]["INDIA_VIX"]["change_pct"] == -8.8
    assert result["market_breadth"]["advance_decline_ratio"] == 2.0
    assert result["fii_dii_cash_activity"]["FII_FPI"]["net_value_crore"] == -10.0
