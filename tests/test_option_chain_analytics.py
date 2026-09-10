from analytics.oi_heatmap import option_oi_heatmap
from analytics.option_chain import calculate_max_pain, classify_pcr, summarize_oi_levels, summarize_pcr_trend


def test_pcr_classification_respects_neutral_boundaries():
    assert classify_pcr(0.69) == "Bearish"
    assert classify_pcr(0.70) == "Neutral"
    assert classify_pcr(1.30) == "Neutral"
    assert classify_pcr(1.31) == "Bullish"


def test_max_pain_is_minimum_aggregate_intrinsic_loss():
    interest = {
        90.0: {"ce_oi": 100.0, "pe_oi": 500.0},
        100.0: {"ce_oi": 100.0, "pe_oi": 500.0},
        110.0: {"ce_oi": 100.0, "pe_oi": 500.0},
    }
    assert calculate_max_pain(interest) == 110.0


def test_oi_levels_return_walls_and_fresh_writing_zones():
    levels = summarize_oi_levels([
        {"strike": 95, "ce_oi": 900, "pe_oi": 1000, "ce_doi": 40, "pe_doi": 0},
        {"strike": 100, "ce_oi": 1000, "pe_oi": 1400, "ce_doi": 100, "pe_doi": 90},
        {"strike": 105, "ce_oi": 1600, "pe_oi": 1200, "ce_doi": 50, "pe_doi": 120},
    ])
    assert levels == {
        "pe_oi_support": 100.0,
        "ce_oi_resistance": 105.0,
        "pe_writing_zone": 105.0,
        "ce_writing_zone": 100.0,
    }


def test_pcr_trend_is_descriptive_and_does_not_assign_a_trade():
    trend = summarize_pcr_trend([
        {"pcr": 0.9, "max_pain": 100},
        {"pcr": 1.0, "max_pain": 105},
    ])
    assert trend == {"samples": 2, "pcr_change": 0.1, "max_pain_change": 5.0, "pcr_state": "RISING"}


def test_oi_heatmap_normalizes_intensity_without_assigning_direction():
    heatmap = option_oi_heatmap([
        {"strike": 100, "ce_oi": 50, "pe_oi": 100, "ce_doi": -2, "pe_doi": 10},
        {"strike": 110, "ce_oi": 200, "pe_oi": 20, "ce_doi": 5, "pe_doi": -1},
    ])
    assert heatmap["strikes"][0]["pe_oi_intensity"] == 0.5
    assert heatmap["strikes"][1]["ce_oi_intensity"] == 1.0
    assert "not guaranteed" in heatmap["methodology_caveat"]
