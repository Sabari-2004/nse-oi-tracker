from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
MAIN = (ROOT / "app" / "main.py").read_text(encoding="utf-8")


def test_manual_refresh_bypasses_signal_market_and_news_caches():
    assert "this.fetchSignals(true)" in INDEX
    assert "this.fetchMarketOverview(true)" in INDEX
    assert "this.fetchNews(true)" in INDEX
    assert "newsRefreshTimer = setInterval(() => this.fetchNews(true), 180000)" in INDEX


def test_news_endpoint_supports_explicit_upstream_refresh():
    assert "async def corporate_news" in MAIN
    assert "if refresh:" in MAIN
    assert "fetch_corporate_announcements" in MAIN
    assert "upsert_corporate_announcements" in MAIN


def test_signal_api_exposes_actual_data_source_status():
    assert '"primary_market_data_source": active_source' in MAIN
    assert '"angel_one_configured": angel_market_data is not None' in MAIN
    assert '"refresh_supported": True' in MAIN
    assert 'realtime_source' in MAIN


def test_startup_warns_when_bhavcopy_history_is_empty():
    assert "Daily bhavcopy history is empty" in MAIN
    assert "python -m collector.backfill --days 60 --max-downloads 60" in MAIN
