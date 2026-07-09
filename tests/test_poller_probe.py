"""Tests for poller._poll_once's handling of non-2xx probe responses.

Regression test for the false-reset bug: a 401/429 response carries none of the
rate-limit headers, and sample_from_headers() has no failure path of its own
(it always returns ok=True) -- so without a status check, a failed probe reads
as a genuine 0% sample and fools ResetNotifier/ApproachingNotifier into firing
a false "limit reset" then a false "approaching" re-alert.

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import httpx  # noqa: E402
import poller  # noqa: E402


def _patch_transport(monkeypatch, handler) -> None:
    class _FakeClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(poller.httpx, "Client", _FakeClient)


def test_poll_once_ok_false_on_401(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "expired"}})

    _patch_transport(monkeypatch, handler)
    sample = poller._poll_once("fake-token")

    assert sample.ok is False
    assert sample.session_pct == 0
    assert sample.weekly_pct == 0


def test_poll_once_ok_false_on_429(monkeypatch):
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    _patch_transport(monkeypatch, handler)
    sample = poller._poll_once("fake-token")

    assert sample.ok is False


def test_poll_once_ok_true_on_200(monkeypatch):
    def handler(request):
        if request.url.path == "/v1/messages":
            return httpx.Response(
                200,
                headers={
                    "anthropic-ratelimit-unified-5h-utilization": "0.5",
                    "anthropic-ratelimit-unified-7d-utilization": "0.3",
                },
                json={"id": "msg_1"},
            )
        return httpx.Response(200, json={})  # USAGE_URL / PROFILE_URL enrichment calls

    _patch_transport(monkeypatch, handler)
    sample = poller._poll_once("fake-token")

    assert sample.ok is True
    assert sample.session_pct == 50
    assert sample.weekly_pct == 30


if __name__ == "__main__":
    import types

    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]

    class _MonkeyPatch:
        def __init__(self):
            self._sets = []

        def setattr(self, obj, name, value):
            self._sets.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._sets):
                setattr(obj, name, old)

    for name, fn in fns:
        mp = _MonkeyPatch()
        try:
            fn(mp)
        finally:
            mp.undo()
        print(f"ok  {name}")
    print(f"\n{len(fns)} passed")
