from pathlib import Path


INDEX = Path(__file__).parent / "static" / "index.html"


def test_trade_history_is_not_auto_cleared_by_date():
    source = INDEX.read_text(encoding="utf-8")
    assert "retains the complete trade history" in source
    assert "localStorage.removeItem(STORAGE_KEY);" not in source
    assert "Clear all saved trades? This cannot be undone." in source


def test_trade_history_has_all_history_fallback():
    source = INDEX.read_text(encoding="utf-8")
    assert "localStorage.getItem(STORAGE_KEY)" in source
    assert "Array.isArray(parsed) ? parsed : []" in source
    assert "const migrated = [];" in source
    assert "localStorage.setItem(STORAGE_KEY, JSON.stringify(this.trades));" in source
