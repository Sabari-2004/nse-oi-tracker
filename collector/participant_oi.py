"""Parse the public NSE F&O participant-wise OI end-of-day report."""

from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from typing import Any

from app.nse_fetcher import fetch_participant_oi_report


MEASURES = (
    "Future Index Long", "Future Index Short", "Future Stock Long", "Future Stock Short",
    "Option Index Call Long", "Option Index Put Long", "Option Index Call Short", "Option Index Put Short",
    "Option Stock Call Long", "Option Stock Put Long", "Option Stock Call Short", "Option Stock Put Short",
    "Total Long Contracts", "Total Short Contracts",
)


def _number(value: object) -> int:
    try:
        return int(float(str(value or "0").replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def parse_participant_oi_csv(content: str, report_date: date) -> list[dict[str, Any]]:
    """Parse title-prefixed NSE CSV into normalized participant rows.

    The public report starts with a human-readable title followed by a header;
    locate the header rather than relying on a brittle fixed row offset.
    """
    lines = [line for line in content.splitlines() if line.strip()]
    header_index = next((i for i, line in enumerate(lines) if line.lstrip().startswith("Client Type,")), None)
    if header_index is None:
        return []
    reader = csv.DictReader(StringIO("\n".join(lines[header_index:])))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        participant = str(raw.get("Client Type") or "").strip().upper()
        if participant not in {"CLIENT", "DII", "FII", "PRO", "TOTAL"}:
            continue
        measures = {name: _number(raw.get(name)) for name in MEASURES}
        rows.append({
            "report_date": report_date.isoformat(),
            "participant": participant,
            "measures": measures,
            "net_index_futures": measures["Future Index Long"] - measures["Future Index Short"],
            "net_stock_futures": measures["Future Stock Long"] - measures["Future Stock Short"],
            "source": "NSE F&O participant-wise OI EOD report",
        })
    return rows


def collect_participant_oi(report_date: date) -> list[dict[str, Any]]:
    """Fetch and normalize one report; a missing archive returns no rows."""
    content = fetch_participant_oi_report(report_date)
    return parse_participant_oi_csv(content, report_date) if content else []
