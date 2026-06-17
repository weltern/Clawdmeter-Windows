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
from transcript import account_tokens_by_model, scan_events

# Monthly subscription price (USD) keyed by organization.rate_limit_tier.
PLAN_PRICES = {
    "default_claude_max_20x": 200.0,
    "default_claude_max_5x": 100.0,
    "default_claude_pro": 20.0,
}

_DATE_SUFFIX = re.compile(r"-\d{6,8}$")

# Extension (lowercase, no dot) -> language for the code-by-language breakdown.
# Aims to cover the majorly-used languages; anything unlisted falls to "Other".
LANGUAGE_BY_EXT = {
    "py": "Python", "pyw": "Python", "pyi": "Python",
    "cs": "C#",
    "java": "Java",
    "kt": "Kotlin", "kts": "Kotlin",
    "scala": "Scala", "sc": "Scala",
    "groovy": "Groovy", "gradle": "Groovy",
    "c": "C/C++", "h": "C/C++", "cpp": "C/C++", "cxx": "C/C++", "cc": "C/C++",
    "hpp": "C/C++", "hh": "C/C++", "hxx": "C/C++",
    "go": "Go",
    "rs": "Rust",
    "swift": "Swift",
    "dart": "Dart",
    "rb": "Ruby", "erb": "Ruby", "rake": "Ruby",
    "php": "PHP",
    "js": "JavaScript", "jsx": "JavaScript", "mjs": "JavaScript", "cjs": "JavaScript",
    "ts": "TypeScript", "tsx": "TypeScript", "mts": "TypeScript", "cts": "TypeScript",
    "vue": "Vue",
    "svelte": "Svelte",
    "html": "HTML", "htm": "HTML",
    "css": "CSS", "scss": "CSS", "sass": "CSS", "less": "CSS",
    "lua": "Lua",
    "r": "R", "rmd": "R",
    "sh": "Shell", "bash": "Shell", "zsh": "Shell", "fish": "Shell",
    "ps1": "PowerShell", "psm1": "PowerShell", "psd1": "PowerShell",
    "pl": "Perl", "pm": "Perl",
    "hs": "Haskell",
    "ex": "Elixir", "exs": "Elixir",
    "erl": "Erlang", "hrl": "Erlang",
    "clj": "Clojure", "cljs": "Clojure", "cljc": "Clojure",
    "fs": "F#", "fsx": "F#", "fsi": "F#",
    "vb": "Visual Basic",
    "jl": "Julia",
    "zig": "Zig",
    "nim": "Nim",
    "ml": "OCaml", "mli": "OCaml",
    "sol": "Solidity",
    "sql": "SQL",
    "graphql": "GraphQL", "gql": "GraphQL",
    "md": "Markdown", "markdown": "Markdown", "mdx": "Markdown",
    "json": "JSON", "jsonc": "JSON",
    "yml": "YAML", "yaml": "YAML",
    "toml": "TOML",
    "xml": "XML",
    "proto": "Protobuf",
}


def language_for_path(path: str) -> str:
    """Language for a file path from its extension (handles both / and \\
    separators). Leading-dot names (.env, .gitignore) and extension-less files
    (Dockerfile, Makefile) have no extension -> 'Other'."""
    name = re.split(r"[\\/]", path or "")[-1]
    dot = name.rfind(".")
    ext = name[dot + 1:].lower() if dot > 0 else ""
    return LANGUAGE_BY_EXT.get(ext, "Other")


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

SESSION_GAP_SECONDS = 30 * 60   # a lull longer than this starts a new work session


def _zero() -> dict:
    return {k: 0 for k in _BUCKETS}


def _add(acc: dict, vals: tuple) -> None:
    for k, v in zip(_BUCKETS, vals):
        acc[k] += v


def break_even_day(value_by_day: list, monthly_price: float | None):
    """The `date` this month's cumulative value first reached the subscription
    price, or None (no price set, or not reached yet). `value_by_day` is
    [(date, usd)] oldest-first."""
    if not monthly_price or monthly_price <= 0:
        return None
    cum = 0.0
    for d, v in value_by_day:
        cum += v
        if cum >= monthly_price:
            return d
    return None


def cache_hit_rate(cache_read: int, input_tokens: int) -> float:
    """Share of input served from cache: cache_read / (cache_read + fresh input).
    0.0 when there's no input at all."""
    denom = (cache_read or 0) + (input_tokens or 0)
    return (cache_read or 0) / denom if denom else 0.0


def sessionize(timestamps: list, gap: float = SESSION_GAP_SECONDS) -> list:
    """Group assistant-turn timestamps into (start, end) work sessions, splitting
    whenever the lull between consecutive turns exceeds `gap` seconds."""
    ts = sorted(t for t in timestamps if t is not None)
    if not ts:
        return []
    sessions: list = []
    start = prev = ts[0]
    for t in ts[1:]:
        if t - prev > gap:
            sessions.append((start, prev))
            start = t
        prev = t
    sessions.append((start, prev))
    return sessions


def session_stats(timestamps: list, gap: float = SESSION_GAP_SECONDS) -> dict:
    """Count / average / longest duration of the gap-split work sessions."""
    durs = [e - s for s, e in sessionize(timestamps, gap)]
    return {
        "count": len(durs),
        "avg_secs": (sum(durs) / len(durs)) if durs else 0.0,
        "longest_secs": max(durs) if durs else 0.0,
    }


