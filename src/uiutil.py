"""Tiny shared UI formatting helpers.

Lives in its own module so both dashboard.py and session_shelf.py can use them
without an import cycle (dashboard imports session_shelf, so session_shelf can't
import back from dashboard).
"""

from __future__ import annotations

import app_settings

# --- usage-bar colour thresholds --------------------------------------------
# The yellow ("warm") point tracks the user's own approaching-limit notification
# threshold: that setting is their statement of "this is when I start caring",
# so the bar agrees with it instead of using a second, unrelated number. It used
# to be a flat 50%, which lit up half the bar's range and made yellow mean
# nothing.
WARN_PCT_DEFAULT = 75   # approaching notifications off: no user signal to follow
WARN_PCT_CAP = 90       # a 99% notify threshold would leave no warning band


def warn_threshold(notify_pct: int | None) -> int:
    """The % at which a usage bar turns yellow.

    ``notify_pct`` is the user's approaching-limit threshold for that window, or
    None when approaching notifications are switched off.
    """
    if notify_pct is None:
        return WARN_PCT_DEFAULT
    return min(int(notify_pct), WARN_PCT_CAP)


def bar_warn_thresholds() -> tuple[int, int]:
    """``(session, weekly)`` yellow points resolved from the user's settings.

    The two windows have separate notification thresholds (the 5h window cycles
    fast and warns late; the 7d window is the scarce one and warns earlier), so
    their bars turn yellow at different points on purpose.
    """
    if not app_settings.get_approaching_enabled():
        return WARN_PCT_DEFAULT, WARN_PCT_DEFAULT
    return (warn_threshold(app_settings.get_approaching_session_pct()),
            warn_threshold(app_settings.get_approaching_weekly_pct()))


def format_minutes(mins: int) -> str:
    """'-', '5m', '2h 20m', '4d 06h' from a minutes count."""
    if mins <= 0:
        return "-"
    if mins < 60:
        return f"{mins}m"
    hours, m = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h {m:02d}m"
    days, h = divmod(hours, 24)
    return f"{days}d {h:02d}h"


def heat(pct: int, warn_at: int = WARN_PCT_DEFAULT) -> str:
    """Bar 'heat' bucket driving its chunk color: cool / warm / hot.

    Yellow starts at ``warn_at``; red starts halfway from there to 100, so the
    red band scales with however late the warning point is (warn 75 -> red 87,
    warn 90 -> red 95). At/over 100% the callers switch to the overage rendering
    instead, so nothing here needs to handle that.

    The ``max(1, ...)`` matters: with a warn point of 99 the halfway step rounds
    to 0, which would put red AT the yellow point and skip yellow entirely. Red
    is always at least one point above yellow, so the buckets can never invert —
    at the extreme the bar simply stays yellow until it hits overage.
    """
    warn_at = max(0, min(int(warn_at), 100))
    hot_at = warn_at + max(1, (100 - warn_at) // 2)
    if pct >= hot_at:
        return "hot"
    if pct >= warn_at:
        return "warm"
    return "cool"
