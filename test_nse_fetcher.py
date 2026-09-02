import importlib



import app.nse_fetcher as nse_fetcher





def test_fetch_all_fno_oi_change_enriches_second_snapshot(monkeypatch):
  
    module = importlib.reload(nse_fetcher)
  
    payloads = iter([
      
        {"data": [{"symbol": "ABC", "underlyingValue": "100", "oi": 1000}]},
      
        {"data": [{"symbol": "ABC", "underlyingValue": "105", "oi": 1000}]},
      
    ])
  
    monkeypatch.setattr(module._nse, "get", lambda *args, **kwargs: next(payloads))
  


    first = module.fetch_all_fno_oi_change()
  
    second = module.fetch_all_fno_oi_change()
  


    assert first[0]["symbol"] == "ABC"
  
    assert "pChange" not in first[0]
  
    assert second[0]["ltp"] == 105.0
  
    assert second[0]["change"] == 5.0
  
    assert second[0]["pChange"] == 5.0
  
    assert second[0]["price_source"] == "rolling_underlying_snapshot"
  




def test_fetch_all_fno_oi_change_preserves_rows_with_invalid_price(monkeypatch):
  
    module = importlib.reload(nse_fetcher)
  
    monkeypatch.setattr(
      
        module._nse,
      
        "get",
      
        lambda *args, **kwargs: {"data": [{"symbol": "ABC", "underlyingValue": "not-a-number"}]},
      
    )
  


    rows = module.fetch_all_fno_oi_change()
  


    assert rows == [{"symbol": "ABC", "underlyingValue": "not-a-number"}]
  
























