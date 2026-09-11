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


def apply_daily_technical_context(
    candidate: dict[str, Any], context: dict[str, Any],
) -> dict[str, Any]:
    """Attach daily technical agreement without promoting a trade recommendation.

    Daily bhavcopy data cannot establish intraday VWAP, event risk, or option
    contract suitability. It can still report whether its independently
    calculated trend and relative-volume evidence agrees with the observed
    price/OI direction.
    """
    enriched = dict(candidate)
    direction = str(enriched.get("observed_direction") or enriched.get("signal_direction") or "")
    confirmed = list(enriched.get("confirmed_factors") or [])
    missing = list(enriched.get("missing_confirmations") or [])
    regime = context.get("regime")
    relative_volume = context.get("relative_volume20")
    alignment = "INSUFFICIENT_DAILY_HISTORY"

    if context.get("validation_ready"):
        if context.get("atr14") is not None:
            confirmed.append("daily ATR context")
        if context.get("daily_vwap_proxy20") is not None:
            confirmed.append("daily VWAP proxy context")
        if relative_volume is not None and float(relative_volume) >= 1.2:
            confirmed.append("relative volume")
        if direction == "BUY" and regime == "TREND_UP":
            confirmed.append("EMA20/EMA50 trend")
            alignment = "ALIGNED"
        elif direction == "SELL" and regime == "TREND_DOWN":
            confirmed.append("EMA20/EMA50 trend")
            alignment = "ALIGNED"
        else:
            alignment = "CONFLICTING_OR_RANGE_BOUND"

    # Preserve only genuinely unfulfilled requirements. Daily indicators
    # reduce uncertainty but never substitute for intraday/external evidence.
    if "EMA20/EMA50 trend" in confirmed:
        missing = [item for item in missing if item != "EMA20/EMA50 trend"]
    if "relative volume" in confirmed:
        missing = [item for item in missing if item != "relative volume"]
    enriched.update(
        {
            "technical_context": context,
            "technical_alignment": alignment,
            "confirmed_factors": list(dict.fromkeys(confirmed)),
            "missing_confirmations": missing,
            "trade_recommendation": "NO_TRADE",
            "actionable": False,
        }
    )
    return enriched


def apply_intraday_observation_context(
    candidate: dict[str, Any], context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach live-session VWAP evidence while keeping the safety boundary.

    The current public NSE feed provides snapshots, not guaranteed OHLCV
    candles. Therefore this context is descriptive only and can never make a
    candidate actionable by itself.
    """
    enriched = dict(candidate)
    context = context or {}
    confirmed = list(enriched.get("confirmed_factors") or [])
    missing = list(enriched.get("missing_confirmations") or [])
    direction = str(enriched.get("signal_direction") or enriched.get("observed_direction") or "")
    vwap = context.get("vwap")
    price = float(enriched.get("ltp") or 0)
    aligned = (
        vwap is not None and price > 0 and
        ((direction == "BUY" and price >= float(vwap)) or
         (direction == "SELL" and price <= float(vwap)))
    )
    if aligned:
        confirmed.append("live-session VWAP alignment")
        missing = [item for item in missing if item != "VWAP alignment"]
    elif vwap is not None:
        missing.append("VWAP mismatch")
    else:
        missing.append("live-session VWAP unavailable")
    enriched.update({
        "intraday_context": context,
        "intraday_vwap_alignment": "ALIGNED" if aligned else "UNAVAILABLE_OR_CONFLICTING",
        "confirmed_factors": list(dict.fromkeys(confirmed)),
        "missing_confirmations": list(dict.fromkeys(missing)),
        "trade_recommendation": "NO_TRADE",
        "actionable": False,
    })
    return enriched
