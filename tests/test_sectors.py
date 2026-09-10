from analytics.sectors import attach_sector, known_sectors, sector_for_symbol


def test_sector_taxonomy_is_explicit_about_unknown_symbols():
    assert sector_for_symbol("RELIANCE") == "Energy & Utilities"
    assert sector_for_symbol("not-a-listed-symbol") == "Unclassified"
    assert attach_sector({"symbol": "TCS"})["sector"] == "Information Technology"
    assert "Unclassified" in known_sectors()
