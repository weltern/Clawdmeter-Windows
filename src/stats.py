"""Stats computations for Clawdmeter — API-equivalent value of usage + plan ROI.

Pure logic (no Qt, no network). The dollar value joins per-model token tallies
from ``transcript.account_tokens_by_model`` with the bundled ``price_map``:

    value = Σ_model (input·in_rate + output·out_rate
                     + cache_read·cr_rate + cache_write·cw_rate) / 1e6

Compared against the monthly subscription price (from the plan tier), this is
the "your $X plan delivered $Y of pay-as-you-go value" ROI figure.
"""

from __future__ import annotations

import re
from datetime import datetime

from pricing import model_rates
from transcript import account_tokens_by_model

# Monthly subscription price (USD) keyed by organization.rate_limit_tier.
PLAN_PRICES = {
    "default_claude_max_20x": 200.0,
    "default_claude_max_5x": 100.0,
    "default_claude_pro": 20.0,
}

_DATE_SUFFIX = re.compile(r"-\d{6,8}$")


def plan_monthly_usd(tier: str | None) -> float | None:
    """Monthly subscription price for a rate_limit_tier, or None if unknown."""
    return PLAN_PRICES.get(tier or "")


def _rates_for(model_id: str) -> dict | None:
    """Price-map rates for a model, tolerating a dated id (claude-x-1-20251101)
    by falling back to the undated key the map uses."""
    r = model_rates(model_id)
    if r:
        return r
    base = _DATE_SUFFIX.sub("", model_id)
    return model_rates(base) if base != model_id else None


def model_value_usd(model_id: str, usage: dict) -> float:
    """USD pay-as-you-go value of one model's token usage. Unknown models -> 0."""
    rates = _rates_for(model_id)
    if not rates:
        return 0.0

    def rate(key: str) -> float:
        v = rates.get(key)
        return float(v) if isinstance(v, (int, float)) else 0.0

    def tok(k: str) -> float:
        return float(usage.get(k, 0) or 0)

    return (
        tok("input") * rate("input")
        + tok("output") * rate("output")
        + tok("cache_read") * rate("cache_read")
        + tok("cache_write") * rate("cache_write_5m")  # 5m TTL is Claude Code's default
    ) / 1_000_000.0


def value_usd(tokens_by_model: dict) -> float:
    """Total API-equivalent USD value across all models (rounded to cents)."""
    return round(sum(model_value_usd(m, u) for m, u in tokens_by_model.items()), 2)


def month_start_ts(now: float) -> float:
    """Epoch of local midnight on the 1st of `now`'s month."""
    dt = datetime.fromtimestamp(now)
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def monthly_value_usd(now: float) -> float:
    """API-equivalent USD value of this calendar month's usage (transcript scan)."""
    return value_usd(account_tokens_by_model(month_start_ts(now)))
