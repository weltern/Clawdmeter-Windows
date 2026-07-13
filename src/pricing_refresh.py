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

It also closes a second, subtler gap: the rate card only ever prints a *display
name* ("Claude Opus 4.5"), never the API model ID real usage is billed under
("claude-opus-4-5-20251101") -- so mapping one to the other has always relied on
``pricing.updater.NAME_TO_ID``, a hand-maintained guess that has, confirmed,
already gotten dated IDs wrong for several "known" models. ``fetch_model_registry``
asks Anthropic directly instead, via the same OAuth session ``poller.py`` already
authenticates with (no new credential, no user action): Anthropic's Models API
returns the exact {id, display_name} pairs it uses, so the join stops being a
guess. It's live-only -- the offline CI job has no user session to call this
with -- so it's passed to ``build_price_map`` as an optional ``registry``;
omitted (as CI does), behavior is unchanged. A failed registry fetch (expired
token, offline, endpoint hiccup) never blocks the pricing refresh itself -- it
just falls back to NAME_TO_ID's guess for that cycle, same as it always has.

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

import httpx
from PySide6.QtCore import QStandardPaths, QThread, Signal

import app_settings
import pricing
import poller
from pricing import updater as pricing_updater

log = logging.getLogger("clawdmeter.pricing_refresh")

CHECK_INTERVAL_SECONDS = 24 * 60 * 60   # only actually hit the network once/day
_INITIAL_DELAY_SECONDS = 15             # let the poller/update-checker go first
_WAKE_SECONDS = 60                      # re-evaluate stop cadence

CACHE_FILENAME = "price_map.json"

MODELS_API_URL = "https://api.anthropic.com/v1/models"
_MODELS_PAGE_LIMIT = 100


def fetch_model_registry(token: str, timeout: float = 15.0) -> dict[str, str]:
    """{display_name: api_id} for every model Anthropic's Models API currently
    lists, authenticated with the same OAuth session token ``poller.py`` reads
    from the local Claude Code credentials file (no separate API key needed).

    Paginates via ``after_id``/``has_more``. Raises ``httpx.HTTPError`` on any
    failure -- deliberately loud, like ``fetch_rate_card`` -- so an expired
    token or a network blip can never look like "Anthropic has zero models";
    the caller treats a raised error as "skip the registry this cycle, fall
    back to NAME_TO_ID," never as "the registry is empty."
    """
    headers = dict(poller.API_HEADERS_TEMPLATE)
    headers["Authorization"] = f"Bearer {token}"
    headers.pop("Content-Type", None)   # GET, no request body

    registry: dict[str, str] = {}
    after_id: str | None = None
    with httpx.Client(timeout=timeout, headers=headers) as http:
        while True:
            params = {"limit": _MODELS_PAGE_LIMIT}
            if after_id:
                params["after_id"] = after_id
            resp = http.get(MODELS_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            for m in data.get("data") or []:
                name, api_id = m.get("display_name"), m.get("id")
                if name and api_id:
                    registry[name] = api_id
            if not data.get("has_more"):
                break
            after_id = data.get("last_id")
    return registry


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

    def _fetch_registry(self) -> dict[str, str] | None:
        """Best-effort: None (not an error) if there's no session token, or the
        registry fetch itself fails -- either way the caller just falls back to
        NAME_TO_ID's static guess for this cycle instead of skipping the whole
        pricing refresh over a registry-only problem."""
        token = poller.read_token()
        if not token:
            return None
        try:
            return fetch_model_registry(token)
        except Exception as exc:   # noqa: BLE001 - registry is a nice-to-have
            log.warning("Model registry fetch failed, falling back to "
                        "NAME_TO_ID for this cycle: %s", exc)
            return None

    def _refresh_once(self) -> None:
        if not self._due():
            return
        try:
            markdown = pricing_updater.fetch_rate_card()
            parsed = pricing_updater.parse_rate_card(markdown)
            registry = self._fetch_registry()
            new_map = pricing_updater.build_price_map(parsed, registry=registry)
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
