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
from datetime import datetime, timedelta

from pricing import model_rates
from transcript import account_tokens_by_model, iter_model_events

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


def model_display(model_id: str) -> str:
    """Human display name for a model id (from price_map), else the id itself."""
    rates = _rates_for(model_id)
    if rates and rates.get("display_name"):
        return rates["display_name"]
    return model_id


def value_usd(tokens_by_model: dict) -> float:
    """Total API-equivalent USD value across all models (rounded to cents)."""
    return round(sum(model_value_usd(m, u) for m, u in tokens_by_model.items()), 2)


def cache_savings_usd(tokens_by_model: dict) -> float:
    """USD saved by cache reads vs paying the full input rate for those tokens
    (cache reads are far cheaper than fresh input). Unknown models contribute 0."""
    total = 0.0
    for m, u in tokens_by_model.items():
        rates = _rates_for(m)
        if not rates:
            continue
        ir, crr = rates.get("input"), rates.get("cache_read")
        if isinstance(ir, (int, float)) and isinstance(crr, (int, float)):
            total += (u.get("cache_read", 0) or 0) * (ir - crr) / 1_000_000.0
    return round(total, 2)


def cap_eta(points: list, current: float, now: float, min_span: float = 600.0) -> float | None:
    """Estimate when a rising window hits 100%, from recent (ts, pct) samples.

    Linear slope across the sampled span (oldest->newest). Returns the projected
    epoch, or None when there isn't enough data (need >= min_span seconds of it),
    the window isn't rising, or it's already at/over the cap. Strictly "at current
    pace" — bursty work makes this optimistic/pessimistic, so callers must label it.
    """
    pts = sorted((p for p in points if p[0] is not None and p[1] is not None),
                 key=lambda p: p[0])
    if len(pts) < 2 or current >= 100:
        return None
    (t0, p0), (t1, p1) = pts[0], pts[-1]
    span = t1 - t0
    if span < min_span:
        return None
    slope = (p1 - p0) / span            # percent per second
    if slope <= 0:
        return None
    return now + (100.0 - current) / slope


def month_start_ts(now: float) -> float:
    """Epoch of local midnight on the 1st of `now`'s month."""
    dt = datetime.fromtimestamp(now)
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def monthly_value_usd(now: float) -> float:
    """API-equivalent USD value of this calendar month's usage (transcript scan)."""
    return value_usd(account_tokens_by_model(month_start_ts(now)))


_BUCKETS = ("input", "output", "cache_read", "cache_write")


def build_aggregate(events: list, now: float, since: float) -> dict:
    """Compute the Stats aggregates from raw model events in one pass (pure).

    `events`: list of (ts, model, input, output, cache_read, cache_write).
    `since`: window start (the per-day series spans since..today). Returns the
    month-to-date value, a per-day value series (sparkline), a 7x24 weekday/hour
    assistant-turn heatmap, per-model value, and the Wrapped headline figures.
    """
    by_model: dict[str, dict[str, int]] = {}
    by_project: dict[str, dict[str, dict[str, int]]] = {}   # project -> {model: usage}
    day_tokens: dict = {}                       # date -> {model: usage}
    heatmap = [[0] * 24 for _ in range(7)]      # [weekday 0=Mon][hour] turn counts
    turns = 0
    days_seen: set = set()
    for ts, model, project, i, o, cr, cw in events:
        turns += 1
        dt = datetime.fromtimestamp(ts)
        heatmap[dt.weekday()][dt.hour] += 1
        d = dt.date()
        days_seen.add(d)
        acc = by_model.setdefault(model, {k: 0 for k in _BUCKETS})
        dacc = day_tokens.setdefault(d, {}).setdefault(model, {k: 0 for k in _BUCKETS})
        pacc = by_project.setdefault(project, {}).setdefault(model, {k: 0 for k in _BUCKETS})
        for key, v in zip(_BUCKETS, (i, o, cr, cw)):
            acc[key] += v
            dacc[key] += v
            pacc[key] += v

    by_model_value = {m: round(model_value_usd(m, u), 2) for m, u in by_model.items()}
    by_project_value = {pj: value_usd(models) for pj, models in by_project.items()}
    top_model = max(by_model_value.items(), key=lambda kv: kv[1]) if by_model_value else None

    series: list = []
    d = datetime.fromtimestamp(since).date()
    today = datetime.fromtimestamp(now).date()
    while d <= today:
        series.append((d, value_usd(day_tokens.get(d, {}))))
        d += timedelta(days=1)
    busiest = max(series, key=lambda dv: dv[1]) if series else None

    return {
        "value_total": value_usd(by_model),
        "value_by_day": series,          # [(date, usd)] oldest..today
        "heatmap": heatmap,              # [7][24] assistant-turn counts
        "by_model_value": by_model_value,
        "by_project_value": by_project_value,
        "top_model": top_model,          # (model_id, usd) | None
        "busiest_day": busiest,          # (date, usd) | None
        "turns": turns,
        "active_days": len(days_seen),
        "cache_savings_usd": cache_savings_usd(by_model),
        "cache_read_tokens": sum(u["cache_read"] for u in by_model.values()),
        "input_tokens": sum(u["input"] for u in by_model.values()),
    }


def monthly_aggregate(now: float) -> dict:
    """Build the full Stats aggregate for the current calendar month (disk scan)."""
    since = month_start_ts(now)
    return build_aggregate(iter_model_events(since), now, since)
