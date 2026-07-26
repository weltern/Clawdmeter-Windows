"""The alert toast must actually be visible when an alert fires.

An alert matters precisely when Clawdmeter is NOT the frontmost app — so the
toast has to survive the app losing focus, and it must not need the main window
to be raised in order to notify anyone.

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import app_settings  # noqa: E402
import dashboard  # noqa: E402

_app = QApplication.instance() or QApplication([])


def test_toast_is_not_a_tool_window_on_macos(monkeypatch):
    # Regression: macOS auto-hides a Qt.Tool window when the app loses focus, so
    # the toast disappeared exactly when it was needed. MiniWidget and
    # CompactView already drop Qt.Tool on darwin; the toast never got the fix.
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    t = dashboard.ResetToast()
    try:
        flags = t.windowFlags()
        # Qt.Tool is a COMPOSITE value (Popup|Dialog == 11) that shares bits with
        # Qt.Window (1), so a plain `flags & Qt.Tool` is truthy for any window.
        # The window type has to be compared against the type mask.
        wtype = flags & Qt.WindowType_Mask
        assert wtype != Qt.Tool, "Qt.Tool auto-hides on macOS focus loss"
        assert wtype == Qt.Window
        assert flags & Qt.WindowStaysOnTopHint
        assert flags & Qt.FramelessWindowHint
        # ...and it still must never steal focus from what the user is doing.
        assert t.testAttribute(Qt.WA_ShowWithoutActivating)
    finally:
        t.deleteLater()


def test_toast_keeps_tool_flag_off_macos(monkeypatch):
    # Off macOS, Qt.Tool is what keeps the toast out of the taskbar/alt-tab.
    monkeypatch.setattr(dashboard.sys, "platform", "win32")
    t = dashboard.ResetToast()
    try:
        flags = t.windowFlags()
        assert (flags & Qt.WindowType_Mask) == Qt.Tool
        assert flags & Qt.WindowStaysOnTopHint
        assert t.testAttribute(Qt.WA_ShowWithoutActivating)
    finally:
        t.deleteLater()


def test_toast_opts_into_showing_over_fullscreen_spaces(monkeypatch):
    # Regression: macOS Spaces are isolated. A fullscreen app gets its own
    # Space, and a window that has not opted in cannot be drawn there — so
    # showing the toast SWITCHED Spaces, which looked like the fullscreen app
    # being yanked out. Observed on the M2 with Safari fullscreen.
    calls = []
    monkeypatch.setattr(dashboard.macos_window, "make_overlay",
                        lambda w: calls.append(w) or True)
    t = dashboard.ResetToast()
    try:
        t.show()
        assert calls, "showEvent must opt the toast into all Spaces"
        t.hide()
        t.show()
        assert len(calls) >= 2, "must re-apply on every show, not latch once"
    finally:
        t.deleteLater()


def test_make_overlay_sets_the_two_flags_that_matter():
    import macos_window
    # CanJoinAllSpaces alone still will not draw on a fullscreen Space;
    # FullScreenAuxiliary is the one that permits it. Guard both constants.
    assert macos_window._COLLECTION_CAN_JOIN_ALL_SPACES == 1
    assert macos_window._COLLECTION_FULLSCREEN_AUXILIARY == 256


def test_make_overlay_noops_off_darwin(monkeypatch):
    import macos_window
    monkeypatch.setattr(macos_window.sys, "platform", "win32")
    assert macos_window.make_overlay(object()) is False


def test_raising_the_main_window_is_opt_in(monkeypatch):
    # The toast plus the tray flash are the notification. Raising the main
    # window steals focus and fires on frequent approaching-limit alerts too,
    # not just the rare reset it was written for — so it defaults off.
    monkeypatch.setattr(app_settings, "_settings", lambda: _Blank())
    assert app_settings.get_reset_notify_popup() is False
    # The toast itself stays on by default — it is the notification.
    assert app_settings.get_reset_notify_toast() is True


def test_a_stored_preference_still_wins(monkeypatch):
    # Flipping the default must not override someone who deliberately enabled it.
    monkeypatch.setattr(app_settings, "_settings", lambda: _Stored(True))
    assert app_settings.get_reset_notify_popup() is True
    monkeypatch.setattr(app_settings, "_settings", lambda: _Stored("true"))
    assert app_settings.get_reset_notify_popup() is True


class _Blank:
    """QSettings stand-in with nothing stored, so the shipped default wins."""

    def value(self, _key, default=None):
        return default


class _Stored:
    def __init__(self, v):
        self._v = v

    def value(self, _key, default=None):
        return self._v


if __name__ == "__main__":
    print("run via pytest (these need monkeypatch)")
