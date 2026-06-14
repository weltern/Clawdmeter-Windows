"""Tiny shared UI formatting helpers.

Lives in its own module so both dashboard.py and session_shelf.py can use them
without an import cycle (dashboard imports session_shelf, so session_shelf can't
import back from dashboard).
"""

from __future__ import annotations


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


def heat(pct: int) -> str:
    """Bar 'heat' bucket driving its chunk color: cool / warm / hot."""
    if pct >= 80:
        return "hot"
    if pct >= 50:
        return "warm"
    return "cool"
