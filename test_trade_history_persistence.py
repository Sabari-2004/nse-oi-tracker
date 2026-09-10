from pathlib import Path


INDEX = Path(__file__).parent / "static" / "index.html"


def test_trade_history_is_scoped_to_the_current_day():
    source = INDEX.read_text(encoding="utf-8")
    assert "one independent trade list per IST day" in source
    assert "Daily history ? resets at midnight IST" in source
    assert "localStorage.getItem(`${STORAGE_KEY}:${today}`)" in source
    assert "Clear all saved trades? This cannot be undone." in source


def test_trade_history_has_all_history_fallback():
    source = INDEX.read_text(encoding="utf-8")
    assert "Array.isArray(parsed) ? parsed : []" in source
    assert "localStorage.setItem(`${STORAGE_KEY}:${today}`, JSON.stringify(this.trades));" in source


def test_frontend_direction_requires_aligned_max_pain_pcr_and_oi_evidence():
    source = INDEX.read_text(encoding="utf-8")
    assert "chainDirection()" in source
    assert "painPct > 0.25" in source
    assert "pcr > 1.3" in source
    assert "oiPct > 0.5" in source
    assert "bull >= 2 && bear === 0" in source
    assert "Informational bias, not a standalone trade signal" in source


def test_trade_save_records_date_and_migrates_only_same_day_legacy_data():
    source = INDEX.read_text(encoding="utf-8")
    assert "tradeDate:        this.todayIST()" in source
    assert "localStorage.getItem(STORAGE_DATE) === today" in source
    assert "Never resurrect an older day's history" in source
    assert "Trade history could not be saved in browser storage" in source


def test_trade_history_has_indexeddb_redundant_backup():
    source = INDEX.read_text(encoding="utf-8")
    assert "indexedDB.open('nse_oi_tracker_history', 1)" in source
    assert "saveTradesToIndexedDB(today, this.trades)" in source
    assert "loadTradesFromIndexedDB()" in source
    assert "!this._dailyStoragePresent" in source


def test_dashboard_prefers_server_owned_history_when_available():
    source = INDEX.read_text(encoding="utf-8")
    assert "fetch('/api/history/today')" in source
    assert "this.historySource = 'server';" in source
    assert "Server history unavailable; using browser fallback" in source
