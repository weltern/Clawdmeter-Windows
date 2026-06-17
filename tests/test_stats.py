"""Tests for stats.py (API-equivalent value + plan ROI) and the per-model
transcript scan. Uses the real bundled price_map; the scan uses a tmp transcript.

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import stats  # noqa: E402
from transcript import account_tokens_by_model  # noqa: E402


def test_model_value_known_rates():
    # claude-opus-4-8: input 5.0, output 25.0 USD per MTok
    assert stats.model_value_usd("claude-opus-4-8", {"input": 1_000_000}) == 5.0
    assert stats.model_value_usd("claude-opus-4-8", {"output": 1_000_000}) == 25.0
    v = stats.model_value_usd("claude-opus-4-8", {"input": 200_000, "output": 50_000})
    assert round(v, 2) == 2.25


def test_model_value_cache_buckets():
    # cache_read 0.5, cache_write_5m 6.25 USD per MTok
    v = stats.model_value_usd(
        "claude-opus-4-8", {"cache_read": 1_000_000, "cache_write": 1_000_000})
    assert round(v, 2) == 6.75


def test_unknown_model_is_zero():
    assert stats.model_value_usd("not-a-model", {"input": 1_000_000}) == 0.0


def test_dated_model_id_falls_back():
    assert stats.model_value_usd("claude-opus-4-8-20251101", {"input": 1_000_000}) == 5.0


def test_value_usd_sums_models():
    tbm = {"claude-opus-4-8": {"input": 1_000_000},
           "claude-sonnet-4-6": {"input": 1_000_000}}
    expect = round(
        stats.model_value_usd("claude-opus-4-8", {"input": 1_000_000})
        + stats.model_value_usd("claude-sonnet-4-6", {"input": 1_000_000}), 2)
    assert stats.value_usd(tbm) == expect


def test_plan_monthly_usd():
    assert stats.plan_monthly_usd("default_claude_max_5x") == 100.0
    assert stats.plan_monthly_usd("default_claude_max_20x") == 200.0
    assert stats.plan_monthly_usd("default_claude_pro") == 20.0
    assert stats.plan_monthly_usd("mystery_tier") is None
    assert stats.plan_monthly_usd(None) is None


def test_month_start_ts():
    now = datetime(2026, 6, 17, 14, 30, 5).timestamp()
    d = datetime.fromtimestamp(stats.month_start_ts(now))
    assert (d.year, d.month, d.day, d.hour, d.minute, d.second) == (2026, 6, 1, 0, 0, 0)
    assert stats.month_start_ts(now) <= now


def test_account_tokens_by_model(tmp_path):
    now = datetime.now(timezone.utc).timestamp()
    iso = datetime.now(timezone.utc).isoformat()
    old_iso = datetime.fromtimestamp(now - 10 * 86400, tz=timezone.utc).isoformat()
    f = tmp_path / "proj" / "sess.jsonl"
    f.parent.mkdir(parents=True)
    rows = [
        {"timestamp": iso, "message": {"role": "assistant", "model": "claude-opus-4-8",
            "usage": {"input_tokens": 100, "output_tokens": 50,
                      "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}}},
        {"timestamp": iso, "message": {"role": "assistant", "model": "claude-opus-4-8",
            "usage": {"input_tokens": 100, "output_tokens": 50}}},
        {"timestamp": old_iso, "message": {"role": "assistant", "model": "claude-opus-4-8",
            "usage": {"input_tokens": 999, "output_tokens": 999}}},  # pre-window -> excluded
        {"timestamp": iso, "message": {"role": "user", "content": "hi"}},  # not assistant
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    tbm = account_tokens_by_model(now - 3600, root=tmp_path)
    assert tbm == {"claude-opus-4-8":
                   {"input": 200, "output": 100, "cache_read": 10, "cache_write": 5}}


def test_build_aggregate():
    from datetime import date, datetime
    d1 = datetime(2026, 6, 10, 14, 0, 0)   # two opus turns this day
    d2 = datetime(2026, 6, 10, 15, 0, 0)
    d3 = datetime(2026, 6, 12, 9, 0, 0)    # one sonnet turn, different day
    ev = [
        (d1.timestamp(), "claude-opus-4-8", "alpha", 1_000_000, 0, 0, 0),   # $5
        (d2.timestamp(), "claude-opus-4-8", "alpha", 0, 1_000_000, 0, 0),   # $25
        (d3.timestamp(), "claude-sonnet-4-6", "beta", 1_000_000, 0, 0, 0),
    ]
    now = datetime(2026, 6, 12, 18, 0, 0).timestamp()
    since = datetime(2026, 6, 1, 0, 0, 0).timestamp()
    agg = stats.build_aggregate(ev, now, since)

    assert agg["turns"] == 3
    assert agg["active_days"] == 2
    sonnet_v = stats.model_value_usd("claude-sonnet-4-6", {"input": 1_000_000})
    assert agg["value_total"] == round(30.0 + sonnet_v, 2)
    assert agg["top_model"] == ("claude-opus-4-8", 30.0)
    assert agg["heatmap"][d1.weekday()][14] == 1
    assert agg["heatmap"][d1.weekday()][15] == 1
    assert agg["heatmap"][d3.weekday()][9] == 1
    series = dict(agg["value_by_day"])
    assert series[date(2026, 6, 10)] == 30.0
    assert series[date(2026, 6, 11)] == 0.0
    assert agg["busiest_day"][0] == date(2026, 6, 10)
    assert len(agg["value_by_day"]) == 12   # Jun 1..12 inclusive
    assert "cache_savings_usd" in agg and "cache_read_tokens" in agg
    assert agg["by_project_value"]["alpha"] == 30.0   # both opus turns


def test_cache_savings():
    # opus input 5.0, cache_read 0.5 -> saved (5.0-0.5) per MTok of cache reads
    assert stats.cache_savings_usd({"claude-opus-4-8": {"cache_read": 1_000_000}}) == 4.5
    assert stats.cache_savings_usd({"unknown-x": {"cache_read": 1_000_000}}) == 0.0
    assert stats.cache_savings_usd({}) == 0.0


def test_break_even_day():
    from datetime import date
    series = [(date(2026, 6, 1), 40.0), (date(2026, 6, 2), 80.0),
              (date(2026, 6, 3), 10.0)]
    assert stats.break_even_day(series, 100.0) == date(2026, 6, 2)   # 40+80 >= 100
    assert stats.break_even_day(series, 5.0) == date(2026, 6, 1)
    assert stats.break_even_day(series, 1000.0) is None              # never reached
    assert stats.break_even_day(series, None) is None
    assert stats.break_even_day(series, 0.0) is None


def test_cache_hit_rate():
    assert stats.cache_hit_rate(900, 100) == 0.9
    assert stats.cache_hit_rate(0, 0) == 0.0
    assert stats.cache_hit_rate(0, 500) == 0.0


def test_sessionize_and_stats():
    ts = [0, 60, 120, 2120, 2150]   # gap 2000s (>1800) splits before 2120
    sess = stats.sessionize(ts, gap=1800)
    assert sess == [(0, 120), (2120, 2150)]
    st = stats.session_stats(ts, gap=1800)
    assert st["count"] == 2
    assert st["longest_secs"] == 120
    assert st["avg_secs"] == 75
    assert stats.session_stats([], gap=1800) == {
        "count": 0, "avg_secs": 0.0, "longest_secs": 0.0}


def test_day_streaks():
    from datetime import date
    today = date(2026, 6, 17)
    cur, best = stats.day_streaks({date(2026, 6, 17), date(2026, 6, 16),
                                   date(2026, 6, 15), date(2026, 6, 10)}, today)
    assert cur == 3 and best == 3
    # today inactive but yesterday active -> streak survives, anchored yesterday
    cur, _ = stats.day_streaks({date(2026, 6, 16), date(2026, 6, 15)}, today)
    assert cur == 2
    assert stats.day_streaks(set(), today) == (0, 0)


def test_build_aggregate_cross_period():
    from datetime import datetime
    d1 = datetime(2026, 6, 16, 14, 0, 0)
    d2 = datetime(2026, 6, 16, 14, 5, 0)   # +5min: same session as d1
    ev = [
        (d1.timestamp(), "claude-opus-4-8", "alpha", 1_000_000, 0, 800_000, 0),
        (d2.timestamp(), "claude-opus-4-8", "alpha", 0, 1_000_000, 0, 0),
    ]
    now = datetime(2026, 6, 17, 12, 0, 0).timestamp()
    since = datetime(2026, 6, 1, 0, 0, 0).timestamp()
    acts = [(d1.timestamp(), "coding"), (d2.timestamp(), "reading"),
            (since - 10, "coding")]   # pre-month tool call -> excluded
    agg = stats.build_aggregate(ev, now, since, activity_events=acts)

    assert agg["lifetime_value_usd"] == agg["value_total"]   # all events in-month
    assert agg["current_streak"] >= 1 and agg["best_streak"] >= 1
    assert agg["week_this_usd"] == agg["value_total"]        # both within 7d of now
    assert agg["week_last_usd"] == 0.0
    assert agg["sessions"]["count"] == 1                     # 5-min gap < 30min
    assert agg["activity_counts"] == {"coding": 1, "reading": 1}
    assert 0.0 < agg["cache_hit_rate"] < 1.0                 # 800k cache vs 1M input
    assert agg["record_day"][1] == agg["value_total"]


def test_language_for_path():
    assert stats.language_for_path("/home/u/app/main.py") == "Python"
    assert stats.language_for_path(r"C:\proj\src\Program.cs") == "C#"
    assert stats.language_for_path("/x/components/App.tsx") == "TypeScript"
    assert stats.language_for_path("/x/ui/Page.svelte") == "Svelte"
    assert stats.language_for_path("/x/widget.VUE") == "Vue"          # case-insensitive
    assert stats.language_for_path("/x/Dockerfile") == "Other"        # no extension
    assert stats.language_for_path("/x/.env") == "Other"             # leading-dot only
    assert stats.language_for_path("/x/data.parquet") == "Other"      # unmapped ext
    assert stats.language_for_path("") == "Other"


def test_language_breakdown_dedup_and_window():
    since = 1000.0
    files = [
        (1100.0, "/p/a.py"), (1200.0, "/p/a.py"),   # same file twice -> 1
        (1300.0, "/p/b.py"),
        (1400.0, "/p/Main.cs"),
        (1500.0, "/p/README.md"),
        (900.0, "/p/old.py"),                        # before `since` -> excluded
    ]
    counts, total = stats.language_breakdown(files, since)
    assert counts == {"Python": 2, "C#": 1, "Markdown": 1}
    assert total == 4
    assert stats.language_breakdown([], since) == ({}, 0)


def test_build_aggregate_language():
    from datetime import datetime
    d1 = datetime(2026, 6, 10, 9, 0, 0)
    ev = [(d1.timestamp(), "claude-opus-4-8", "alpha", 1_000_000, 0, 0, 0)]
    now = datetime(2026, 6, 12, 18, 0, 0).timestamp()
    since = datetime(2026, 6, 1, 0, 0, 0).timestamp()
    files = [(d1.timestamp(), "/p/x.py"), (d1.timestamp(), "/p/y.ps1"),
             (since - 100, "/p/old.go")]   # pre-month -> excluded
    agg = stats.build_aggregate(ev, now, since, file_events=files)
    assert agg["language_counts"] == {"Python": 1, "PowerShell": 1}
    assert agg["files_edited"] == 2


def test_parse_iso_ts_utc_and_naive():
    from transcript import parse_iso_ts
    z = parse_iso_ts("2026-06-14T03:26:31.977Z")
    off = parse_iso_ts("2026-06-14T03:26:31.977+00:00")
    naive = parse_iso_ts("2026-06-14T03:26:31.977")   # tz-less -> treated as UTC
    assert z == off == naive
    assert parse_iso_ts("2026-06-14T03:26:31") == parse_iso_ts("2026-06-14T03:26:31Z")
    assert parse_iso_ts(None) is None
    assert parse_iso_ts("not-a-timestamp") is None


def test_build_aggregate_empty_month():
    from datetime import datetime
    now = datetime(2026, 6, 12, 18, 0, 0).timestamp()
    since = datetime(2026, 6, 1, 0, 0, 0).timestamp()
    agg = stats.build_aggregate([], now, since)
    assert agg["busiest_day"] is None        # no epoch-0 date for a zero month
    assert agg["record_day"] is None
    assert agg["value_total"] == 0.0 and agg["lifetime_value_usd"] == 0.0
    assert agg["current_streak"] == 0 and agg["best_streak"] == 0
    assert agg["files_edited"] == 0 and agg["language_counts"] == {}


def test_scan_events_streams(tmp_path):
    """The disk parser behind the whole Stats aggregate: token rows, activity
    classification, and file-mutation paths from one transcript."""
    from transcript import scan_events
    iso = datetime.now(timezone.utc).isoformat()
    f = tmp_path / "myproj" / "s.jsonl"
    f.parent.mkdir(parents=True)
    rows_in = [
        {"cwd": "/home/u/myproj", "timestamp": iso, "message": {
            "role": "assistant", "model": "claude-opus-4-8",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [
                {"type": "tool_use", "name": "Edit",
                 "input": {"file_path": "/home/u/myproj/app.py"}},   # mutation -> coding + file
                {"type": "tool_use", "name": "Read",
                 "input": {"file_path": "/home/u/myproj/readme.md"}},  # read -> reading, NO file
                {"type": "tool_use", "name": "mcp__github__list_prs",
                 "input": {}},                                          # mcp -> integrating
            ]}},
        {"timestamp": iso, "message": {"role": "user", "content": "hi"}},  # not assistant
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows_in), encoding="utf-8")
    rows, acts, files = scan_events(0.0, root=tmp_path)
    assert len(rows) == 1
    _ts, model, project, i, o, _cr, _cw = rows[0]
    assert (model, project, i, o) == ("claude-opus-4-8", "myproj", 10, 5)
    assert sorted(a for _, a in acts) == ["coding", "integrating", "reading"]
    assert [p for _, p in files] == ["/home/u/myproj/app.py"]   # only the mutating Edit


def test_cap_eta():
    pts = [(0.0, 10.0), (1200.0, 30.0)]          # +20% over 1200s
    eta = stats.cap_eta(pts, current=30.0, now=1200.0)
    assert eta is not None and abs(eta - 5400.0) < 1.0   # 70% left / slope
    assert stats.cap_eta([(0.0, 50.0), (1200.0, 50.0)], 50.0, 1200.0) is None   # flat
    assert stats.cap_eta([(0.0, 10.0), (100.0, 30.0)], 30.0, 100.0) is None     # span<600
    assert stats.cap_eta(pts, current=100.0, now=1200.0) is None                # at cap
    assert stats.cap_eta([(0.0, 10.0)], 10.0, 0.0) is None                      # one point


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    for name, fn in fns:
        if fn.__code__.co_argcount:
            with tempfile.TemporaryDirectory() as d:
                fn(Path(d))
        else:
            fn()
        print(f"ok  {name}")
    print(f"\n{len(fns)} passed")
