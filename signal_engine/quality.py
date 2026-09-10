"""Make the boundary between a market observation and a trade explicit."""

from __future__ import annotations

from typing import Any


INDEPENDENT_CONFIRMATIONS = (
    "ATR-based risk",
    "VWAP alignment",
    "EMA20/EMA50 trend",
    "relative volume",
    "market regime",
    "India VIX / event-risk context",
)


def oi_price_candidate(*, signal: str, bias: str, direction: str) -> dict[str, Any]:
    """Mark a price/OI relationship as non-actionable until independently validated."""
    return {
        "classification": "OI_PRICE_CANDIDATE",
        "market_bias": bias,
        "observed_direction": direction,
        "trade_recommendation": "NO_TRADE",
        "actionable": False,
        "validation_status": "MISSING_INDEPENDENT_CONFIRMATIONS",
        "confirmed_factors": ["price change", "open-interest change"],
        "missing_confirmations": list(INDEPENDENT_CONFIRMATIONS),
        "recommendation_reason": (
            f"{signal} is a price/OI observation only; do not treat it as an "
            "option-entry recommendation until independent confirmation is available."
        ),
    }
