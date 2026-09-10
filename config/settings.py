"""Validated runtime settings for the NSE OI Tracker service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer; received {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}; received {value}")
    return value


def _csv_values(name: str) -> tuple[str, ...]:
    """Parse an optional comma-separated environment value without wildcards."""
    return tuple(
        value.strip().rstrip("/")
        for value in os.getenv(name, "").split(",")
        if value.strip()
    )


@dataclass(frozen=True, slots=True)
class Settings:
    """Environment-backed operational settings with safe local defaults."""

    data_dir: Path
    database_path: Path
    poll_interval_seconds: int
    cache_ttl_seconds: int
    debug_token: str | None
    history_retention_days: int
    cors_origins: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[1]
        configured_dir = os.getenv("NSE_OI_DATA_DIR", "data")
        data_dir = Path(configured_dir)
        if not data_dir.is_absolute():
            data_dir = project_root / data_dir
        data_dir = data_dir.resolve()
        data_dir.mkdir(parents=True, exist_ok=True)

        database_name = os.getenv("NSE_OI_DATABASE", "nse_oi_tracker.sqlite3")
        database_path = Path(database_name)
        if not database_path.is_absolute():
            database_path = data_dir / database_path

        token = os.getenv("DEBUG_TOKEN", "").strip() or None
        return cls(
            data_dir=data_dir,
            database_path=database_path,
            poll_interval_seconds=_positive_int("NSE_OI_POLL_INTERVAL_SECONDS", 60),
            cache_ttl_seconds=_positive_int("NSE_OI_CACHE_TTL_SECONDS", 60),
            debug_token=token,
            history_retention_days=_positive_int("NSE_OI_HISTORY_RETENTION_DAYS", 30),
            # The bundled dashboard is served from this FastAPI origin and
            # needs no CORS exception. Configure origins only for a separate
            # dashboard host; a blank configuration is never a wildcard.
            cors_origins=_csv_values("NSE_OI_CORS_ORIGINS"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process settings once, keeping imports deterministic."""
    return Settings.from_environment()
