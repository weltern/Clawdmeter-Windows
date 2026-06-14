"""Unit tests for poller.sample_from_headers — the pure rate-limit-header →
UsageSample mapping, including the new overage dimension.

Importing poller pulls in PySide6 (QThread), so run headless via
QT_QPA_PLATFORM=offscreen. Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from poller import sample_from_headers  # noqa: E402


def test_parses_session_weekly_and_overage():
    now = 1_000_000.0
    h = {
        "anthropic-ratelimit-unified-5h-utilization": "0.13",
        "anthropic-ratelimit-unified-5h-reset": str(now + 2 * 3600),
        "anthropic-ratelimit-unified-5h-status": "allowed",
        "anthropic-ratelimit-unified-7d-utilization": "0.1",
        "anthropic-ratelimit-unified-7d-reset": str(now + 4 * 24 * 3600),
        "anthropic-ratelimit-unified-overage-utilization": "0.23",
        "anthropic-ratelimit-unified-overage-reset": str(now + 12 * 24 * 3600),
    }
    s = sample_from_headers(h, now)
    assert s.ok is True
    assert s.status == "allowed"
    assert s.session_pct == 13
    assert s.session_reset_minutes == 120
    assert s.weekly_pct == 10
    assert s.overage_pct == 23
    assert s.overage_reset_minutes == 12 * 24 * 60


def test_overage_absent_defaults_to_zero():
    # The common case: no overage headers -> overage is 0 (UI stays hidden).
    s = sample_from_headers(
        {"anthropic-ratelimit-unified-7d-utilization": "0.5"}, 1_000_000.0
    )
    assert s.overage_pct == 0
    assert s.overage_reset_minutes == 0
    assert s.weekly_pct == 50


def test_garbage_values_are_safe():
    s = sample_from_headers(
        {
            "anthropic-ratelimit-unified-5h-utilization": "n/a",
            "anthropic-ratelimit-unified-overage-reset": "not-a-number",
        },
        1_000_000.0,
    )
    assert s.session_pct == 0
    assert s.overage_reset_minutes == 0


def test_past_reset_clamps_to_zero():
    now = 1_000_000.0
    s = sample_from_headers(
        {"anthropic-ratelimit-unified-5h-reset": str(now - 500)}, now
    )
    assert s.session_reset_minutes == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
