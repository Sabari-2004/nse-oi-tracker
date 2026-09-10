from datetime import datetime

from alerts.dispatcher import dispatch_candidate_alert
from database.repository import SignalRepository
from utils.time import IST


class FakeResponse:
    status_code = 200


class FakeSender:
    def __init__(self):
        self.calls = []

    def post(self, url, *, data, headers, timeout):
        self.calls.append((url, data, headers, timeout))
        return FakeResponse()


def test_alerts_are_opt_in_and_candidate_message_is_not_an_order():
    sender = FakeSender()
    assert dispatch_candidate_alert({}, webhook_url=None, ntfy_topic_url=None, sender=sender) == []
    result = dispatch_candidate_alert(
        {"symbol": "RELIANCE", "signal": "LONG_BUILDUP", "confidence": 90},
        webhook_url="https://example.test/hook", ntfy_topic_url="https://ntfy.sh/example", sender=sender,
    )
    assert [item.channel for item in result] == ["webhook", "ntfy"]
    assert all(item.delivered for item in result)
    assert "not a trade instruction" in sender.calls[1][1]


def test_alert_reservation_is_idempotent(tmp_path):
    repository = SignalRepository(tmp_path / "tracker.sqlite3")
    now = datetime(2026, 9, 10, 10, 0, tzinfo=IST)
    assert repository.reserve_alert("same", "webhook", now) is True
    assert repository.reserve_alert("same", "webhook", now) is False
    repository.complete_alert("same", delivered=True, detail="HTTP 200", observed_at=now)


def test_telegram_alert_is_opt_in_and_does_not_put_token_in_payload():
    sender = FakeSender()
    result = dispatch_candidate_alert(
        {"symbol": "RELIANCE"}, webhook_url=None, ntfy_topic_url=None,
        telegram_bot_token="not-a-real-token", telegram_chat_id="123", sender=sender,
    )
    assert result[0].channel == "telegram"
    assert "not-a-real-token" in sender.calls[0][0]
    assert "not-a-real-token" not in sender.calls[0][1]
