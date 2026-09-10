from signal_engine.quality import apply_daily_technical_context, oi_price_candidate


def test_daily_alignment_reduces_missing_factors_without_making_a_trade():
    candidate = oi_price_candidate(signal="LONG_BUILDUP", bias="Bullish", direction="BUY")
    enriched = apply_daily_technical_context(candidate, {
        "validation_ready": True,
        "regime": "TREND_UP",
        "atr14": 10.0,
        "daily_vwap_proxy20": 100.0,
        "relative_volume20": 1.5,
    })

    assert enriched["technical_alignment"] == "ALIGNED"
    assert "EMA20/EMA50 trend" in enriched["confirmed_factors"]
    assert "relative volume" in enriched["confirmed_factors"]
    assert "EMA20/EMA50 trend" not in enriched["missing_confirmations"]
    assert enriched["trade_recommendation"] == "NO_TRADE"
    assert enriched["actionable"] is False


def test_daily_context_does_not_hide_missing_intraday_and_event_checks():
    candidate = oi_price_candidate(signal="SHORT_BUILDUP", bias="Bearish", direction="SELL")
    enriched = apply_daily_technical_context(candidate, {"validation_ready": False})

    assert enriched["technical_alignment"] == "INSUFFICIENT_DAILY_HISTORY"
    assert "VWAP alignment" in enriched["missing_confirmations"]
    assert "India VIX / event-risk context" in enriched["missing_confirmations"]
