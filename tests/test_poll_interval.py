"""Unit tests for the configurable poll-interval settings logic.

Run with `python -m pytest tests/ -q`, or directly: `python tests/test_poll_interval.py`.

QSettings is isolated to a throwaway INI file for each test, so these never
touch the real HKCU\\Software\\Clawdmeter store.
"""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QSettings  # noqa: E402

import app_settings  # noqa: E402


@contextmanager
def _isolated_settings():
    """Point app_settings at one throwaway INI-backed QSettings for the test.

    A single shared instance (rather than app_settings' usual fresh-per-call)
    keeps reads and writes deterministic without depending on disk-flush timing.
    """
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


def test_clamp_bounds():
    c = app_settings._clamp_poll_interval
    assert app_settings.POLL_INTERVAL_MIN == 10
    assert app_settings.POLL_INTERVAL_MAX == 600
    assert c(5) == 10          # below floor -> floor
    assert c(10) == 10
    assert c(60) == 60
    assert c(600) == 600
    assert c(99999) == 600     # above ceiling -> ceiling
    assert c(-3) == 10


def test_default_when_unset():
    with _isolated_settings():
        assert app_settings.get_poll_interval() == app_settings.POLL_INTERVAL_DEFAULT == 60


def test_set_clamps_persists_and_returns_stored():
    with _isolated_settings():
        assert app_settings.set_poll_interval(5) == 10        # below floor
        assert app_settings.get_poll_interval() == 10
        assert app_settings.set_poll_interval(45) == 45       # in range
        assert app_settings.get_poll_interval() == 45
        assert app_settings.set_poll_interval(99999) == 600   # above ceiling
        assert app_settings.get_poll_interval() == 600


def test_get_clamps_value_stored_out_of_range():
    # A value persisted out of range (e.g. by an older build) is clamped on read.
    with _isolated_settings() as store:
        store.setValue(app_settings.KEY_POLL_INTERVAL, 5)
        assert app_settings.get_poll_interval() == 10


def test_get_falls_back_on_garbage():
    with _isolated_settings() as store:
        store.setValue(app_settings.KEY_POLL_INTERVAL, "not-a-number")
        assert app_settings.get_poll_interval() == app_settings.POLL_INTERVAL_DEFAULT


def test_string_value_round_trips():
    # QSettings can hand values back as strings; ensure defensive int() parsing works.
    with _isolated_settings() as store:
        store.setValue(app_settings.KEY_POLL_INTERVAL, "120")
        assert app_settings.get_poll_interval() == 120


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
