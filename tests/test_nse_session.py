import json

import app.nse_fetcher as nse_fetcher


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content_type="application/json"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.content = json.dumps(payload).encode() if payload is not None else b""
        self.text = self.content.decode(errors="replace")

    def json(self):
        return json.loads(self.content)


def test_safe_json_rejects_html_even_when_session_is_stale():
    session = nse_fetcher.NSESession()
    response = FakeResponse(content_type="text/html", payload={"blocked": True})
    assert session._safe_json(response, "test-endpoint") is None


def test_get_rebuilds_after_block_response(monkeypatch):
    session = nse_fetcher.NSESession()
    session._sess = type("FakeSession", (), {"headers": {}})()
    monkeypatch.setattr(session, "_ensure", lambda: None)
    rebuilds = []
    monkeypatch.setattr(session, "_build", lambda: rebuilds.append(True) or session._sess)
    monkeypatch.setattr(nse_fetcher.time, "sleep", lambda *_: None)
    responses = iter([FakeResponse(403, {"error": "blocked"}), FakeResponse(200, {"ok": True})])
    monkeypatch.setattr(session, "_api_get", lambda *_: next(responses))

    assert session.get("https://nse.test/api", "https://nse.test/") == {"ok": True}
    assert len(rebuilds) == 1


def test_get_seeded_visits_page_before_api(monkeypatch):
    session = nse_fetcher.NSESession()
    session._sess = type("FakeSession", (), {"headers": {}})()
    monkeypatch.setattr(session, "_ensure", lambda: None)
    monkeypatch.setattr(nse_fetcher.time, "sleep", lambda *_: None)
    calls = []
    monkeypatch.setattr(session, "_nav_get", lambda url, referer: calls.append(("seed", url, referer)) or FakeResponse(200, {}))
    monkeypatch.setattr(session, "_api_get", lambda url, referer: calls.append(("api", url, referer)) or FakeResponse(200, {"data": []}))

    result = session.get_seeded("seed-url", "seed-ref", "api-url", "api-ref")

    assert result == {"data": []}
    assert [call[0] for call in calls] == ["seed", "api"]


def test_public_fetchers_use_seeded_option_chain_and_derivative_paths(monkeypatch):
    seen = []

    def get(url, referer, **kwargs):
        seen.append(("get", url, referer))
        return {"expiryDates": ["01-Jan-2030"]}

    def get_seeded(**kwargs):
        seen.append(("seeded", kwargs))
        return {"data": []}

    monkeypatch.setattr(nse_fetcher._nse, "get", get)
    monkeypatch.setattr(nse_fetcher._nse, "get_seeded", get_seeded)

    assert nse_fetcher.fetch_option_chain_index("NIFTY") == {"data": []}
    assert nse_fetcher.fetch_quote_derivative("A&B") == {"data": []}
    assert seen[0][0] == "get"
    assert any(item[0] == "seeded" and "symbol=A%26B" in item[1]["api_url"] for item in seen)
