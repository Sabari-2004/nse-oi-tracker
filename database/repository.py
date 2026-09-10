"""SQLite repository for immutable scan snapshots and today's signal history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from signal_engine.risk import build_risk_plan
from utils.time import as_ist, ist_trade_date


@dataclass(frozen=True, slots=True)
class SnapshotWrite:
    snapshot_id: int
    created: bool
    signal_count: int


class SignalRepository:
    """Own the SQLite schema and all history/result mutations.

    A scan is stored as an immutable snapshot. Signal events are children of a
    snapshot, so repeated same-day occurrences are preserved instead of being
    collapsed to one browser-local record per ticker.
    """

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scan_snapshots (
                    id INTEGER PRIMARY KEY,
                    trade_date TEXT NOT NULL,
                    captured_at_ist TEXT NOT NULL,
                    captured_minute_ist TEXT NOT NULL,
                    source TEXT NOT NULL,
                    is_stale INTEGER NOT NULL DEFAULT 0,
                    fingerprint TEXT NOT NULL,
                    signal_count INTEGER NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(trade_date, captured_minute_ist, fingerprint)
                );

                CREATE TABLE IF NOT EXISTS signal_events (
                    id INTEGER PRIMARY KEY,
                    snapshot_id INTEGER NOT NULL REFERENCES scan_snapshots(id) ON DELETE CASCADE,
                    trade_date TEXT NOT NULL,
                    captured_at_ist TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    confidence INTEGER NOT NULL DEFAULT 0,
                    entry REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    target_1 REAL NOT NULL,
                    target_2 REAL NOT NULL,
                    risk_reward REAL NOT NULL,
                    risk_source TEXT NOT NULL,
                    current_price REAL,
                    exit_price REAL,
                    max_target_hit INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    result TEXT,
                    closed_at_ist TEXT,
                    payload_json TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(snapshot_id, symbol, signal)
                );

                CREATE INDEX IF NOT EXISTS idx_snapshot_trade_date
                    ON scan_snapshots(trade_date, captured_at_ist DESC);
                CREATE INDEX IF NOT EXISTS idx_event_visible_history
                    ON signal_events(trade_date, archived, captured_at_ist DESC);
                CREATE INDEX IF NOT EXISTS idx_event_open_symbol
                    ON signal_events(trade_date, status, symbol);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(signal_events)").fetchall()
            }
            if "max_target_hit" not in columns:
                connection.execute(
                    "ALTER TABLE signal_events ADD COLUMN max_target_hit INTEGER NOT NULL DEFAULT 0"
                )

    @staticmethod
    def _fingerprint(signals: Iterable[dict[str, Any]]) -> str:
        canonical = json.dumps(list(signals), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _event_payload(signal: dict[str, Any], captured_at: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = dict(signal)
        direction = str(payload.get("signal_direction") or "NONE").upper()
        plan = build_risk_plan(
            float(payload.get("ltp") or 0), direction, str(payload.get("signal") or "")
        )
        if plan is None:
            raise ValueError(f"Cannot persist a tradable signal without valid price/direction: {payload!r}")
        plan_data = plan.to_dict()
        entry = float(plan_data["entry"])
        target_1_pct = abs((float(plan_data["target_1"]) - entry) / entry * 100)
        target_2_pct = abs((float(plan_data["target_2"]) - entry) / entry * 100)
        stop_pct = abs((float(plan_data["stop_loss"]) - entry) / entry * 100)
        target_sign = "+" if direction == "BUY" else "-"
        stop_sign = "-" if direction == "BUY" else "+"
        payload.update(
            {
                "tradeDate": ist_trade_date(captured_at),
                "addedAt": captured_at.strftime("%H:%M:%S"),
                "currentLTP": payload.get("ltp"),
                "direction": direction,
                "entry": plan_data["entry"],
                "sl": plan_data["stop_loss"],
                "tg1": plan_data["target_1"],
                "tg2": plan_data["target_2"],
                "tg1Pct": f"{target_sign}{target_1_pct:g}",
                "tg2Pct": f"{target_sign}{target_2_pct:g}",
                "slPct": f"{stop_sign}{stop_pct:g}",
                "risk_reward": plan_data["risk_reward"],
                "risk_source": plan_data["source"],
                "reasons": payload.get("reasons")
                or ["Price/OI classification; technical/regime gates are not yet available"],
            }
        )
        return payload, plan_data

    def record_scan(
        self,
        signals: list[dict[str, Any]],
        captured_at: datetime,
        *,
        source: str = "nse_oi_spurts",
        is_stale: bool = False,
    ) -> SnapshotWrite:
        """Persist one scan atomically, deduplicating identical minute snapshots."""
        captured_at = as_ist(captured_at)
        fingerprint = self._fingerprint(signals)
        trade_date = ist_trade_date(captured_at)
        captured_minute = captured_at.strftime("%Y-%m-%dT%H:%M")

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO scan_snapshots (
                    trade_date, captured_at_ist, captured_minute_ist, source,
                    is_stale, fingerprint, signal_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_date,
                    captured_at.isoformat(),
                    captured_minute,
                    source,
                    int(is_stale),
                    fingerprint,
                    len(signals),
                ),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    """
                    SELECT id, signal_count FROM scan_snapshots
                    WHERE trade_date = ? AND captured_minute_ist = ? AND fingerprint = ?
                    """,
                    (trade_date, captured_minute, fingerprint),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Snapshot deduplication did not return the existing row")
                return SnapshotWrite(snapshot_id=int(row["id"]), created=False, signal_count=int(row["signal_count"]))

            snapshot_id = int(cursor.lastrowid)
            for signal in signals:
                payload, plan = self._event_payload(signal, captured_at)
                connection.execute(
                    """
                    INSERT INTO signal_events (
                        snapshot_id, trade_date, captured_at_ist, symbol, signal, direction,
                        confidence, entry, stop_loss, target_1, target_2, risk_reward,
                        risk_source, current_price, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        trade_date,
                        captured_at.isoformat(),
                        str(payload.get("symbol") or "").upper(),
                        str(payload.get("signal") or "NEUTRAL"),
                        str(payload["direction"]),
                        int(payload.get("confidence") or 0),
                        plan["entry"],
                        plan["stop_loss"],
                        plan["target_1"],
                        plan["target_2"],
                        plan["risk_reward"],
                        plan["source"],
                        float(payload.get("ltp") or 0),
                        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
                    ),
                )
            return SnapshotWrite(snapshot_id=snapshot_id, created=True, signal_count=len(signals))

    def update_open_events(self, signals: Iterable[dict[str, Any]], observed_at: datetime) -> int:
        """Mark target/stop outcomes for visible, same-day open events."""
        prices = {
            str(signal.get("symbol") or "").upper(): float(signal.get("ltp") or 0)
            for signal in signals
            if signal.get("symbol") and float(signal.get("ltp") or 0) > 0
        }
        if not prices:
            return 0
        observed_at = as_ist(observed_at)
        trade_date = ist_trade_date(observed_at)
        updated = 0
        with self._connect() as connection:
            events = connection.execute(
                """
                SELECT id, symbol, direction, stop_loss, target_1, target_2, max_target_hit
                FROM signal_events
                WHERE trade_date = ? AND archived = 0 AND status IN ('OPEN', 'TG1_HIT')
                """,
                (trade_date,),
            ).fetchall()
            for event in events:
                price = prices.get(str(event["symbol"]))
                if price is None:
                    continue
                direction = str(event["direction"])
                if direction == "BUY":
                    target_hit = 2 if price >= event["target_2"] else 1 if price >= event["target_1"] else 0
                    stop_hit = price <= event["stop_loss"]
                else:
                    target_hit = 2 if price <= event["target_2"] else 1 if price <= event["target_1"] else 0
                    stop_hit = price >= event["stop_loss"]
                max_target_hit = max(int(event["max_target_hit"]), target_hit)
                status = "TG2_HIT" if target_hit == 2 else "SL_HIT" if stop_hit else "TG1_HIT" if max_target_hit else "OPEN"
                if status in {"OPEN", "TG1_HIT"}:
                    connection.execute(
                        "UPDATE signal_events SET current_price = ?, max_target_hit = ?, status = ? WHERE id = ?",
                        (price, max_target_hit, status, event["id"]),
                    )
                    continue
                connection.execute(
                    """
                    UPDATE signal_events
                    SET current_price = ?, exit_price = ?, max_target_hit = ?, status = ?, result = ?, closed_at_ist = ?
                    WHERE id = ?
                    """,
                    (price, price, max_target_hit, status, status, observed_at.isoformat(), event["id"]),
                )
                updated += 1
        return updated

    def expire_open_events(self, observed_at: datetime) -> int:
        """Close unresolved current-day records after market hours."""
        observed_at = as_ist(observed_at)
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE signal_events
                SET status = 'EXPIRED', result = 'EXPIRED', exit_price = current_price, closed_at_ist = ?
                WHERE trade_date = ? AND archived = 0 AND status IN ('OPEN', 'TG1_HIT')
                """,
                (observed_at.isoformat(), ist_trade_date(observed_at)),
            )
            return result.rowcount

    def history_for_date(self, trade_date: str, *, limit: int = 1000) -> tuple[list[dict[str, Any]], int]:
        """Return visible events and the total count for one IST trading date."""
        with self._connect() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM signal_events WHERE trade_date = ? AND archived = 0",
                    (trade_date,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT * FROM signal_events
                WHERE trade_date = ? AND archived = 0
                ORDER BY captured_at_ist DESC, id DESC
                LIMIT ?
                """,
                (trade_date, limit),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            payload.update(
                {
                    "id": int(row["id"]),
                    "tradeDate": row["trade_date"],
                    "captured_at_ist": row["captured_at_ist"],
                    "direction": row["direction"],
                    "entry": row["entry"],
                    "sl": row["stop_loss"],
                    "tg1": row["target_1"],
                    "tg2": row["target_2"],
                    "risk_reward": row["risk_reward"],
                    "risk_source": row["risk_source"],
                    "currentLTP": row["current_price"],
                    "exitPrice": row["exit_price"],
                    "max_target_hit": row["max_target_hit"],
                    "status": row["status"],
                    "result": row["result"],
                }
            )
            events.append(payload)
        return events, total

    def performance_for_date(self, trade_date: str) -> dict[str, int | float]:
        """Return compact, server-owned same-day outcome metrics."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, max_target_hit, risk_reward, entry, exit_price, direction
                FROM signal_events WHERE trade_date = ? AND archived = 0
                """,
                (trade_date,),
            ).fetchall()
        counts = {"OPEN": 0, "TG1_HIT": 0, "TG2_HIT": 0, "SL_HIT": 0, "EXPIRED": 0}
        pnl_points = 0.0
        for row in rows:
            status = str(row["status"])
            counts[status] = counts.get(status, 0) + 1
            if row["exit_price"] is not None:
                delta = float(row["exit_price"]) - float(row["entry"])
                pnl_points += delta if row["direction"] == "BUY" else -delta
        open_events = counts["OPEN"] + counts["TG1_HIT"]
        closed = len(rows) - open_events
        tp1_hits = sum(1 for row in rows if int(row["max_target_hit"]) >= 1)
        tp2_hits = sum(1 for row in rows if int(row["max_target_hit"]) >= 2)
        wins = tp1_hits
        return {
            "signals_generated": len(rows),
            "wins": wins,
            "losses": counts["SL_HIT"],
            "tp1_hits": tp1_hits,
            "tp2_hits": tp2_hits,
            "sl_hits": counts["SL_HIT"],
            "open": open_events,
            "expired": counts["EXPIRED"],
            "accuracy": round((wins / closed) * 100, 2) if closed else 0.0,
            "average_rr": round(
                sum(float(row["risk_reward"]) for row in rows) / len(rows), 2
            ) if rows else 0.0,
            "pnl_points": round(pnl_points, 2),
        }

    def archive_previous_history(self, today: str, retention_days: int) -> int:
        """Hide older history from normal endpoints and prune old archives."""
        cutoff = (datetime.fromisoformat(today).date() - timedelta(days=retention_days)).isoformat()
        with self._connect() as connection:
            archived = connection.execute(
                "UPDATE signal_events SET archived = 1 WHERE trade_date < ? AND archived = 0",
                (today,),
            ).rowcount
            connection.execute("UPDATE scan_snapshots SET archived = 1 WHERE trade_date < ?", (today,))
            connection.execute("DELETE FROM signal_events WHERE trade_date < ?", (cutoff,))
            connection.execute("DELETE FROM scan_snapshots WHERE trade_date < ?", (cutoff,))
            return archived

    def vacuum(self) -> None:
        """Compact the local SQLite file during an explicit maintenance action."""
        with self._connect() as connection:
            connection.execute("VACUUM")
