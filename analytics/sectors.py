"""Small, transparent NSE-symbol sector taxonomy used only for UI grouping.

NSE's OI-spurts endpoint does not include a sector field.  This curated map is
therefore deliberately conservative: an unknown symbol is labelled
``Unclassified`` instead of being silently assigned to a sector.  It does not
affect signal scoring or make a market-data claim.
"""

from __future__ import annotations


SECTOR_BY_SYMBOL: dict[str, str] = {
    # Banking and financials
    "AXISBANK": "Financials", "BAJFINANCE": "Financials", "BAJAJFINSV": "Financials",
    "BANKBARODA": "Financials", "CANBK": "Financials", "CHOLAFIN": "Financials",
    "HDFCBANK": "Financials", "HDFCLIFE": "Financials", "ICICIBANK": "Financials",
    "ICICIGI": "Financials", "ICICIPRULI": "Financials", "IDFCFIRSTB": "Financials",
    "INDUSINDBK": "Financials", "JIOFIN": "Financials", "KOTAKBANK": "Financials",
    "LICHSGFIN": "Financials", "LICI": "Financials", "M&MFIN": "Financials",
    "MANAPPURAM": "Financials", "MUTHOOTFIN": "Financials", "PFC": "Financials",
    "PNB": "Financials", "RECLTD": "Financials", "SBICARD": "Financials",
    "SBILIFE": "Financials", "SBIN": "Financials", "SHRIRAMFIN": "Financials",
    # Information technology
    "COFORGE": "Information Technology", "CYIENT": "Information Technology",
    "HCLTECH": "Information Technology", "INFY": "Information Technology",
    "LTIM": "Information Technology", "MPHASIS": "Information Technology",
    "PERSISTENT": "Information Technology", "TCS": "Information Technology",
    "TECHM": "Information Technology", "WIPRO": "Information Technology",
    # Energy, oil, gas and utilities
    "ADANIGREEN": "Energy & Utilities", "ADANIPOWER": "Energy & Utilities",
    "BPCL": "Energy & Utilities", "COALINDIA": "Energy & Utilities",
    "GAIL": "Energy & Utilities", "IOC": "Energy & Utilities", "NTPC": "Energy & Utilities",
    "ONGC": "Energy & Utilities", "OIL": "Energy & Utilities", "POWERGRID": "Energy & Utilities",
    "RELIANCE": "Energy & Utilities", "TATAPOWER": "Energy & Utilities", "TORNTPOWER": "Energy & Utilities",
    # Metals and materials
    "ADANIENT": "Metals & Materials", "HINDALCO": "Metals & Materials",
    "HINDCOPPER": "Metals & Materials", "JSWSTEEL": "Metals & Materials",
    "JINDALSTEL": "Metals & Materials", "NATIONALUM": "Metals & Materials",
    "NMDC": "Metals & Materials", "SAIL": "Metals & Materials", "TATASTEEL": "Metals & Materials",
    "VEDL": "Metals & Materials", "ULTRACEMCO": "Metals & Materials",
    "AMBUJACEM": "Metals & Materials", "DALBHARAT": "Metals & Materials",
    # Auto and ancillary
    "ASHOKLEY": "Automobiles", "BAJAJ-AUTO": "Automobiles", "BHARATFORG": "Automobiles",
    "BOSCHLTD": "Automobiles", "EICHERMOT": "Automobiles", "HEROMOTOCO": "Automobiles",
    "M&M": "Automobiles", "MARUTI": "Automobiles", "MOTHERSON": "Automobiles",
    "SUNDRMFAST": "Automobiles", "TATAMOTORS": "Automobiles", "TVSMOTOR": "Automobiles",
    # Consumer, retail and staples
    "BRITANNIA": "Consumer", "COLPAL": "Consumer", "DABUR": "Consumer",
    "DMART": "Consumer", "GODREJCP": "Consumer", "HINDUNILVR": "Consumer",
    "ITC": "Consumer", "MARICO": "Consumer", "NESTLEIND": "Consumer",
    "PAGEIND": "Consumer", "TITAN": "Consumer", "TRENT": "Consumer",
    "UNITDSPR": "Consumer", "VBL": "Consumer",
    # Health care and pharma
    "ALKEM": "Healthcare", "APOLLOHOSP": "Healthcare", "AUROPHARMA": "Healthcare",
    "BIOCON": "Healthcare", "CIPLA": "Healthcare", "DIVISLAB": "Healthcare",
    "DRREDDY": "Healthcare", "GRANULES": "Healthcare", "IPCALAB": "Healthcare",
    "LAURUSLABS": "Healthcare", "LUPIN": "Healthcare", "SUNPHARMA": "Healthcare",
    "TORNTPHARM": "Healthcare", "ZYDUSLIFE": "Healthcare",
    # Industrials, capital goods and construction
    "ABB": "Industrials", "BEL": "Industrials", "BHEL": "Industrials", "CUMMINSIND": "Industrials",
    "DIXON": "Industrials", "HAL": "Industrials", "IRCTC": "Industrials", "IRFC": "Industrials",
    "L&T": "Industrials", "LT": "Industrials", "RVNL": "Industrials", "SIEMENS": "Industrials",
    "TATACONSUM": "Consumer", "ADANIPORTS": "Industrials", "CONCOR": "Industrials",
    # Telecom, media and technology platforms
    "BHARTIARTL": "Telecom & Media", "IDEA": "Telecom & Media", "INDUSTOWER": "Telecom & Media",
    "NAZARA": "Telecom & Media", "NYKAA": "Consumer", "PAYTM": "Financials",
    # Real estate and chemicals
    "DLF": "Real Estate", "GODREJPROP": "Real Estate", "OBEROIRLTY": "Real Estate",
    "PRESTIGE": "Real Estate", "SUNTECK": "Real Estate", "DEEPAKNTR": "Chemicals",
    "PIDILITIND": "Chemicals", "SRF": "Chemicals", "UPL": "Chemicals",
}


def sector_for_symbol(symbol: object) -> str:
    """Return the curated sector or a visible non-assertive fallback."""
    return SECTOR_BY_SYMBOL.get(str(symbol or "").upper().strip(), "Unclassified")


def attach_sector(candidate: dict) -> dict:
    """Copy a scanner candidate and add a display/filter sector label."""
    return {**candidate, "sector": sector_for_symbol(candidate.get("symbol"))}


def known_sectors() -> list[str]:
    """Stable select-menu values, with unknown data represented explicitly."""
    return sorted(set(SECTOR_BY_SYMBOL.values())) + ["Unclassified"]
