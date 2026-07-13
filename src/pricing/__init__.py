"""USD pricing for Claude models, for Clawdmeter-Windows.

The bundled ``price_map.json`` is the source of truth: a metadata header plus a
``models`` object **keyed by Claude API model ID** so it joins directly with the
usage data the poller already keys by model. Prices are USD per million tokens
(``unit: per_mtok``); ``multipliers`` and ``surcharges`` cover derivable feature
rates and non-token usage charges.

This module is the loader/accessor: ``load_price_map()`` reads the JSON (locating
it whether running from source or a PyInstaller bundle, mirroring
``sprite_player.assets_root``), and ``model_rates(model_id)`` is a thin per-model
lookup. A full cost calculator is intentionally out of scope; ``updater.py`` keeps
the map current from Anthropic's published rate card — offline via CI, and live
via ``pricing_refresh.PricingRefresher`` (see that module), which calls
``set_override_path()`` once it has fetched and validated a fresher map, so a
running app is never stuck with whatever shipped in its exe.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

PRICE_MAP_FILENAME = "price_map.json"

# Runtime override, set by pricing_refresh once a live-fetched map has passed
# the same validation the CI updater enforces. None = use the bundled map.
_override_path: Path | None = None


def price_map_path() -> Path:
    """Locate price_map.json whether running from source or a PyInstaller bundle.

    Mirrors ``sprite_player.assets_root``: under PyInstaller the file is unpacked
    into ``_MEIPASS/pricing`` (see Clawdmeter.spec ``datas``); from source it sits
    next to this module.
    """
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / "pricing" / PRICE_MAP_FILENAME
    return Path(__file__).resolve().parent / PRICE_MAP_FILENAME


def set_override_path(path: Path | None) -> None:
    """Point the loader at a runtime-refreshed cache file, or clear the override
    (``None``) to fall back to the bundled map. The caller is trusted to have
    already validated ``path`` — this module never fetches or parses anything
    itself. Thread-safe: ``lru_cache`` serializes concurrent readers/clearers."""
    global _override_path
    _override_path = path
    load_price_map.cache_clear()


@lru_cache(maxsize=1)
def load_price_map() -> dict[str, Any]:
    """Load and cache the active price map as a dict — the override file set by
    ``set_override_path()`` if one is live, else the bundled map.

    Cached because the map in use is immutable between refreshes (nothing
    mutates the file out from under a cached read; a new fetch instead writes a
    new override and clears this cache). Raises ``FileNotFoundError`` if the
    bundled map is missing and ``json.JSONDecodeError`` if it's corrupt — both
    are programmer errors (a broken build), not the swallow-and-continue network
    failures the poller guards against, so they're surfaced loudly.
    """
    path = _override_path if _override_path and _override_path.exists() else price_map_path()
    return json.loads(path.read_text(encoding="utf-8"))


def model_rates(model_id: str) -> dict[str, Any] | None:
    """Return the per-MTok USD rates for an API model ID, or None if unknown.

    Returns None (rather than raising) for an unknown model so callers joining
    against live usage data can degrade gracefully — usage may reference a model
    that isn't in the map yet, just as the poller tolerates missing fields.

    A model may carry ``rate_changes``: scheduled repricings that weren't yet
    in effect when the map was written (see
    ``pricing.updater.resolve_time_boxed_variants``). If the *latest* one whose
    ``effective_from`` is today-or-earlier exists, its fields override the
    entry's own — so a scheduled price change applies itself the day it takes
    effect, purely from wall-clock time, with no re-fetch required.
    """
    entry = load_price_map().get("models", {}).get(model_id)
    if entry is None:
        return None
    changes = entry.get("rate_changes")
    if not changes:
        return entry
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    due = [c for c in changes if c.get("effective_from", "") <= today]
    if not due:
        return entry
    latest = max(due, key=lambda c: c["effective_from"])
    merged = {**entry, **latest}
    merged.pop("rate_changes", None)
    return merged