def day_streaks(active_days: set, today) -> tuple[int, int]:
    """(current streak ending today or yesterday, best-ever streak) over a set of
    active `date`s. Anchoring on yesterday means a streak survives a day with no
    usage *yet*."""
    if not active_days:
        return 0, 0
    ordered = sorted(active_days)
    best = run = 1
    for a, b in zip(ordered, ordered[1:]):
        if (b - a).days == 1:
            run += 1
            best = max(best, run)
        elif (b - a).days > 1:
            run = 1
    cur = 0
    d = today
    if d not in active_days and (today - timedelta(days=1)) in active_days:
        d = today - timedelta(days=1)
    while d in active_days:
        cur += 1
        d -= timedelta(days=1)
    return cur, best


def language_breakdown(file_events: list, since: float) -> tuple[dict, int]:
    """({language: distinct-file count}, total files) for files mutated at/after
    `since`, deduped by path (a file edited many times counts once)."""
    seen: set = set()
    counts: dict = {}
    for ts, path in (file_events or []):
        if ts >= since and path not in seen:
            seen.add(path)
            lang = language_for_path(path)
            counts[lang] = counts.get(lang, 0) + 1
    return counts, len(seen)


def build_aggregate(events: list, now: float, since: float,
                    activity_events: list | None = None,
                    file_events: list | None = None) -> dict:
    """Compute every Stats aggregate from raw events in one pass (pure).

    `events`: (ts, model, project, input, output, cache_read, cache_write) — a
    LIFETIME list; `since` (the month start) scopes the month-to-date figures
    while the cross-period figures (lifetime value, streaks, week-over-week,
    record day) read the whole list. `activity_events`: (ts, activity_str) tool
    calls and `file_events`: (ts, file_path) mutated files, both scoped to the
    month for the activity / code-by-language breakdowns.
    """
    by_model: dict = {}
    by_project: dict = {}                       # project -> {model: usage}
    day_tokens: dict = {}                       # month: date -> {model: usage}
    heatmap = [[0] * 24 for _ in range(7)]      # [weekday 0=Mon][hour] turn counts
    turns = 0
    days_seen: set = set()
    month_ts: list = []
    life_by_model: dict = {}
    life_day_tokens: dict = {}                  # lifetime: date -> {model: usage}
    life_days: set = set()
    cut7, cut14 = now - 7 * 86400, now - 14 * 86400
    wk_this: dict = {}
    wk_last: dict = {}

    for ts, model, project, i, o, cr, cw in events:
        vals = (i, o, cr, cw)
        d = datetime.fromtimestamp(ts).date()
        _add(life_by_model.setdefault(model, _zero()), vals)
        _add(life_day_tokens.setdefault(d, {}).setdefault(model, _zero()), vals)
        life_days.add(d)
        if ts >= cut7:
            _add(wk_this.setdefault(model, _zero()), vals)
        elif ts >= cut14:
            _add(wk_last.setdefault(model, _zero()), vals)
        if ts >= since:
            turns += 1
            dt = datetime.fromtimestamp(ts)
            heatmap[dt.weekday()][dt.hour] += 1
            days_seen.add(d)
            month_ts.append(ts)
            _add(by_model.setdefault(model, _zero()), vals)
            _add(day_tokens.setdefault(d, {}).setdefault(model, _zero()), vals)
            _add(by_project.setdefault(project, {}).setdefault(model, _zero()), vals)

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

    record_day = None
    if life_day_tokens:
        rd_date, rd_models = max(life_day_tokens.items(),
                                 key=lambda kv: value_usd(kv[1]))
        record_day = (rd_date, value_usd(rd_models))

    cur_streak, best_streak = day_streaks(life_days, today)

    activity_counts: dict = {}
    for ts, a in (activity_events or []):
        if ts >= since:
            activity_counts[a] = activity_counts.get(a, 0) + 1

    lang_counts, files_edited = language_breakdown(file_events, since)

    cache_read_tokens = sum(u["cache_read"] for u in by_model.values())
    input_tokens = sum(u["input"] for u in by_model.values())

    return {
        "value_total": value_usd(by_model),
        "value_by_day": series,          # [(date, usd)] month_start..today
        "heatmap": heatmap,              # [7][24] assistant-turn counts (month)
        "by_model_value": by_model_value,
        "by_project_value": by_project_value,
        "top_model": top_model,          # (model_id, usd) | None
        "busiest_day": busiest,          # (date, usd) | None  — this month
        "turns": turns,
        "active_days": len(days_seen),
        "cache_savings_usd": cache_savings_usd(by_model),
        "cache_read_tokens": cache_read_tokens,
        "input_tokens": input_tokens,
        "cache_hit_rate": cache_hit_rate(cache_read_tokens, input_tokens),
        # cross-period (read the whole event list, not just the month)
        "lifetime_value_usd": value_usd(life_by_model),
        "record_day": record_day,        # (date, usd) | None — biggest day ever
        "current_streak": cur_streak,
        "best_streak": best_streak,
        "week_this_usd": value_usd(wk_this),
        "week_last_usd": value_usd(wk_last),
        "sessions": session_stats(month_ts),
        "activity_counts": activity_counts,
        "language_counts": lang_counts,     # {language: distinct-file count} (month)
        "files_edited": files_edited,       # distinct files mutated this month
    }


def compute_aggregate(now: float) -> dict:
    """Full Stats aggregate: month-to-date figures plus lifetime value, streaks,
    week-over-week, activity and code-by-language breakdowns. Lifetime transcript
    scan (cached per file by size/mtime). Disk I/O — call it off the UI thread."""
    rows, acts, files = scan_events(0.0)
    return build_aggregate(rows, now, month_start_ts(now),
                           activity_events=acts, file_events=files)
