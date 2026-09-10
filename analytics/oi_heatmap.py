"""Explainable OI heatmap data, intentionally independent of a chart library."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def option_oi_heatmap(strikes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Normalize CE/PE OI and OI-change intensity for an auditable ladder.

    An intensity is a relative value in the returned chain window, not a price
    forecast.  Consumers can render colour without having to reimplement the
    scaling or rely on a paid charting platform.
    """
    rows = [{
        "strike": _number(row.get("strike")),
        "ce_oi": _number(row.get("ce_oi")), "pe_oi": _number(row.get("pe_oi")),
        "ce_doi": _number(row.get("ce_doi")), "pe_doi": _number(row.get("pe_doi")),
    } for row in strikes]
    rows = sorted((row for row in rows if row["strike"] > 0), key=lambda row: row["strike"])
    largest_oi = max((max(row["ce_oi"], row["pe_oi"]) for row in rows), default=0.0)
    largest_change = max((max(abs(row["ce_doi"]), abs(row["pe_doi"])) for row in rows), default=0.0)
    ladder = [{
        **row,
        "ce_oi_intensity": round(row["ce_oi"] / largest_oi, 4) if largest_oi else 0.0,
        "pe_oi_intensity": round(row["pe_oi"] / largest_oi, 4) if largest_oi else 0.0,
        "ce_doi_intensity": round(abs(row["ce_doi"]) / largest_change, 4) if largest_change else 0.0,
        "pe_doi_intensity": round(abs(row["pe_doi"]) / largest_change, 4) if largest_change else 0.0,
        "net_oi": round(row["pe_oi"] - row["ce_oi"], 2),
        "net_doi": round(row["pe_doi"] - row["ce_doi"], 2),
    } for row in rows]
    return {
        "strikes": ladder,
        "scale": {"max_open_interest": largest_oi, "max_absolute_oi_change": largest_change},
        "methodology_caveat": "Intensity is relative to the returned option-chain strike window; OI zones are contextual analytics, not guaranteed support, resistance, or a trade signal.",
    }
