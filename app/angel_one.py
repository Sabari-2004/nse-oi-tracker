"""Optional Angel One SmartAPI quote source.

The adapter is inert unless all required ANGEL_* environment variables are set.
It never logs credentials and callers can safely fall back to NSE when Angel is
unavailable or a symbol has no token mapping.
"""
from __future__ import annotations

import json
import logging
import os
import time
from threading import RLock

import pyotp
from curl_cffi import requests

logger = logging.getLogger(__name__)
BASE = "https://apiconnect.angelone.in/rest"

class AngelOneSource:
    def __init__(self):
        self.api_key = os.getenv("ANGEL_API_KEY", "")
        self.client_code = os.getenv("ANGEL_CLIENT_CODE", "")
        self.pin = os.getenv("ANGEL_PIN", os.getenv("ANGEL_PASSWORD", ""))
        self.totp_secret = os.getenv("ANGEL_TOTP_SECRET", "")
        self.state = os.getenv("ANGEL_STATE", "live")
        try:
            self.tokens = json.loads(os.getenv("ANGEL_SYMBOL_TOKENS", "{}"))
        except json.JSONDecodeError:
            self.tokens = {}
        self._jwt = ""
        self._expires = 0.0
        self._lock = RLock()
        self._previous_oi: dict[str, float] = {}

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.client_code and self.pin and self.totp_secret and self.tokens)

    def _headers(self, auth: bool = False) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": self.api_key,
        }
        if auth:
            headers["Authorization"] = f"Bearer {self._jwt}"
        return headers

    def _login(self) -> bool:
        if not self.configured:
            return False
        try:
            code = pyotp.TOTP(self.totp_secret).now()
            response = requests.post(
                f"{BASE}/auth/angelbroking/user/v1/loginByPassword",
                headers=self._headers(),
                json={"clientcode": self.client_code, "password": self.pin, "totp": code, "state": self.state},
                timeout=15,
            )
            payload = response.json()
            data = payload.get("data") or {}
            self._jwt = data.get("jwtToken", "")
            self._expires = time.time() + 8 * 3600
            if not payload.get("status") or not self._jwt:
                logger.warning("Angel One login rejected: %s", payload.get("message", "unknown error"))
                return False
            return True
        except Exception as exc:
            logger.warning("Angel One login failed; using fallback: %s", exc)
            return False

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Return FULL quotes keyed by symbol; empty dict means unavailable."""
        if not self.configured:
            return {}
        with self._lock:
            if not self._jwt or time.time() >= self._expires:
                if not self._login():
                    return {}
            exchange_tokens = {"NSE": [str(self.tokens[s]) for s in symbols if s in self.tokens]}
            if not exchange_tokens["NSE"]:
                return {}
            try:
                response = requests.post(
                    f"{BASE}/secure/angelbroking/market/v1/quote/",
                    headers=self._headers(auth=True),
                    json={"mode": "FULL", "exchangeTokens": exchange_tokens},
                    timeout=15,
                )
                payload = response.json()
                fetched = (payload.get("data") or {}).get("fetched", [])
                result = {}
                for item in fetched:
                    symbol = str(item.get("tradingSymbol", "")).upper().removesuffix("-EQ")
                    ltp = float(item.get("ltp") or 0)
                    oi = float(item.get("opnInterest") or item.get("openInterest") or 0)
                    if symbol and ltp:
                        previous = self._previous_oi.get(symbol, oi)
                        oi_change = oi - previous
                        oi_change_pct = (oi_change / previous * 100) if previous else 0
                        self._previous_oi[symbol] = oi
                        result[symbol] = {"ltp": ltp, "oi": oi, "oi_change": oi_change, "oi_change_pct": oi_change_pct}
                return result
            except Exception as exc:
                logger.warning("Angel One quote request failed; using fallback: %s", exc)
                return {}

angel_one = AngelOneSource()
