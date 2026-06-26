"""Pure helpers for the idle poll back-off decision.

Kept Qt-free and side-effect-free so the cadence logic is unit-testable; the
dashboard owns the actual poller + timestamps and calls these to decide.

The signal is *local* session activity (the newest transcript event the app has
seen). Usage % itself is account-wide, so backing off only slows how fast usage
driven elsewhere shows up — never how it's computed. That's why the back-off
slows rather than stops, and is opt-in.
"""

from __future__ import annotations


def is_idle(
    now: float,
    last_activity_ts: float | None,
    *,
    enabled: bool,
    idle_after_secs: float,
) -> bool:
    """True if the back-off is on and no session has been active recently."""
    if not enabled or last_activity_ts is None:
        return False
    return (now - last_activity_ts) >= idle_after_secs


def target_interval(
    now: float,
    last_activity_ts: float | None,
    *,
    enabled: bool,
    normal: int,
    idle_interval: int,
    idle_after_secs: float,
) -> int:
    """The poll interval to use right now: the idle interval when idle, else the
    normal interval. Never faster than normal, so a misconfigured idle interval
    can't speed polling up."""
    if is_idle(now, last_activity_ts, enabled=enabled, idle_after_secs=idle_after_secs):
        return max(normal, idle_interval)
    return normal
