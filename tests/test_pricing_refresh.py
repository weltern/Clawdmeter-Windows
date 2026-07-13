"""Unit tests for pricing_refresh: the in-app, no-release-needed pricing sync.

Run with `python -m pytest tests/ -q`. No real network is touched —
`pricing_updater.fetch_rate_card` is always monkeypatched to return the
committed fixture (tests/fixtures/pricing_sample.md), same as test_pricing.py.
Every test resets `pricing`'s override state so it can't leak into other test
files that assert on the bundled map.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import app_settings  # noqa: E402
import pricing  # noqa: E402
import pricing_refresh  # noqa: E402
from pricing import updater as pricing_updater  # noqa: E402


def _mock_httpx_client(handler):
    """A pricing_refresh.httpx.Client stand-in wired to an httpx.MockTransport,
    so fetch_model_registry can be exercised with zero real network access.
    Captures the real httpx.Client before patching -- referring to httpx.Client
    by name inside factory() would recurse into itself once patched in."""
    real_client = httpx.Client

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    return factory

_app = QApplication.instance() or QApplication([])

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pricing_sample.md"


@pytest.fixture(scope="module")
def sample_md() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_override():
    pricing.set_override_path(None)
    yield
    pricing.set_override_path(None)


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch):
    # Default every test to "no OAuth session" so _refresh_once()'s registry
    # step never makes a real network call using this machine's actual Claude
    # credentials. Tests that specifically exercise the registry path override
    # poller.read_token themselves.
    monkeypatch.setattr(pricing_refresh.poller, "read_token", lambda: None)


# --- throttle (_due) --------------------------------------------------------

def test_due_when_never_refreshed(monkeypatch):
    monkeypatch.setattr(app_settings, "get_last_pricing_refresh", lambda: 0.0)
    assert pricing_refresh.PricingRefresher()._due() is True


def test_not_due_within_a_day(monkeypatch):
    monkeypatch.setattr(app_settings, "get_last_pricing_refresh", lambda: time.time())
    assert pricing_refresh.PricingRefresher()._due() is False


# --- _refresh_once -----------------------------------------------------------

def test_refresh_once_writes_cache_and_sets_override(monkeypatch, tmp_path, sample_md):
    cache_file = tmp_path / "price_map.json"
    monkeypatch.setattr(pricing_refresh, "cache_path", lambda: cache_file)
    monkeypatch.setattr(pricing_updater, "fetch_rate_card", lambda: sample_md)
    monkeypatch.setattr(app_settings, "get_last_pricing_refresh", lambda: 0.0)
    stamps = []
    monkeypatch.setattr(app_settings, "set_last_pricing_refresh", stamps.append)

    pricing_refresh.PricingRefresher()._refresh_once()

    assert stamps, "a successful fetch must stamp last_pricing_refresh"
    assert cache_file.exists()
    assert pricing.load_price_map()["models"], "override should now be active"


def test_refresh_once_no_changes_skips_write(monkeypatch, tmp_path, sample_md):
    cache_file = tmp_path / "price_map.json"
    existing_map = pricing_updater.build_price_map(pricing_updater.parse_rate_card(sample_md))
    pricing_updater.write_price_map(existing_map, cache_file)
    mtime_before = cache_file.stat().st_mtime

    monkeypatch.setattr(pricing_refresh, "cache_path", lambda: cache_file)
    monkeypatch.setattr(pricing_updater, "fetch_rate_card", lambda: sample_md)
    monkeypatch.setattr(app_settings, "get_last_pricing_refresh", lambda: 0.0)
    stamps = []
    monkeypatch.setattr(app_settings, "set_last_pricing_refresh", stamps.append)

    pricing_refresh.PricingRefresher()._refresh_once()

    assert stamps, "a successful no-op check still counts as checked today"
    assert cache_file.stat().st_mtime == mtime_before, "identical map must not rewrite the file"


def test_refresh_once_swallows_fetch_error(monkeypatch, tmp_path):
    def _boom():
        raise ConnectionError("offline")

    monkeypatch.setattr(pricing_refresh, "cache_path", lambda: tmp_path / "price_map.json")
    monkeypatch.setattr(pricing_updater, "fetch_rate_card", _boom)
    monkeypatch.setattr(app_settings, "get_last_pricing_refresh", lambda: 0.0)
    stamps = []
    monkeypatch.setattr(app_settings, "set_last_pricing_refresh", stamps.append)

    pricing_refresh.PricingRefresher()._refresh_once()   # must not raise

    assert not stamps, "a failed fetch must retry sooner, not wait a full day"
    assert not (tmp_path / "price_map.json").exists()


def test_refresh_once_respects_throttle(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "get_last_pricing_refresh", lambda: time.time())
    calls = []
    monkeypatch.setattr(pricing_updater, "fetch_rate_card", lambda: calls.append(1))

    pricing_refresh.PricingRefresher()._refresh_once()

    assert not calls, "not due yet -> no network call at all"


# --- apply_cached_override --------------------------------------------------

def test_apply_cached_override_uses_existing_cache(monkeypatch, tmp_path, sample_md):
    cache_file = tmp_path / "price_map.json"
    cached_map = pricing_updater.build_price_map(pricing_updater.parse_rate_card(sample_md))
    pricing_updater.write_price_map(cached_map, cache_file)
    monkeypatch.setattr(pricing_refresh, "cache_path", lambda: cache_file)

    pricing_refresh.apply_cached_override()

    assert pricing.load_price_map()["fetched_at"] == cached_map["fetched_at"]


def test_apply_cached_override_noop_without_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(pricing_refresh, "cache_path", lambda: tmp_path / "does_not_exist.json")

    pricing_refresh.apply_cached_override()

    # Falls back to the bundled map rather than raising.
    assert "claude-opus-4-8" in pricing.load_price_map()["models"]


# --- fetch_model_registry ----------------------------------------------------

def test_fetch_model_registry_single_page(monkeypatch):
    def handler(request):
        assert request.headers["authorization"] == "Bearer tok123"
        return httpx.Response(200, json={
            "data": [
                {"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"},
                {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"},
            ],
            "has_more": False, "first_id": "a", "last_id": "b",
        })
    monkeypatch.setattr(pricing_refresh.httpx, "Client", _mock_httpx_client(handler))

    registry = pricing_refresh.fetch_model_registry("tok123")

    assert registry == {"Claude Sonnet 5": "claude-sonnet-5", "Claude Opus 4.8": "claude-opus-4-8"}


def test_fetch_model_registry_paginates(monkeypatch):
    seen_after_ids = []

    def handler(request):
        after = request.url.params.get("after_id")
        seen_after_ids.append(after)
        if after is None:
            return httpx.Response(200, json={
                "data": [{"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"}],
                "has_more": True, "first_id": "1", "last_id": "1",
            })
        return httpx.Response(200, json={
            "data": [{"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"}],
            "has_more": False, "first_id": "2", "last_id": "2",
        })
    monkeypatch.setattr(pricing_refresh.httpx, "Client", _mock_httpx_client(handler))

    registry = pricing_refresh.fetch_model_registry("tok123")

    assert registry == {"Claude Sonnet 5": "claude-sonnet-5", "Claude Opus 4.8": "claude-opus-4-8"}
    assert seen_after_ids == [None, "1"]


def test_fetch_model_registry_raises_on_http_error(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"error": "unauthorized"})
    monkeypatch.setattr(pricing_refresh.httpx, "Client", _mock_httpx_client(handler))

    with pytest.raises(httpx.HTTPError):
        pricing_refresh.fetch_model_registry("bad-token")


# --- PricingRefresher._fetch_registry (graceful fallback) -------------------

def test_fetch_registry_none_without_a_token(monkeypatch):
    monkeypatch.setattr(pricing_refresh.poller, "read_token", lambda: None)
    assert pricing_refresh.PricingRefresher()._fetch_registry() is None


def test_fetch_registry_none_on_fetch_failure(monkeypatch):
    monkeypatch.setattr(pricing_refresh.poller, "read_token", lambda: "tok")

    def _boom(token, timeout=15.0):
        raise ConnectionError("offline")
    monkeypatch.setattr(pricing_refresh, "fetch_model_registry", _boom)

    assert pricing_refresh.PricingRefresher()._fetch_registry() is None


def test_fetch_registry_returns_dict_on_success(monkeypatch):
    monkeypatch.setattr(pricing_refresh.poller, "read_token", lambda: "tok")
    monkeypatch.setattr(pricing_refresh, "fetch_model_registry",
                         lambda token, timeout=15.0: {"Claude Sonnet 5": "claude-sonnet-5"})

    assert pricing_refresh.PricingRefresher()._fetch_registry() == {"Claude Sonnet 5": "claude-sonnet-5"}


def test_refresh_once_uses_registry_id_when_available(monkeypatch, tmp_path, sample_md):
    cache_file = tmp_path / "price_map.json"
    monkeypatch.setattr(pricing_refresh, "cache_path", lambda: cache_file)
    monkeypatch.setattr(pricing_updater, "fetch_rate_card", lambda: sample_md)
    monkeypatch.setattr(app_settings, "get_last_pricing_refresh", lambda: 0.0)
    monkeypatch.setattr(app_settings, "set_last_pricing_refresh", lambda ts: None)
    monkeypatch.setattr(pricing_refresh.poller, "read_token", lambda: "tok")
    monkeypatch.setattr(pricing_refresh, "fetch_model_registry",
                         lambda token, timeout=15.0: {"Claude Opus 4.8": "claude-opus-4-8-20991231"})

    pricing_refresh.PricingRefresher()._refresh_once()

    written = pricing_updater.load_existing(cache_file)
    assert "claude-opus-4-8-20991231" in written["models"]
    assert "claude-opus-4-8" not in written["models"]


def test_refresh_once_falls_back_when_no_token(monkeypatch, tmp_path, sample_md):
    cache_file = tmp_path / "price_map.json"
    monkeypatch.setattr(pricing_refresh, "cache_path", lambda: cache_file)
    monkeypatch.setattr(pricing_updater, "fetch_rate_card", lambda: sample_md)
    monkeypatch.setattr(app_settings, "get_last_pricing_refresh", lambda: 0.0)
    monkeypatch.setattr(app_settings, "set_last_pricing_refresh", lambda ts: None)
    monkeypatch.setattr(pricing_refresh.poller, "read_token", lambda: None)

    pricing_refresh.PricingRefresher()._refresh_once()   # must not raise

    written = pricing_updater.load_existing(cache_file)
    assert "claude-opus-4-8" in written["models"]   # NAME_TO_ID's guess, unchanged


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
