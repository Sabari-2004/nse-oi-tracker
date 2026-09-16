"""Conservative event-risk labels for public NSE corporate announcements."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any


HIGH_RISK_TERMS = (
    "results", "board meeting", "buyback", "merger", "amalgamation",
    "acquisition", "delisting", "insolvency", "resignation",
    "fraud", "litigation", "dividend", "split", "bonus",
)
HIGH_RISK_PHRASES = ("open offer", "offer for sale", "buyback offer")
MEDIUM_CATEGORY_TERMS = (
    "scheme of arrangement", "acquisition", "delisting", "rights issue",
    "covenant", "debt",
)
CAPPED_CATEGORIES = ("press release", "analysts", "institutional investor meet")
MEDIUM_RISK_TERMS = (
    "allotment", "order", "contract", "agreement", "investor",
    "fund raise", "preferential", "credit rating", "clarification",
)


def classify_event_risk(text: str, category: str = "") -> tuple[str, list[str]]:
    """Classify disclosure volatility risk, never news sentiment/direction."""
    lowered = text.lower()
    category_lower = category.lower()
    strong = [phrase for phrase in HIGH_RISK_PHRASES if phrase in lowered]
    high = [term for term in HIGH_RISK_TERMS if term in lowered]
    if strong:
        return "HIGH_EVENT_RISK", strong + [term for term in high if term not in strong]
    if high and not any(category_term in category_lower for category_term in CAPPED_CATEGORIES):
        return "HIGH_EVENT_RISK", high
    medium = [term for term in MEDIUM_RISK_TERMS if term in lowered]
    medium += [term for term in MEDIUM_CATEGORY_TERMS if term in category_lower and term not in medium]
    if medium or high:
        return "MEDIUM_EVENT_RISK", medium or high
    return "DISCLOSURE", []


def normalize_nse_announcements(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize only the fields required for auditable NSE-disclosure context."""
    normalized: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        identifier = str(row.get("seq_id") or row.get("dt") or "").strip()
        title = str(row.get("attchmntText") or row.get("desc") or "").strip()
        published = str(row.get("an_dt") or row.get("exchdisstime") or "").strip()
        if not symbol or not identifier or not title:
            continue
        risk, terms = classify_event_risk(f"{row.get('desc') or ''} {title}", str(row.get("desc") or ""))
        normalized.append({
            "announcement_id": identifier,
            "symbol": symbol,
            "published_at": published,
            "category": str(row.get("desc") or "Disclosure").strip(),
            "title": title,
            "attachment_url": str(row.get("attchmntFile") or "").strip() or None,
            "event_risk": risk,
            "risk_terms": terms,
        })
    return normalized


def latest_event_risk(announcements: Iterable[Mapping[str, Any]], symbol: str) -> dict[str, Any]:
    """Return the latest disclosure for a symbol, without inventing sentiment."""
    matches = [item for item in announcements if str(item.get("symbol") or "").upper() == symbol.upper()]
    if not matches:
        return {"event_risk": "NO_RECENT_NSE_DISCLOSURE", "announcement": None}
    latest = max(matches, key=lambda item: str(item.get("published_at") or ""))
    return {"event_risk": latest.get("event_risk"), "announcement": latest}
