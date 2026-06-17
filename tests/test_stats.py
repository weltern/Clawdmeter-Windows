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
