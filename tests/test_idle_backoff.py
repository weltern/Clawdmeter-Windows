"""Tests for the idle poll back-off: the pure cadence logic + its settings.

QSettings is isolated to a throwaway INI per accessor test, so these never touch
the real HKCU\\Software\\Clawdmeter store (in particular, never flip the real
idle-back-off enable flag).

Run with `python -m pytest tests/ -q`, or directly:
`python tests/test_idle_backoff.py`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QSettings  # noqa: E402

import app_settings  # noqa: E402
import poll_cadence  # noqa: E402


@contextmanager
def _isolated_settings():
    fd, path = tempfile.mkstemp(suffix=".ini")
    os.close(fd)
    store = QSettings(path, QSettings.IniFormat)
    real = app_settings._settings
    app_settings._settings = lambda: store
    try:
        yield store
    finally:
        app_settings._settings = real
        store.sync()
        os.remove(path)


NOW = 1_000_000.0  # fixed clock; Date.now() isn't available and isn't needed


# --- pure cadence -----------------------------------------------------------

def test_is_idle_false_when_disabled():
    assert poll_cadence.is_idle(NOW, NOW - 9999, enabled=False, idle_after_secs=900) is False


def test_is_idle_false_without_activity_timestamp():
    assert poll_cadence.is_idle(NOW, None, enabled=True, idle_after_secs=900) is False


def test_is_idle_respects_window():
    assert poll_cadence.is_idle(NOW, NOW - 800, enabled=True, idle_after_secs=900) is False
    assert poll_cadence.is_idle(NOW, NOW - 900, enabled=True, idle_after_secs=900) is True
    assert poll_cadence.is_idle(NOW, NOW - 5000, enabled=True, idle_after_secs=900) is True


def _target(active_ago, *, enabled=True, normal=60, idle_interval=300, after=900):
    return poll_cadence.target_interval(
        NOW, NOW - active_ago, enabled=enabled, normal=normal,
        idle_interval=idle_interval, idle_after_secs=after)


def test_target_normal_when_active():
    assert _target(10) == 60


def test_target_backs_off_when_idle():
    assert _target(1000) == 300


def test_target_never_faster_than_normal():
    # A misconfigured idle interval below normal must not speed polling up.
    assert _target(1000, normal=120, idle_interval=30) == 120


def test_target_normal_when_disabled_even_if_stale():
    assert _target(99999, enabled=False) == 60


# --- settings accessors -----------------------------------------------------

def test_enable_defaults_off_and_round_trips():
    with _isolated_settings():
        assert app_settings.get_idle_backoff_enabled() is False
        app_settings.set_idle_backoff_enabled(True)
        assert app_settings.get_idle_backoff_enabled() is True


def test_idle_interval_default_and_clamp():
    with _isolated_settings():
        assert app_settings.get_idle_interval() == app_settings.IDLE_INTERVAL_DEFAULT == 300
        assert app_settings.set_idle_interval(5) == app_settings.IDLE_INTERVAL_MIN == 60
        assert app_settings.set_idle_interval(99999) == app_settings.IDLE_INTERVAL_MAX == 3600
        assert app_settings.set_idle_interval(450) == 450


def test_idle_after_default_and_clamp():
    with _isolated_settings():
        assert app_settings.get_idle_after_minutes() == app_settings.IDLE_AFTER_DEFAULT == 15
        assert app_settings.set_idle_after_minutes(0) == app_settings.IDLE_AFTER_MIN == 1
        assert app_settings.set_idle_after_minutes(9999) == app_settings.IDLE_AFTER_MAX == 240
        assert app_settings.set_idle_after_minutes(30) == 30


def test_idle_settings_fall_back_on_garbage():
    with _isolated_settings() as store:
        store.setValue(app_settings.KEY_IDLE_INTERVAL, "nope")
        store.setValue(app_settings.KEY_IDLE_AFTER_MINUTES, "nope")
        assert app_settings.get_idle_interval() == app_settings.IDLE_INTERVAL_DEFAULT
        assert app_settings.get_idle_after_minutes() == app_settings.IDLE_AFTER_DEFAULT


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
