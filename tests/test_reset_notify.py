"""Unit tests for the pure reset-notification decision logic.

Run with `python -m pytest tests/ -q`, or directly: `python tests/test_reset_notify.py`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from reset_notify import ResetNotifier  # noqa: E402


@dataclass
class _Sample:
    """Structural stand-in for poller.UsageSample (avoids importing Qt/httpx)."""

    session_pct: int
    weekly_pct: int
    status: str = "allowed"
    ok: bool = True


def sample(spct, wpct, status="allowed", ok=True):
    return _Sample(spct, wpct, status, ok)


def test_session_reset_high_prior_notifies():
    n = ResetNotifier()
    n.observe(sample(90, 10))
    d = n.observe(sample(2, 10))
    assert d.session_reset and d.notify and "session" in d.reasons
    assert not d.weekly_reset


def test_session_reset_low_prior_does_not_notify():
    n = ResetNotifier()
    n.observe(sample(40, 10))
    d = n.observe(sample(2, 10))
    assert d.session_reset and not d.notify and d.reasons == ()


def test_no_reset_no_notify():
    n = ResetNotifier()
    n.observe(sample(90, 10))
    d = n.observe(sample(92, 10))
    assert not d.any_reset and not d.notify


def test_error_sample_does_not_corrupt_state():
    n = ResetNotifier()
    n.observe(sample(90, 10))
    mid = n.observe(sample(0, 0, status="error", ok=False))
    assert not mid.any_reset and not mid.notify  # error ignored
    # Recovery is compared against the pre-error baseline (90), not the error 0.
    d = n.observe(sample(85, 10))
    assert d.session_reset and d.notify  # 90 -> 85 is a 5pt drop, prior 90 >= 75


def test_fires_once_per_reset():
    n = ResetNotifier()
    n.observe(sample(90, 10))
    first = n.observe(sample(2, 10))
    second = n.observe(sample(2, 10))  # steady low after reset
    assert first.session_reset and not second.session_reset


def test_weekly_path():
    n = ResetNotifier()
    n.observe(sample(10, 95))
    d = n.observe(sample(10, 3))
    assert d.weekly_reset and d.notify and "weekly" in d.reasons
    assert not d.session_reset


def test_status_gated_low_pct_still_notifies():
    n = ResetNotifier()
    n.observe(sample(40, 10, status="rejecting"))
    d = n.observe(sample(2, 10))
    assert d.session_reset and d.notify  # low pct but was being throttled


def test_weekly_not_gated_by_session_status():
    # `status` is the 5h (session) status only — a 5h throttle must NOT make a
    # low-utilization weekly reset notify (no cross-axis false positive).
    n = ResetNotifier()
    n.observe(sample(10, 30, status="rejecting"))
    d = n.observe(sample(10, 20))
    assert d.weekly_reset and not d.notify and "weekly" not in d.reasons


def test_first_sample_no_decision():
    n = ResetNotifier()
    d = n.observe(sample(90, 10))
    assert not d.any_reset and not d.notify


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
