"""Tests for the notify layer: Telegram client, Notifier, allowlist, factory."""

from __future__ import annotations

from src.common.config import load_config
from src.common.secrets import NotificationCredentials
from src.notify.telegram import (
    NullNotifier,
    Notifier,
    TelegramClient,
    build_notifier,
)


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload or {"ok": True, "result": {"message_id": 1}}

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        return FakeResp(self.payload)


def _creds():
    return NotificationCredentials(bot_token="TOK", allowed_chat_ids=(42,))


# --- allowlist ---
def test_allowlist():
    c = _creds()
    assert c.is_allowed(42) and c.is_allowed("42")
    assert not c.is_allowed(7)
    assert not NotificationCredentials(bot_token="T").is_allowed(42)  # empty => deny all


# --- client ---
def test_send_message_builds_inline_keyboard():
    session = FakeSession()
    client = TelegramClient(creds=_creds(), session=session)
    client.send_message(42, "hi", buttons=[[("Yes", "yes"), ("No", "no")]])
    url, payload, _ = session.calls[-1]
    assert url.endswith("/botTOK/sendMessage")
    assert payload["chat_id"] == 42 and payload["text"] == "hi"
    assert payload["reply_markup"]["inline_keyboard"][0][0] == {"text": "Yes", "callback_data": "yes"}


def test_api_raises_on_not_ok():
    session = FakeSession(payload={"ok": False, "description": "boom"})
    client = TelegramClient(creds=_creds(), session=session)
    try:
        client.send_message(42, "hi")
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_get_updates_passes_offset():
    session = FakeSession(payload={"ok": True, "result": []})
    client = TelegramClient(creds=_creds(), session=session)
    client.get_updates(offset=5, timeout=0)
    _, payload, _ = session.calls[-1]
    assert payload["offset"] == 5


# --- notifier ---
class RecordingClient:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send_message(self, chat_id, text, buttons=None):
        if self.fail:
            raise RuntimeError("network down")
        self.sent.append((chat_id, text, buttons))


def test_notifier_respects_event_filter():
    client = RecordingClient()
    n = Notifier(client, chat_ids=[1], enabled=True, events=["halt"])
    n.alert("halt", "stopped")
    n.alert("fill", "ignored")
    assert len(client.sent) == 1 and "HALT" in client.sent[0][1]


def test_notifier_proposal_attaches_buttons():
    client = RecordingClient()
    n = Notifier(client, chat_ids=[1], enabled=True)

    class P:
        id = "NVDA-x"
        def summary(self):
            return "BUY 10 NVDA"

    n.proposal(P())
    _, text, buttons = client.sent[-1]
    assert "PROPOSED" in text
    assert buttons[0][0] == ("✅ Approve", "approve:NVDA-x")


def test_notifier_is_best_effort_on_failure():
    n = Notifier(RecordingClient(fail=True), chat_ids=[1], enabled=True)
    n.alert("halt", "x")          # must not raise


def test_null_notifier_is_noop():
    NullNotifier().alert("halt", "x")
    NullNotifier().proposal(object())


def test_build_notifier_null_when_unconfigured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    assert isinstance(build_notifier(load_config()), NullNotifier)
