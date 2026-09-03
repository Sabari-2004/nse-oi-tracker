from datetime import time

from app.oi_analyzer import (
    SIGNAL_CAS_SHORT_COVERING,
    _parse_option_chain,
    detect_cas_jump,
)


def test_detect_cas_jump_requires_window_and_oi_covering():
    assert detect_cas_jump("ATHER", 2.1, time(15, 35), -4.0) is True
    assert detect_cas_jump("ATHER", 2.1, time(15, 45), -4.0) is False
    assert detect_cas_jump("ATHER", 2.1, time(15, 35), -2.9) is False


def test_option_chain_returns_pcr_label_spot_and_intrinsic_max_pain():
    data = {
        "records": {
            "underlyingValue": 100,
            "expiryDates": ["01-Jan-2027"],
            "data": [
                {"strikePrice": 90, "CE": {"openInterest": 100, "lastPrice": 5}, "PE": {"openInterest": 500, "lastPrice": 4}},
                {"strikePrice": 100, "CE": {"openInterest": 100, "lastPrice": 5}, "PE": {"openInterest": 500, "lastPrice": 4}},
                {"strikePrice": 110, "CE": {"openInterest": 100, "lastPrice": 5}, "PE": {"openInterest": 500, "lastPrice": 4}},
            ],
        },
        "filtered": {},
    }
    result = _parse_option_chain(data, "ATHER")
    assert result["atm_strike"] == 100
    assert result["pcr"] == 5.0
    assert result["pcr_label"] == "Bullish"
    assert result["max_pain"] == 110
    assert result["spot_source"] == "records.underlyingValue"


def test_cas_signal_constant_is_available():
    assert SIGNAL_CAS_SHORT_COVERING == "CAS_SHORT_COVERING"


def test_oi_change_fallback_is_used_when_percent_missing():
    from app import oi_analyzer
    row = {
        "symbol": "ATHER", "ltp": 100, "pChange": 2.0,
        "oi": 26000, "oiChange": -1182,
    }
    parsed = oi_analyzer._parse_row(row)
    assert parsed is not None
    assert parsed["oi_change_pct"] == -4.35
    assert oi_analyzer._field_usage["ATHER"]["oi_change_field"] == "oiChange"
