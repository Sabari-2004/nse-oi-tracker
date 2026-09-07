from pathlib import Path


INDEX = Path(__file__).parent / "static" / "index.html"


def test_trade_history_is_scoped_to_the_current_day():
    source = INDEX.read_text(encoding="utf-8")
    assert "one independent trade list per IST day" in source
    assert "Daily history — resets at midnight IST" in source
    assert "localStorage.getItem(`${STORAGE_KEY}:${today}`)" in source
    assert "Clear all saved trades? This cannot be undone." in source


def test_trade_history_has_all_history_fallback():
    source = INDEX.read_text(encoding="utf-8")
    assert "Array.isArray(parsed) ? parsed : []" in source
    assert "localStorage.setItem(`${STORAGE_KEY}:${today}`, JSON.stringify(this.trades));" in source
