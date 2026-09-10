"""Build an auditable public-data market-intelligence briefing."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


def build_market_intelligence(
    overview: Mapping[str, Any] | None,
    regime: Mapping[str, Any] | None,
    announcements: Iterable[Mapping[str, Any]],
    candidates: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize what is known without converting candidates to trades."""
    overview = overview or {}
    regime = regime or {}
    candidates = list(candidates)
    announcements = list(announcements)
    signal_counts = Counter(str(item.get("signal") or "UNKNOWN") for item in candidates)
    high_event = [
        item for item in announcements
        if str(item.get("event_risk") or "") == "HIGH_EVENT_RISK"
    ][:10]
    vix = ((overview.get("indices") or {}).get("INDIA_VIX") or {}).get("last")
    return {
        "market_regime": regime.get("regime", "INSUFFICIENT_DATA"),
        "india_vix": vix,
        "breadth": overview.get("market_breadth") or {},
        "fii_dii_cash_activity": overview.get("fii_dii_cash_activity") or {},
        "candidate_counts": dict(sorted(signal_counts.items())),
        "high_event_disclosures": high_event,
        "headline": (
            f"Public-data context: {regime.get('regime', 'INSUFFICIENT_DATA')}; "
            f"{len(candidates)} current price/OI candidates; {len(high_event)} high event-risk disclosure(s)."
        ),
        "coverage": {
            "public_nse": ["indices", "India VIX", "breadth", "FII/DII cash", "corporate disclosures", "OI candidates"],
            "not_inferred": ["news sentiment", "option IV", "gamma", "crude oil", "USDINR", "GIFT NIFTY"],
        },
        "caveat": "This briefing is public-data context only. OI candidates remain NO_TRADE observations.",
    }
