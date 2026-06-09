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
from remote_notify import resolve_url, send_ntfy, send_telegram  # noqa: E402


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

    def post(self, url, content=None, headers=None, json=None):
        _FakeClient.last.update(url=url, content=content, headers=headers, json=json)
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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
