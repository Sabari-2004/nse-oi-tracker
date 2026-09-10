"""Pure, explainable option-chain calculations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def classify_pcr(pcr: float) -> str:
    """Classify put/call open-interest ratio with the dashboard thresholds."""
    if pcr < 0.7:
        return "Bearish"
    if pcr > 1.3:
        return "Bullish"
    return "Neutral"


def calculate_max_pain(strike_interest: Mapping[float, Mapping[str, float]]) -> float:
    """Return the settlement strike with the lowest aggregate intrinsic loss."""
    if not strike_interest:
        return 0.0
    losses = {
        candidate: sum(
            float(interest.get("ce_oi", 0)) * max(0, candidate - strike)
            + float(interest.get("pe_oi", 0)) * max(0, strike - candidate)
            for strike, interest in strike_interest.items()
        )
        for candidate in strike_interest
    }
    return float(min(losses, key=losses.get))


def _highest_strike(rows: Iterable[Mapping[str, Any]], value_field: str, *, positive_only: bool = False) -> float | None:
    candidates = []
    for row in rows:
        value = float(row.get(value_field, 0) or 0)
        if positive_only and value <= 0:
            continue
        candidates.append((value, float(row.get("strike", 0) or 0)))
    if not candidates:
        return None
    # Use the first NSE-sorted strike when open interest ties. Letting tuple
    # ordering choose the highest strike hides the tie-breaking policy.
    value, strike = max(candidates, key=lambda candidate: candidate[0])
    return strike if value > 0 else None


def summarize_oi_levels(strikes: Iterable[Mapping[str, Any]]) -> dict[str, float | None]:
    """Summarize OI walls and the strongest fresh CE/PE writing additions.

    These are OI-derived zones, not guaranteed support/resistance. The caller
    must label them as contextual analytics rather than standalone trade rules.
    """
    rows = list(strikes)
    return {
        "pe_oi_support": _highest_strike(rows, "pe_oi"),
        "ce_oi_resistance": _highest_strike(rows, "ce_oi"),
        "pe_writing_zone": _highest_strike(rows, "pe_doi", positive_only=True),
        "ce_writing_zone": _highest_strike(rows, "ce_doi", positive_only=True),
    }


def summarize_pcr_trend(snapshots: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Describe a stored PCR/max-pain sequence without assigning a trade side."""
    rows = list(snapshots)
    if not rows:
        return {
            "samples": 0,
            "pcr_change": None,
            "max_pain_change": None,
            "pcr_state": "INSUFFICIENT_HISTORY",
        }
    latest = rows[-1]
    first = rows[0]
    pcr_change = float(latest["pcr"]) - float(first["pcr"])
    pain_change = float(latest["max_pain"]) - float(first["max_pain"])
    return {
        "samples": len(rows),
        "pcr_change": round(pcr_change, 4),
        "max_pain_change": round(pain_change, 4),
        "pcr_state": "RISING" if pcr_change > 0.03 else "FALLING" if pcr_change < -0.03 else "STABLE",
    }
