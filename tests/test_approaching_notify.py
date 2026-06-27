"""Unit tests for the pure approaching-limit / overage decision logic.

Run with `python -m pytest tests/ -q`, or directly:
`python tests/test_approaching_notify.py`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from approaching_notify import ApproachingNotifier  # noqa: E402


@dataclass
class _Sample:
    """Structural stand-in for poller.UsageSample (avoids importing Qt/httpx)."""

    session_pct: int
    weekly_pct: int
    status: str = "allowed"
    ok: bool = True


def obs(n, spct, wpct, *, enabled=True, sthr=90, wthr=80, overage=True, ok=True):
    return n.observe(
        _Sample(spct, wpct, ok=ok),
        enabled=enabled,
        session_threshold=sthr,
        weekly_threshold=wthr,
        overage_enabled=overage,
    )


def _primed():
    """A notifier primed below all thresholds, ready to detect crossings."""
    n = ApproachingNotifier()
    assert obs(n, 10, 10) == []  # first sample primes silently
    return n


def test_first_sample_primes_silently_even_when_above():
    n = ApproachingNotifier()
    assert obs(n, 95, 85) == []  # already-high at launch must not nag


def test_session_threshold_fires_once():
    n = _primed()
    ev = obs(n, 90, 10)
    assert len(ev) == 1
    assert ev[0].window == "Session (5h)" and ev[0].kind == "approaching"
    assert ev[0].pct == 90 and ev[0].threshold == 90
    assert obs(n, 92, 10) == []  # still above -> no repeat (edge-triggered)


def test_weekly_threshold_independent_of_session():
    n = _primed()
    ev = obs(n, 10, 80)
    assert len(ev) == 1 and ev[0].window == "Weekly (7d)"


def test_rearm_after_dropping_clear_then_recross():
    n = _primed()
    assert len(obs(n, 90, 10)) == 1
    assert obs(n, 70, 10) == []   # drops below 90 - margin -> re-arms
    assert len(obs(n, 91, 10)) == 1  # crossing again fires


def test_margin_blocks_rearm_on_jitter():
    n = _primed()
    assert len(obs(n, 90, 10)) == 1
    assert obs(n, 86, 10) == []   # 86 >= 90 - 5 -> NOT re-armed
    assert obs(n, 90, 10) == []   # so bouncing back to 90 doesn't re-fire


def test_overage_fires_when_crossing_100():
    n = _primed()
    ev = obs(n, 100, 10)
    assert len(ev) == 1 and ev[0].kind == "overage" and ev[0].threshold == 100


def test_jump_past_threshold_into_overage_is_single_event():
    n = _primed()
    ev = obs(n, 120, 10)  # 10 -> 120 in one step
    assert len(ev) == 1 and ev[0].kind == "overage"


def test_overage_suppressed_when_disabled():
    n = _primed()
    assert obs(n, 100, 10, overage=False) == []


def test_both_windows_can_fire_same_poll():
    n = _primed()
    ev = obs(n, 95, 85)
    kinds = {e.window for e in ev}
    assert kinds == {"Session (5h)", "Weekly (7d)"}


def test_disabled_fires_nothing_but_still_rearms():
    n = _primed()
    assert obs(n, 95, 10, enabled=False) == []   # disabled: silent
    # Not marked warned while disabled, so enabling then crossing fires.
    assert len(obs(n, 96, 10, enabled=True)) == 1


def test_error_sample_ignored():
    n = _primed()
    assert len(obs(n, 90, 10)) == 1
    assert obs(n, 0, 0, ok=False) == []   # error sample: no event, no state change
    assert obs(n, 92, 10) == []           # still warned -> no spurious re-fire


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
