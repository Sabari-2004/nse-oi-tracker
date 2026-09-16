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


def test_offer_requires_a_strong_phrase():
    assert classify_event_risk("gold loan offer press release")[0] != "HIGH_EVENT_RISK"
    assert classify_event_risk("company announces an open offer to shareholders")[0] == "HIGH_EVENT_RISK"
    assert classify_event_risk("offer for sale of shares")[0] == "HIGH_EVENT_RISK"


def test_category_escalation_and_press_release_cap():
    assert classify_event_risk("routine filing", "Scheme of Arrangement")[0] == "MEDIUM_EVENT_RISK"
    assert classify_event_risk("routine filing", "Rights Issue")[0] == "MEDIUM_EVENT_RISK"
    assert classify_event_risk("results press release", "Press Release")[0] == "MEDIUM_EVENT_RISK"
    assert classify_event_risk("open offer press release", "Press Release")[0] == "HIGH_EVENT_RISK"
