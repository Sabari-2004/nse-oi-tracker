from analytics.intraday import candle_vwap
from integrations.angel_one_market_data import AngelOneMarketData


def test_candle_vwap_uses_true_ohlcv_volume_weighting():
    candles = [
        {"high": 102, "low": 98, "close": 100, "volume": 10},
        {"high": 112, "low": 108, "close": 110, "volume": 30},
    ]
    assert candle_vwap(candles) == 107.5


def test_angel_client_is_read_only():
    public_methods = set(dir(AngelOneMarketData))
    forbidden = {"_".join((verb, "order")) for verb in ("place", "modify", "cancel")}
    assert not public_methods.intersection(forbidden)
