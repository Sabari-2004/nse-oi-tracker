from analytics.sources import public_source_inventory


def test_source_inventory_separates_active_and_unconfigured_coverage():
    rows = public_source_inventory()
    assert any(row["source"] == "NSE option chain" and row["status"] == "ACTIVE" for row in rows)
    assert any(row["source"] == "BSE/media/macro feeds" and row["status"] == "NOT_CONFIGURED" for row in rows)
