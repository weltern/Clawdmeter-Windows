"""Tests for the persisted usage history (K2).

Uses tmp paths + an explicit persist interval, so nothing touches the real
app-data file. No Qt widgets needed.

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import sys
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from usage_history import UsageHistory, snapshot  # noqa: E402


def mksample(ok=True, ts=1000.0, s=10, w=20, t5=100, t7=200, usd=5.0, plan="max"):
    return types.SimpleNamespace(
        ok=ok, timestamp=ts, session_pct=s, weekly_pct=w,
        tokens_5h=t5, tokens_7d=t7, extra_usage_used_usd=usd, plan_tier=plan,
    )


def test_record_rings_and_persists(tmp_path):
    h = UsageHistory(path=tmp_path / "u.jsonl", persist_interval=0)
    h.record(mksample(ts=1000.0, usd=5.0))
    assert len(h.ring) == 1
    recs = h.load()
    assert len(recs) == 1 and recs[0]["usd"] == 5.0


def test_failed_sample_ignored(tmp_path):
    h = UsageHistory(path=tmp_path / "u.jsonl", persist_interval=0)
    h.record(mksample(ok=False))
    assert len(h.ring) == 0
    assert h.load() == []


def test_persist_is_throttled(tmp_path):
    h = UsageHistory(path=tmp_path / "u.jsonl", persist_interval=300)
    h.record(mksample(ts=1000.0))   # first -> persisted
    h.record(mksample(ts=1100.0))   # +100s within window -> ring only
    h.record(mksample(ts=1400.0))   # +400s -> persisted
    assert len(h.ring) == 3
    assert len(h.load()) == 2       # only the two past-throttle writes hit disk


def test_load_since(tmp_path):
    h = UsageHistory(path=tmp_path / "u.jsonl", persist_interval=0)
    for ts in (1000.0, 2000.0, 3000.0):
        h.record(mksample(ts=ts))
    assert len(h.load(since_ts=2000.0)) == 2


def test_snapshot_is_pii_free():
    assert set(snapshot(mksample())) == {"ts", "s", "w", "t5", "t7", "usd", "plan"}


def test_prune_drops_old_on_reopen(tmp_path):
    p = tmp_path / "u.jsonl"
    h = UsageHistory(path=p, persist_interval=0, retention_days=30)
    h.record(mksample(ts=time.time() - 60 * 86400))   # stale
    recent = time.time() - 1 * 86400
    h.record(mksample(ts=recent))
    recs = UsageHistory(path=p, persist_interval=0, retention_days=30).load()
    assert len(recs) == 1 and recs[0]["ts"] == round(recent, 1)


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    for name, fn in fns:
        with tempfile.TemporaryDirectory() as d:
            (fn(Path(d)) if fn.__code__.co_argcount else fn())
        print(f"ok  {name}")
    print(f"\n{len(fns)} passed")
