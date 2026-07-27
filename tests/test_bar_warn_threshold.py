"""The usage bars' yellow/red points.

Yellow ("warm") follows the user's own approaching-limit notification threshold
for that window — that setting is their statement of "this is when I care", so
the bar agrees with it rather than using a second, unrelated number. Red ("hot")
sits halfway from there to 100 so there is always a warning band. Previously
both were flat (warm at 50, hot at 80), which lit up half the bar's range and
would have inverted once yellow moved past 80.

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import app_settings  # noqa: E402
import uiutil  # noqa: E402
from uiutil import (WARN_PCT_CAP, WARN_PCT_DEFAULT,  # noqa: E402
                    bar_warn_thresholds, heat, warn_threshold)


# --- warn_threshold ---------------------------------------------------------

def test_notifications_off_falls_back_to_75_not_50():
    # The old flat 50% is what this change exists to remove.
    assert warn_threshold(None) == 75
    assert WARN_PCT_DEFAULT == 75


def test_follows_the_users_threshold():
    assert warn_threshold(60) == 60
    assert warn_threshold(75) == 75
    assert warn_threshold(80) == 80


def test_capped_at_90_so_a_99_percent_setting_still_warns():
    # A 99% notify threshold would otherwise leave a 1-point yellow band.
    assert warn_threshold(99) == WARN_PCT_CAP == 90
    assert warn_threshold(95) == 90
    assert warn_threshold(90) == 90


# --- heat buckets -----------------------------------------------------------

def test_bands_with_the_default_warn_point():
    assert heat(0) == "cool"
    assert heat(74) == "cool"
    assert heat(75) == "warm"     # yellow starts exactly at the threshold
    assert heat(86) == "warm"
    assert heat(87) == "hot"      # 75 + (100-75)//2
    assert heat(99) == "hot"


def test_red_band_exists_across_every_reachable_warn_point():
    # warn_threshold caps at 90, so this is the full range the app can produce.
    for warn in range(50, WARN_PCT_CAP + 1):
        band = [heat(p, warn) for p in range(0, 100)]
        assert band[warn - 1] == "cool", warn
        assert band[warn] == "warm", warn
        assert "hot" in band, f"no red band for warn={warn}"
        order = [b for i, b in enumerate(band) if i == 0 or b != band[i - 1]]
        assert order == ["cool", "warm", "hot"], (warn, order)


def test_buckets_never_invert_even_at_absurd_warn_points():
    # heat() is generic, so guard the values warn_threshold would never emit:
    # red must never appear before yellow. At warn=99 there is no room for a red
    # band below 100, so the bar stays yellow until overage takes over.
    rank = {"cool": 0, "warm": 1, "hot": 2}
    for warn in range(0, 101):
        # The real invariant: heat only ever escalates as pct rises. A warn point
        # of 0 legitimately starts at "warm" (everything is a warning), so the
        # sequence need not start at "cool" — it just must never go backwards.
        seq = [rank[heat(p, warn)] for p in range(0, 100)]
        assert all(b >= a for a, b in zip(seq, seq[1:])), warn
    assert heat(98, 99) == "cool"      # still below the warn point
    assert heat(99, 99) == "warm"      # yellow, never red-before-yellow


# --- settings resolution ----------------------------------------------------

def test_thresholds_are_per_window(monkeypatch):
    # 5h and 7d have separate notification thresholds, so their bars turn
    # yellow at different points on purpose.
    monkeypatch.setattr(app_settings, "get_approaching_enabled", lambda: True)
    monkeypatch.setattr(app_settings, "get_approaching_session_pct", lambda: 90)
    monkeypatch.setattr(app_settings, "get_approaching_weekly_pct", lambda: 80)
    assert bar_warn_thresholds() == (90, 80)


def test_disabled_notifications_use_the_default_for_both(monkeypatch):
    monkeypatch.setattr(app_settings, "get_approaching_enabled", lambda: False)
    monkeypatch.setattr(app_settings, "get_approaching_session_pct", lambda: 55)
    monkeypatch.setattr(app_settings, "get_approaching_weekly_pct", lambda: 55)
    assert bar_warn_thresholds() == (75, 75)


def test_a_99_percent_setting_is_capped_through_the_resolver(monkeypatch):
    monkeypatch.setattr(app_settings, "get_approaching_enabled", lambda: True)
    monkeypatch.setattr(app_settings, "get_approaching_session_pct", lambda: 99)
    monkeypatch.setattr(app_settings, "get_approaching_weekly_pct", lambda: 99)
    assert bar_warn_thresholds() == (90, 90)


def test_shipping_defaults_produce_a_later_yellow_than_before(monkeypatch):
    # Out of the box (notifications on, 90/80) the 5h bar warms at 90 and the
    # 7d at 80 — both far later than the old flat 50.
    monkeypatch.setattr(app_settings, "get_approaching_enabled", lambda: True)
    monkeypatch.setattr(app_settings, "_settings", lambda: _Blank())
    s_warn, w_warn = bar_warn_thresholds()
    assert (s_warn, w_warn) == (app_settings.APPROACHING_SESSION_DEFAULT,
                                app_settings.APPROACHING_WEEKLY_DEFAULT) == (90, 80)
    assert heat(60, s_warn) == "cool"      # was "warm" under the old flat 50
    assert heat(60, w_warn) == "cool"


class _Blank:
    """A QSettings stand-in with nothing stored, so the shipped defaults win."""

    def value(self, _key, default=None):
        return default


# --- the renderers actually pass a threshold through ------------------------

def test_bar_renderers_use_the_supplied_threshold():
    import session_shelf

    seen = []

    class _Bar:
        def set_values(self, value, overage, h):
            seen.append((value, overage, h))

    class _Label:
        def setText(self, _t):
            pass

    # 60% is "cool" at a 90 threshold but "warm" at the old flat 50.
    session_shelf.apply_overage_bar(_Label(), _Label(), _Bar(), "SESSION", 60, 90)
    assert seen[-1] == (60, 0, "cool")
    session_shelf.apply_overage_bar(_Label(), _Label(), _Bar(), "SESSION", 60, 50)
    assert seen[-1] == (60, 0, "warm")


def test_overage_is_visually_distinct_from_the_red_band():
    # The "hot" band below 100% is p.danger; overage used to be p.danger too, so
    # 95% and 105% painted the identical colour on every theme except the shipped
    # default. Overage now uses the darker danger_strong.
    import session_shelf
    import theme

    for name in theme.names():
        p = theme.get(name)
        if p is theme.MIDNIGHT_SALMON:
            continue                     # hand-tuned ramp, deliberately exempt
        ramp = session_shelf._heat_ramp(p)
        over = session_shelf._overage_color(p)
        assert over != ramp["hot"], f"{name}: overage indistinguishable from hot"

    # ...and the default keeps its own pair, also distinct.
    sal = theme.MIDNIGHT_SALMON
    assert session_shelf._overage_color(sal) != session_shelf._heat_ramp(sal)["hot"]


def test_overage_still_wins_over_the_heat_bucket():
    import session_shelf

    seen = []

    class _Bar:
        def set_values(self, value, overage, h):
            seen.append((value, overage, h))

    class _Label:
        def setText(self, _t):
            pass

    session_shelf.apply_overage_bar(_Label(), _Label(), _Bar(), "SESSION", 120, 90)
    assert seen[-1] == (0, 20, "cool")   # overage rendering, not a heat bucket


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} collected")
