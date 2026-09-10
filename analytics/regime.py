"""Market-regime labels built only from persisted daily data and public VIX."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def india_vix(market_overview: Mapping[str, Any] | None) -> float | None:
    """Extract India VIX from the normalized NSE overview without guessing."""
    index = ((market_overview or {}).get("indices") or {}).get("INDIA_VIX") or {}
    return _number(index.get("last"))


def classify_market_regime(
    technical: Mapping[str, Any] | None,
    market_overview: Mapping[str, Any] | None,
    *,
    is_expiry_session: bool = False,
) -> dict[str, Any]:
    """Return a conservative regime and evidence, never a directional call.

    The daily technical component can distinguish a daily trend/range state.
    Public India VIX can override that state when volatility is exceptional.
    Expiry mode is accepted only as an explicit scheduler/calendar fact; it is
    never inferred from the weekday because NSE expiry conventions change.
    """
    technical = technical or {}
    daily_regime = technical.get("regime")
    vix = india_vix(market_overview)
    factors: list[str] = []
    missing: list[str] = []
    if vix is None:
        missing.append("public India VIX")
    else:
        factors.append(f"India VIX {vix:.2f}")
    if technical.get("atr14_pct") is not None:
        factors.append(f"daily ATR {float(technical['atr14_pct']):.2f}%")
    else:
        missing.append("daily ATR history")

    if vix is not None and vix >= 25:
        regime = "PANIC_MODE"
    elif is_expiry_session:
        regime = "EXPIRY_MODE"
    elif vix is not None and vix >= 18:
        regime = "HIGH_VOLATILITY"
    elif daily_regime in {"TREND_UP", "TREND_DOWN", "RANGE_BOUND", "HIGH_VOLATILITY"}:
        regime = str(daily_regime)
    else:
        regime = "INSUFFICIENT_DATA"
        missing.append("validated daily trend/range context")
    return {
        "regime": regime,
        "india_vix": vix,
        "daily_regime": daily_regime,
        "factors": factors,
        "missing_data": list(dict.fromkeys(missing)),
        "data_frequency": "daily_plus_live_index_context",
        "caveat": "Regime is market context, not a trade instruction.",
    }
