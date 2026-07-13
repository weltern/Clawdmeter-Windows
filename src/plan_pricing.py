"""Live refresh of Claude subscription-plan prices (Pro / Max) -- the
counterpart to pricing_refresh.py for the OTHER number stats.py's ROI card
needs alongside per-token model rates.

stats.PLAN_PRICES has always been a fully static dict, because Anthropic's
usage API only ever returns a plan *tier name* (e.g. "default_claude_max_5x"),
never a dollar figure -- there was no live source at all until now.

claude.com/pricing turns out to carry stable, machine-readable anchors for
this: Webflow `data-plan="..."` attributes on each price element, fetchable
with a plain httpx.get() -- no JS execution, no bot-blocking (unlike
claude.ai/pricing, which 403s a non-browser client). Confirmed live:
`free`, `pro_monthly`, `pro_annual`, `max_5x_monthly`.

Two things this deliberately does NOT pretend to solve, kept as disclosed
assumptions rather than silent guesses:
  - Max 20x's actual price is not present anywhere in the static page (only
    "5x or 20x more usage than Pro" descriptive copy) -- getting the real
    figure would need a real browser executing JS, a genuinely new runtime
    dependency this project doesn't carry. Instead it's derived as
    MAX_20X_MULTIPLIER x the scraped Max 5x price -- correct against today's
    actual numbers, but a business-rule assumption, not a confirmed fact, and
    tagged "derived" (vs "live") in the written data so nothing downstream
    mistakes one for the other.
  - The account API's tier string never says monthly vs. annual billing, so
    Pro always uses the monthly anchor ($20) -- the plan's default price, not
    necessarily what a specific annual subscriber is actually charged.

Shares PricingRefresher's daily cadence/throttle rather than adding a second
thread and a second timestamp for a smaller, less-volatile secondary concern
-- see pricing_refresh._refresh_plan_prices().

Deliberately Qt-free (mirrors the pricing/__init__.py + pricing/updater.py
split): httpx is fine here, same as pricing.updater, but cache-path
resolution needs QStandardPaths, which lives in pricing_refresh.py alongside
its own cache_path() -- this module only ever takes a `path` it's handed,
never resolves one itself, so stats.py can import it without pulling Qt in.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from pricing.updater import _parse_price

PLAN_PAGE_URL = "https://claude.com/pricing"
_HEADERS = {"User-Agent": "Clawdmeter-plan-pricing"}
CACHE_FILENAME = "plan_prices.json"

_DATA_PLAN_RE = re.compile(r'data-plan="([a-z0-9_]+)"[^>]*>([^<]{1,20})<')

# data-plan anchor -> the stats.PLAN_PRICES key it feeds.
_ANCHOR_TO_KEY = {
    "pro_monthly": "default_claude_pro",
    "max_5x_monthly": "default_claude_max_5x",
}
MAX_20X_MULTIPLIER = 2.0   # Max 20x has no live anchor; derived from Max 5x.

_override_path: Path | None = None


# --- fetch + parse (pure, network isolated per the fetch/parse split used
# elsewhere in this project) ------------------------------------------------

def fetch_plan_page(timeout: float = 15.0) -> str:
    """Fetch claude.com/pricing's raw HTML. Raises httpx.HTTPError on failure
    -- loud, like pricing.updater.fetch_rate_card, so a bad fetch can never
    look like real data; the caller falls back to the last-known-good value.
    """
    with httpx.Client(timeout=timeout, headers=_HEADERS, follow_redirects=True) as http:
        resp = http.get(PLAN_PAGE_URL)
        resp.raise_for_status()
        return resp.text


def parse_plan_prices(html: str) -> dict[str, dict[str, Any]]:
    """{tier: {"amount": float, "source": "live"|"derived"}} for the tiers
    stats.PLAN_PRICES already knows about.

    Raises ValueError if either required live anchor (pro_monthly,
    max_5x_monthly) is missing or its price doesn't parse to a positive
    number -- a page redesign must never silently produce a wrong or zero
    price; refusing outright and keeping the last-known-good value is safer
    than a partial, maybe-stale-maybe-wrong result.
    """
    found: dict[str, str] = {}
    for anchor, text in _DATA_PLAN_RE.findall(html):
        if anchor in _ANCHOR_TO_KEY and anchor not in found:
            found[anchor] = text

    missing = [a for a in _ANCHOR_TO_KEY if a not in found]
    if missing:
        raise ValueError(f"claude.com/pricing is missing expected anchor(s): {missing}")

    prices: dict[str, dict[str, Any]] = {}
    for anchor, key in _ANCHOR_TO_KEY.items():
        amount = _parse_price(found[anchor])
        if amount is None or amount <= 0:
            raise ValueError(f"{anchor} price is not a positive number ({found[anchor]!r})")
        prices[key] = {"amount": amount, "source": "live"}

    max5x = prices["default_claude_max_5x"]["amount"]
    prices["default_claude_max_20x"] = {
        "amount": round(max5x * MAX_20X_MULTIPLIER, 2),
        "source": "derived",
    }
    return prices


# --- file I/O (mirrors pricing.updater.load_existing/write_price_map) ------
# CACHE_FILENAME's actual on-disk location is resolved by the caller
# (pricing_refresh.plan_prices_cache_path(), which needs QStandardPaths) and
# always passed in explicitly -- this module never resolves its own path.

def load_existing(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_plan_prices(prices: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(prices, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- loader/accessor (mirrors pricing.set_override_path/load_price_map) ----

def set_override_path(path: Path | None) -> None:
    global _override_path
    _override_path = path
    load_plan_prices.cache_clear()


@lru_cache(maxsize=1)
def load_plan_prices() -> dict[str, dict[str, Any]]:
    """The active plan-price overrides -- the cache file if one's live, else
    {} (meaning stats.plan_monthly_usd falls back to its static PLAN_PRICES).
    """
    path = _override_path if _override_path and _override_path.exists() else None
    if path is None:
        return {}
    return load_existing(path)


def plan_amount(tier: str) -> float | None:
    """The live/derived monthly price for a plan tier, or None if it's not in
    the current override (unknown tier, or no live refresh has happened yet)."""
    entry = load_plan_prices().get(tier)
    return entry["amount"] if entry else None
