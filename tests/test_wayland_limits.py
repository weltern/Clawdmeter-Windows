"""Controls Wayland cannot honour must not pretend to work.

Confirmed on a real Wayland compositor 2026-07-27 (nested weston, verified by a
live client connection with DISPLAY unset so Qt could not fall back to xcb):
Qt.WindowStaysOnTopHint is ignored for every view. That is a deliberate
property of the protocol -- a client may not raise itself above others -- not a
Qt gap, and no flag or plugin changes it.

So the setting is disabled there rather than silently doing nothing. These
tests exist because that branch only runs on Wayland, which no CI and no
developer machine here exercises: without them a typo in it would surface only
on a user's desktop. (One nearly did -- `uiutil.is_wayland()` was written where
uiutil is imported by name, not as a module, which would have been a NameError
reachable only on Wayland.)
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import app_settings  # noqa: E402
import dashboard  # noqa: E402
import uiutil  # noqa: E402

_app = QApplication.instance() or QApplication([])
app_settings.set_theme = lambda name: None


def _panel(monkeypatch, wayland):
    # Patch the name dashboard actually calls. dashboard does
    # `from uiutil import is_wayland`, so patching uiutil.is_wayland alone
    # would not affect it -- and that asymmetry is worth pinning down.
    monkeypatch.setattr(dashboard, "is_wayland", lambda: wayland)
    host = QWidget()
    p = dashboard.SettingsPanel(host, lambda *_a: None, lambda *_a: None)
    return p, host


@pytest.mark.parametrize("wayland,enabled", [(True, False), (False, True)])
def test_always_on_top_is_disabled_only_on_wayland(monkeypatch, wayland, enabled):
    p, host = _panel(monkeypatch, wayland)
    try:
        assert p.aot_check.isEnabled() is enabled, (
            "always-on-top must be disabled on Wayland (it cannot work) and "
            "enabled everywhere else (it does)")
    finally:
        p.deleteLater()
        host.deleteLater()


def test_wayland_explains_itself_rather_than_going_quiet(monkeypatch):
    p, host = _panel(monkeypatch, True)
    try:
        tip = p.aot_check.toolTip()
        assert tip, "a greyed-out control with no explanation reads as a bug"
        assert "Wayland" in tip
    finally:
        p.deleteLater()
        host.deleteLater()


def test_no_tooltip_clutter_off_wayland(monkeypatch):
    p, host = _panel(monkeypatch, False)
    try:
        assert not p.aot_check.toolTip()
    finally:
        p.deleteLater()
        host.deleteLater()


def test_is_wayland_asks_qt_not_the_environment(monkeypatch):
    """WAYLAND_DISPLAY can be set while Qt is talking xcb through XWayland.

    What matters is the protocol our windows actually speak, so the helper
    reads QGuiApplication.platformName(). Faking the env must not fool it.
    """
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    # The test app really is running offscreen, so the honest answer is False.
    assert uiutil.is_wayland() is False

    class _FakeApp:
        @staticmethod
        def platformName():
            return "wayland"

    monkeypatch.setattr("PySide6.QtGui.QGuiApplication.instance",
                        staticmethod(lambda: _FakeApp()))
    assert uiutil.is_wayland() is True
