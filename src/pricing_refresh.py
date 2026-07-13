"""Runtime (in-app) pricing refresh — the live counterpart to ``pricing.updater``.

``pricing.updater`` also runs offline once a week via the "Update price map" GH
Action, which opens a PR for a human to review before it reaches ``develop`` —
and even then, a merged change only reaches an installed Clawdmeter on that
user's *next app update*, since ``price_map.json`` is otherwise baked into the
exe at build time (see ``pricing.price_map_path``). That's a whole version
release just to refresh a JSON file nobody reviews line-by-line.

This module closes that gap: ``PricingRefresher`` fetches, parses, and validates
the same live rate card directly (reusing ``pricing.updater``'s already-tested,
network-isolated pure functions — nothing is duplicated here), and if the result
differs from what's currently active, writes it to a per-user cache file and
points ``pricing.load_price_map()`` at it via ``pricing.set_override_path()``.
No new release, no PR review, no waiting on anyone.

Mirrors ``update_check.UpdateChecker`` almost exactly: a QThread that wakes on a
slow cadence, throttles real network hits to roughly once a day via a persisted
timestamp, and swallows every fetch/parse/validation error so a bad page or an
offline machine never crashes the thread or blocks startup — it just keeps
using the last-known-good map (cached override, or the bundled one) and retries
next cycle.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QThread, Signal

import app_settings
import pricing
from pricing import updater as pricing_updater

log = logging.getLogger("clawdmeter.pricing_refresh")

CHECK_INTERVAL_SECONDS = 24 * 60 * 60   # only actually hit the network once/day
_INITIAL_DELAY_SECONDS = 15             # let the poller/update-checker go first
_WAKE_SECONDS = 60                      # re-evaluate stop cadence

CACHE_FILENAME = "price_map.json"


def cache_path() -> Path:
    """`%APPDATA%/Clawdmeter/price_map.json` (falls back to ~/.clawdmeter) —
    same directory usage_history.py already uses. Never the install directory,
    which may not be writable and shouldn't be mutated at runtime anyway."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    d = Path(base) if base else (Path.home() / ".clawdmeter")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d / CACHE_FILENAME


def apply_cached_override() -> None:
    """Call once at startup, before anything reads pricing (stats, Stats page):
    if an earlier session's live refresh left a validated cache file behind,
    prefer it over the build-time bundled map immediately — don't wait for
    today's throttle window to allow another fetch."""
    path = cache_path()
    if path.exists():
        pricing.set_override_path(path)


class PricingRefresher(QThread):
    """Background poll of Anthropic's published rate card. Emits `refreshed`
    with the change summary when a live fetch differs from what's active."""

    refreshed = Signal(dict)   # diff_maps() result

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _due(self) -> bool:
        return (time.time() - app_settings.get_last_pricing_refresh()) >= CHECK_INTERVAL_SECONDS

    def _refresh_once(self) -> None:
        if not self._due():
            return
        try:
            markdown = pricing_updater.fetch_rate_card()
            parsed = pricing_updater.parse_rate_card(markdown)
            new_map = pricing_updater.build_price_map(parsed)
        except Exception as exc:   # noqa: BLE001 - a bad page/network must never
            # crash this thread; a run of failures just retries next wake cycle
            # rather than falsely marking today as checked (mirrors UpdateChecker).
            log.warning("Live pricing refresh failed, will retry: %s", exc)
            return
        app_settings.set_last_pricing_refresh(time.time())

        path = cache_path()
        existing = pricing_updater.load_existing(path) or pricing.load_price_map()
        diff = pricing_updater.diff_maps(existing, new_map)
        if not pricing_updater.has_changes(diff):
            return

        pricing_updater.write_price_map(new_map, path)
        pricing.set_override_path(path)
        log.info("Live pricing refreshed: %s", pricing_updater.format_diff(diff))
        self.refreshed.emit(diff)

    def run(self) -> None:  # QThread entry
        for _ in range(_INITIAL_DELAY_SECONDS):
            if self._stop:
                return
            self.msleep(1000)

        while not self._stop:
            self._refresh_once()   # throttled internally via _due()
            for _ in range(_WAKE_SECONDS):
                if self._stop:
                    return
                self.msleep(1000)
