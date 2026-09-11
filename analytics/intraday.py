"""Session VWAP from live NSE observations.

NSE's current live OI endpoint exposes LTP and, for some rows, cumulative
volume, but it does not expose a guaranteed public 5-minute OHLCV feed. This
module therefore computes an explicitly labelled observation VWAP from the
live snapshots the service already receives. It never substitutes a daily
proxy or fabricates bars.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date
from threading import RLock


_MAX_OBSERVATIONS = 480
_lock = RLock()
_sessions: dict[str, tuple[date, float, deque[tuple[float, float]]]] = {}


def observe(symbol: str, price: float, cumulative_volume: float, session_date: date) -> dict:
    symbol = str(symbol or "").upper().strip()
    price = float(price or 0)
    cumulative_volume = float(cumulative_volume or 0)
    if not symbol or price <= 0:
        return {"vwap": None, "available": False, "source": "nse_live_observations"}

    with _lock:
        previous_date, previous_volume, observations = _sessions.get(
            symbol, (session_date, 0.0, deque(maxlen=_MAX_OBSERVATIONS))
        )
        if previous_date != session_date:
            previous_volume = 0.0
            observations = deque(maxlen=_MAX_OBSERVATIONS)
        # Cumulative volume can reset after an upstream refresh. Treat the
        # current observation as a new bucket rather than creating negative VWAP.
        delta_volume = cumulative_volume - previous_volume if cumulative_volume > previous_volume else cumulative_volume
        if delta_volume > 0:
            observations.append((price, delta_volume))
        _sessions[symbol] = (session_date, cumulative_volume, observations)
        total_volume = sum(volume for _, volume in observations)
        vwap = sum(price * volume for price, volume in observations) / total_volume if total_volume else None
        return {
            "vwap": round(vwap, 4) if vwap is not None else None,
            "available": vwap is not None,
            "source": "nse_live_observations",
            "observation_count": len(observations),
            "data_frequency": "scanner_poll_interval",
            "note": "Observation VWAP; not a broker-grade 5-minute OHLCV feed.",
        }


def clear() -> None:
    with _lock:
        _sessions.clear()


def session_vwap(symbol: str) -> float | None:
    symbol = str(symbol or "").upper().strip()
    with _lock:
        record = _sessions.get(symbol)
        if not record:
            return None
        _, _, observations = record
        total_volume = sum(volume for _, volume in observations)
        return sum(price * volume for price, volume in observations) / total_volume if total_volume else None
__all__ = ["observe", "session_vwap", "clear"]
