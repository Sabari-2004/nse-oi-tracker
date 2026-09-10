"""Conservative confidence-analysis (CAS) context for price/OI candidates."""

from __future__ import annotations

from collections.abc import Mapping
from math import sqrt
from typing import Any

from analytics.regime import classify_market_regime, india_vix


def confidence_analysis(
    technical: Mapping[str, Any] | None,
    news_context: Mapping[str, Any] | None,
    market_overview: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Score observable volatility context while leaving option trades unadvised.

    No free, verified source in this app currently supplies live contract IV,
    gamma exposure, intraday VWAP, or a reliable opening-gap series.  They are
    reported as missing instead of being proxied with unrelated data.
    """
    technical = technical or {}
    news_context = news_context or {}
    regime = classify_market_regime(technical, market_overview)
    vix = india_vix(market_overview)
    score = 0
    factors: list[str] = []
    atr_pct = technical.get("atr14_pct")
    relative_volume = technical.get("relative_volume20")
    event_risk = str(news_context.get("event_risk") or "NO_RECENT_NSE_DISCLOSURE")

    if vix is not None:
        score += min(30, max(0, round((vix - 10) * 2)))
        factors.append(f"India VIX {vix:.2f}")
    if atr_pct is not None:
        score += min(25, max(0, round(float(atr_pct) * 6)))
        factors.append(f"daily ATR {float(atr_pct):.2f}%")
    if relative_volume is not None and float(relative_volume) >= 1.2:
        score += min(20, round((float(relative_volume) - 1) * 20))
        factors.append(f"relative daily volume {float(relative_volume):.2f}x")
    if event_risk == "HIGH_EVENT_RISK":
        score += 25
        factors.append("high NSE disclosure event risk")
    elif event_risk == "MEDIUM_EVENT_RISK":
        score += 12
        factors.append("medium NSE disclosure event risk")
    score = min(100, score)

    expected_daily_move_pct = round(vix / sqrt(252), 2) if vix is not None else None
    avoid = event_risk == "HIGH_EVENT_RISK" or regime["regime"] == "PANIC_MODE"
    return {
        "volatility_score": score,
        "expected_daily_move_pct": expected_daily_move_pct,
        "expected_move_method": "India VIX / sqrt(252), annualized-volatility approximation",
        "market_regime": regime,
        "event_risk": event_risk,
        "observed_factors": factors,
        "missing_inputs": ["live option IV", "gamma exposure", "intraday VWAP", "opening gap"],
        "recommendation": "AVOID_TRADE" if avoid else "OBSERVE_ONLY",
        "caveat": "CAS is a volatility-context summary; it does not recommend BUY CE, BUY PE, or a straddle.",
    }
