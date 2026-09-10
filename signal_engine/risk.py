"""Conservative, explicit trade-level calculations for recorded signals."""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True, slots=True)
class RiskPlan:
    entry: float
    stop_loss: float
    target_1: float
    target_2: float
    risk_reward: float
    source: str

    def to_dict(self) -> dict[str, float | str]:
        return asdict(self)


_PERCENTAGE_BY_SIGNAL: dict[str, tuple[float, float, float]] = {
    "LONG_BUILDUP": (0.30, 0.50, 1.00),
    "SHORT_BUILDUP": (0.30, 0.50, 1.00),
    "SHORT_COVERING": (0.20, 0.30, 0.60),
    "LONG_UNWINDING": (0.20, 0.30, 0.60),
}


def build_risk_plan(ltp: float, direction: str, signal: str) -> RiskPlan | None:
    """Build an explicit fallback plan when no ATR is available.

    This is deliberately marked as a percentage fallback. Consumers must not
    present it as ATR-derived risk management; it simply preserves the
    existing UI's levels in a durable, auditable server-side record.
    """
    if ltp <= 0 or direction not in {"BUY", "SELL"}:
        return None
    stop_pct, target_one_pct, target_two_pct = _PERCENTAGE_BY_SIGNAL.get(
        signal, (0.30, 0.50, 1.00)
    )
    if direction == "BUY":
        stop_loss = ltp * (1 - stop_pct / 100)
        target_1 = ltp * (1 + target_one_pct / 100)
        target_2 = ltp * (1 + target_two_pct / 100)
    else:
        stop_loss = ltp * (1 + stop_pct / 100)
        target_1 = ltp * (1 - target_one_pct / 100)
        target_2 = ltp * (1 - target_two_pct / 100)
    risk = abs(ltp - stop_loss)
    reward = abs(target_2 - ltp)
    return RiskPlan(
        entry=round(ltp, 2),
        stop_loss=round(stop_loss, 2),
        target_1=round(target_1, 2),
        target_2=round(target_2, 2),
        risk_reward=round(reward / risk, 2) if risk else 0.0,
        source="percentage_fallback_pending_atr",
    )
