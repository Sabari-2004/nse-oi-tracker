from analytics.news import classify_event_risk, latest_event_risk, normalize_nse_announcements


def test_nse_disclosures_are_labeled_for_event_risk_not_sentiment():
    rows = normalize_nse_announcements([{
        "seq_id": "1", "symbol": "RELIANCE", "an_dt": "10-Sep-2026 12:00:00",
        "desc": "Board Meeting", "attchmntText": "Company has informed about Board Meeting for Results.",
    }])
    assert rows[0]["event_risk"] == "HIGH_EVENT_RISK"
    assert "results" in rows[0]["risk_terms"]
    assert latest_event_risk(rows, "RELIANCE")["announcement"]["symbol"] == "RELIANCE"
    assert classify_event_risk("routine disclosure")[0] == "DISCLOSURE"
