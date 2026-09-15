from app.oi_analyzer import last_scan_data_status


def test_signal_api_contract_exposes_upstream_data_status():
    from pathlib import Path
    main = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    index = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()
    assert '"data_status":       data_status' in main
    assert '"NSE upstream returned no OI rows' in main
    assert "dataStatus === 'UNAVAILABLE'" in index
    assert "this.dataStatus" in index


def test_scan_status_is_a_safe_string_contract():
    assert isinstance(last_scan_data_status(), str)
