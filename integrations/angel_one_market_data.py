"""Read-only Angel One SmartAPI market-data integration.

This module intentionally contains authentication, instrument lookup, quotes,
and candles only. No order, GTT, portfolio-mutation, modify, or cancel API is
implemented or imported.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from threading import RLock
from urllib.parse import quote

import pyotp
from curl_cffi import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://apiconnect.angelone.in"
INSTRUMENT_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"


@dataclass(frozen=True, slots=True)
class AngelInstrument:
    symbol: str
    token: str
    exchange: str
    expiry: str | None = None
    instrument_type: str | None = None


class AngelOneMarketData:
    """Small, thread-safe, read-only SmartAPI client."""

    def __init__(self, *, api_key: str, client_code: str, password: str, totp_secret: str,
                 timeout: float = 15.0) -> None:
        self.api_key = api_key
        self.client_code = client_code
        self.password = password
        self.totp_secret = totp_secret
        self.timeout = timeout
        self._jwt: str | None = None
        self._feed_token: str | None = None
        self._login_at = 0.0
        self._instruments: dict[tuple[str, str], AngelInstrument] = {}
        self._lock = RLock()

    @classmethod
    def from_environment(cls) -> "AngelOneMarketData | None":
        values = {
            "api_key": os.getenv("ANGEL_ONE_API_KEY", "").strip(),
            "client_code": os.getenv("ANGEL_ONE_CLIENT_CODE", "").strip(),
            "password": os.getenv("ANGEL_ONE_PASSWORD", "").strip(),
            "totp_secret": os.getenv("ANGEL_ONE_TOTP_SECRET", "").strip(),
        }
        if not all(values.values()):
            return None
        return cls(**values)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.client_code and self.password and self.totp_secret)

    def _headers(self, *, authenticated: bool = True) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-PrivateKey": self.api_key,
            "X-UserType": "USER",
            "X-SourceID": "WEB",
        }
        if authenticated and self._jwt:
            headers["Authorization"] = f"Bearer {self._jwt}"
        return headers

    def _login(self) -> None:
        with self._lock:
            if self._jwt and time.time() - self._login_at < 8 * 60 * 60:
                return
            response = requests.post(
                f"{BASE_URL}/rest/auth/angelbroking/user/v1/loginByPassword",
                headers=self._headers(authenticated=False),
                json={
                    "clientcode": self.client_code,
                    "password": self.password,
                    "totp": pyotp.TOTP(self.totp_secret).now(),
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("status") or not body.get("data"):
                raise RuntimeError(f"Angel One login failed: {body.get('message', 'unknown error')}")
            self._jwt = str(body["data"].get("jwtToken") or "")
            self._feed_token = str(body["data"].get("feedToken") or "")
            if not self._jwt:
                raise RuntimeError("Angel One login response did not include jwtToken")
            self._login_at = time.time()

    def _get_instruments(self) -> dict[tuple[str, str], AngelInstrument]:
        with self._lock:
            if self._instruments:
                return self._instruments
            response = requests.get(INSTRUMENT_MASTER_URL, timeout=30)
            response.raise_for_status()
            records = response.json()
            result: dict[tuple[str, str], AngelInstrument] = {}
            for row in records:
                exchange = str(row.get("exch_seg") or "").upper()
                symbol = str(row.get("symbol") or "").upper()
                token = str(row.get("token") or "")
                if exchange and symbol and token:
                    result.setdefault((exchange, symbol), AngelInstrument(
                        symbol=symbol,
                        token=token,
                        exchange=exchange,
                        expiry=row.get("expiry"),
                        instrument_type=row.get("instrumenttype"),
                    ))
            self._instruments = result
            return result

    def instrument(self, symbol: str, *, exchange: str = "NSE") -> AngelInstrument | None:
        symbol = symbol.upper().strip()
        instruments = self._get_instruments()
        return instruments.get((exchange.upper(), symbol))

    def full_quotes(self, symbols: list[str], *, exchange: str = "NSE") -> dict[str, dict]:
        """Return read-only full quotes in batches of 50."""
        if not symbols:
            return {}
        self._login()
        instruments = self._get_instruments()
        tokens = [instruments[(exchange.upper(), symbol.upper().strip())].token
                  for symbol in symbols
                  if (exchange.upper(), symbol.upper().strip()) in instruments]
        output: dict[str, dict] = {}
        for offset in range(0, len(tokens), 50):
            batch = tokens[offset:offset + 50]
            response = requests.post(
                f"{BASE_URL}/rest/secure/angelbroking/market/v1/quote/",
                headers=self._headers(),
                json={"mode": "FULL", "exchangeTokens": {exchange.upper(): batch}},
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("status"):
                raise RuntimeError(f"Angel One quote request failed: {body.get('message', 'unknown error')}")
            for row in (body.get("data") or {}).get("fetched", []) or []:
                raw_symbol = str(row.get("tradingSymbol") or row.get("symbol") or "").upper()
                symbol = raw_symbol.removesuffix("-EQ")
                if symbol:
                    output[symbol] = {
                        "ltp": float(row.get("ltp") or 0),
                        "open": float(row.get("open") or 0),
                        "high": float(row.get("high") or 0),
                        "low": float(row.get("low") or 0),
                        "close": float(row.get("close") or 0),
                        "volume": int(float(row.get("tradeVolume") or row.get("volume") or 0)),
                        "oi": int(float(row.get("opnInterest") or row.get("openInterest") or 0)),
                        "change_pct": float(row.get("percentChange") or 0),
                        "source": "angel_one_read_only",
                    }
        return output

    def intraday_candles(self, symbol: str, *, interval: str = "FIVE_MINUTE",
                         exchange: str = "NSE", days: int = 1) -> list[dict]:
        """Fetch read-only intraday candles for a mapped instrument."""
        self._login()
        instrument = self.instrument(symbol, exchange=exchange)
        if not instrument:
            return []
        end = datetime.now().replace(second=0, microsecond=0)
        start = end - timedelta(days=max(1, days))
        response = requests.post(
            f"{BASE_URL}/rest/secure/angelbroking/historical/v1/getCandleData",
            headers=self._headers(),
            json={
                "exchange": exchange.upper(),
                "symboltoken": instrument.token,
                "interval": interval,
                "fromdate": start.strftime("%Y-%m-%d %H:%M"),
                "todate": end.strftime("%Y-%m-%d %H:%M"),
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("status"):
            raise RuntimeError(f"Angel One candle request failed: {body.get('message', 'unknown error')}")
        candles = []
        for row in body.get("data") or []:
            if len(row) < 6:
                continue
            candles.append({
                "time": row[0], "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
                "source": "angel_one_read_only",
            })
        return candles


__all__ = ["AngelInstrument", "AngelOneMarketData"]
