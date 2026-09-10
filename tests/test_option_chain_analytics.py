from analytics.option_chain import calculate_max_pain, classify_pcr, summarize_oi_levels


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
