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

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import app_settings  # noqa: E402
import pricing  # noqa: E402
import pricing_refresh  # noqa: E402
from pricing import updater as pricing_updater  # noqa: E402

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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
