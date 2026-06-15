"""Unit tests for the ntfy phone-push helper (URL building + send wiring).

Run with `python -m pytest tests/ -q`, or directly:
`python tests/test_remote_notify.py`. No real network is touched — send_ntfy's
httpx call is exercised via a tiny fake injected into sys.modules.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import remote_notify  # noqa: E402
from remote_notify import (  # noqa: E402
    resolve_url,
    send_discord,
    send_gotify,
    send_ntfy,
    send_pushover,
    send_slack,
    send_telegram,
    send_webhook,
)


def test_bare_topic_uses_default_server():
    assert resolve_url("clawd-nick-7f3a") == "https://ntfy.sh/clawd-nick-7f3a"


def test_topic_strips_stray_slashes():
    assert resolve_url("/abc/", server="https://ntfy.sh/") == "https://ntfy.sh/abc"


def test_full_url_passed_through():
    assert resolve_url("https://ntfy.example.com/t") == "https://ntfy.example.com/t"
    assert resolve_url("http://box.local/t/") == "http://box.local/t"


def test_empty_topic_raises():
    for bad in ("", "   ", None):
        try:
            resolve_url(bad)  # type: ignore[arg-type]
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_send_empty_topic_returns_error_not_raise():
    ok, msg = send_ntfy("", "t", "b")
    assert not ok and "empty" in msg


class _FakeResp:
    def __init__(self, raise_exc=None):
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc


class _FakeClient:
    """Records the single POST so we can assert URL/content/headers."""

    last = {}

    def __init__(self, timeout=None):
        _FakeClient.last["timeout"] = timeout

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, content=None, headers=None, json=None, data=None):
        _FakeClient.last.update(url=url, content=content, headers=headers,
                                json=json, data=data)
        return _FakeResp(_FakeClient.last.get("raise_exc"))


def _install_fake_httpx(raise_exc=None):
    """Inject a stand-in httpx module; send_ntfy imports httpx lazily."""
    fake = types.ModuleType("httpx")
    fake.HTTPError = Exception
    _FakeClient.last = {"raise_exc": raise_exc}
    fake.Client = _FakeClient
    sys.modules["httpx"] = fake
    return fake


def test_send_posts_expected_request():
    _install_fake_httpx()
    ok, msg = send_ntfy("topic1", "Claude limit reset", "Session limit has reset.")
    assert ok and msg == "sent"
    sent = _FakeClient.last
    assert sent["url"] == "https://ntfy.sh/topic1"
    assert sent["content"] == b"Session limit has reset."
    assert sent["headers"]["Title"] == "Claude limit reset"
    assert sent["headers"]["Tags"] == "bell"


def test_send_reports_http_error():
    _install_fake_httpx(raise_exc=Exception("boom"))
    ok, msg = send_ntfy("topic1", "t", "b")
    assert not ok and "ntfy push failed" in msg


def test_send_reports_non_http_error():
    # e.g. httpx.InvalidURL is NOT an HTTPError subclass — a malformed topic
    # must still be reported, never raised into the caller's thread.
    class _HTTPError(Exception):
        pass

    fake = _install_fake_httpx(raise_exc=ValueError("invalid url"))
    fake.HTTPError = _HTTPError  # ValueError is outside this hierarchy
    ok, msg = send_ntfy("bad topic!", "t", "b")
    assert not ok and "ntfy push failed" in msg


def test_telegram_requires_token_and_chat():
    for token, chat in (("", "123"), ("abc", ""), ("", "")):
        ok, msg = send_telegram(token, chat, "t", "b")
        assert not ok and "required" in msg


def test_telegram_posts_expected_request():
    _install_fake_httpx()
    ok, msg = send_telegram("BOTTOKEN", "98765", "Claude limit reset", "Resume now.")
    assert ok and msg == "sent"
    sent = _FakeClient.last
    assert sent["url"] == "https://api.telegram.org/botBOTTOKEN/sendMessage"
    assert sent["json"] == {"chat_id": "98765", "text": "Claude limit reset\nResume now."}


def test_telegram_reports_error():
    _install_fake_httpx(raise_exc=ValueError("boom"))
    ok, msg = send_telegram("BOTTOKEN", "98765", "t", "b")
    assert not ok and "Telegram push failed" in msg


def test_discord_requires_url():
    for bad in ("", "   ", None):
        ok, msg = send_discord(bad, "t", "b")  # type: ignore[arg-type]
        assert not ok and "empty" in msg


def test_discord_rejects_non_url():
    ok, msg = send_discord("not-a-url", "t", "b")
    assert not ok and "https" in msg


def test_discord_posts_expected_request():
    _install_fake_httpx()
    ok, msg = send_discord(
        "https://discord.com/api/webhooks/1/abc", "Claude limit reset", "Resume now."
    )
    assert ok and msg == "sent"
    sent = _FakeClient.last
    assert sent["url"] == "https://discord.com/api/webhooks/1/abc"
    assert sent["json"] == {"content": "**Claude limit reset**\nResume now."}


def test_discord_reports_error():
    _install_fake_httpx(raise_exc=ValueError("boom"))
    ok, msg = send_discord("https://discord.com/api/webhooks/1/abc", "t", "b")
    assert not ok and "Discord push failed" in msg


def test_slack_posts_expected_request():
    _install_fake_httpx()
    ok, msg = send_slack("https://hooks.slack.com/services/x", "Claude limit reset",
                         "Resume now.")
    assert ok and msg == "sent"
    assert _FakeClient.last["url"] == "https://hooks.slack.com/services/x"
    assert _FakeClient.last["json"] == {"text": "*Claude limit reset*\nResume now."}


def test_slack_rejects_empty_and_non_url():
    ok, msg = send_slack("", "t", "b")
    assert not ok and "empty" in msg
    ok, msg = send_slack("not-a-url", "t", "b")
    assert not ok and "https" in msg


def test_webhook_posts_expected_json():
    _install_fake_httpx()
    ok, msg = send_webhook("https://example.com/hook", "T", "B")
    assert ok and _FakeClient.last["json"] == {"title": "T", "body": "B", "app": "Clawdmeter"}


def test_webhook_rejects_empty():
    ok, msg = send_webhook("", "t", "b")
    assert not ok and "empty" in msg


def test_pushover_posts_expected_request():
    _install_fake_httpx()
    ok, msg = send_pushover("APPTOKEN", "USERKEY", "T", "B")
    assert ok and _FakeClient.last["url"] == "https://api.pushover.net/1/messages.json"
    assert _FakeClient.last["data"] == {
        "token": "APPTOKEN", "user": "USERKEY", "title": "T", "message": "B"}


def test_pushover_requires_token_and_user():
    for tok, usr in (("", "u"), ("t", ""), ("", "")):
        ok, msg = send_pushover(tok, usr, "t", "b")
        assert not ok and "required" in msg


def test_gotify_posts_expected_request():
    _install_fake_httpx()
    ok, msg = send_gotify("https://gotify.example.com/", "APPTOKEN", "T", "B")
    assert ok and _FakeClient.last["url"] == "https://gotify.example.com/message"
    assert _FakeClient.last["headers"]["X-Gotify-Key"] == "APPTOKEN"
    assert _FakeClient.last["json"] == {"title": "T", "message": "B", "priority": 5}


def test_gotify_requires_server_and_token():
    ok, msg = send_gotify("", "tok", "t", "b")
    assert not ok and "required" in msg
    ok, msg = send_gotify("not-a-url", "tok", "t", "b")
    assert not ok and "https" in msg


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
