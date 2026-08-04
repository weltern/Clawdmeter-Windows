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


def test_wayland_shows_the_box_unticked_without_destroying_the_setting(monkeypatch):
    """A ticked-but-greyed box asserts a state the window is not in.

    It also cannot be cleared, since the control is disabled. So on Wayland it
    renders unticked -- but the STORED preference must survive untouched, or
    merely opening Settings on a Wayland session would wipe the user's choice
    for their next X11 login.
    """
    import app_settings
    monkeypatch.setattr(app_settings, "get_always_on_top", lambda: True)
    wrote = []
    monkeypatch.setattr(app_settings, "set_always_on_top", lambda v: wrote.append(v))

    p, host = _panel(monkeypatch, True)
    try:
        assert p.aot_check.isChecked() is False, \
            "a disabled control must not display a state it cannot honour"
        assert wrote == [], \
            f"opening Settings on Wayland overwrote the stored setting: {wrote}"
    finally:
        p.deleteLater()
        host.deleteLater()


def test_x11_still_reflects_the_stored_setting(monkeypatch):
    import app_settings
    monkeypatch.setattr(app_settings, "get_always_on_top", lambda: True)
    p, host = _panel(monkeypatch, False)
    try:
        assert p.aot_check.isChecked() is True
        assert p.aot_check.isEnabled() is True
    finally:
        p.deleteLater()
        host.deleteLater()


# --- the X11 compositing probe: bounded, and not cached forever --------------

def test_a_hanging_x11_probe_cannot_freeze_the_ui(monkeypatch):
    """XOpenDisplay is a blocking connect against whatever $DISPLAY names.

    Normally instant against a local socket, but nothing bounds it if $DISPLAY
    points somewhere unreachable -- a stale SSH-forwarded display, say. This is
    called on the GUI thread to answer a cosmetic question about popup corners,
    so it must never be able to hang the app.
    """
    import time as _time
    monkeypatch.setattr(uiutil, "_COMPOSITING_PROBE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(uiutil.sys, "platform", "linux")
    monkeypatch.setattr(uiutil, "is_wayland", lambda: False)

    def _hang():
        _time.sleep(30)
        return False
    monkeypatch.setattr(uiutil, "_probe_x11_compositing", _hang)
    uiutil.linux_compositing.cache_clear()

    started = _time.monotonic()
    result = uiutil.linux_compositing()
    elapsed = _time.monotonic() - started
    uiutil.linux_compositing.cache_clear()

    assert elapsed < 5, f"the probe blocked for {elapsed:.1f}s -- the UI froze"
    assert result is True, "on timeout, assume composited (the prettier branch)"


def test_the_answer_is_rechecked_after_the_ttl(monkeypatch):
    """Compositing genuinely toggles at runtime -- a user turns their
    compositor off for a game, or KWin restarts. Caching for the whole process
    would leave the popup corners wrong until the app was restarted."""
    monkeypatch.setattr(uiutil.sys, "platform", "linux")
    monkeypatch.setattr(uiutil, "is_wayland", lambda: False)
    calls = []
    answer = [True]
    monkeypatch.setattr(uiutil, "_probe_x11_compositing",
                        lambda: (calls.append(1), answer[0])[1])
    clock = [1000.0]
    monkeypatch.setattr(uiutil.time, "monotonic", lambda: clock[0])
    uiutil.linux_compositing.cache_clear()
    try:
        assert uiutil.linux_compositing() is True
        assert uiutil.linux_compositing() is True
        assert len(calls) == 1, "a second call within the TTL must use the cache"

        # A fixed advance, deliberately NOT _COMPOSITING_TTL_S + 1: reading the
        # constant would make this test adapt to any value, so it would pass
        # even against a cache that never expires. 60s is the outer bound of
        # what counts as "noticed promptly" for a cosmetic corner radius.
        clock[0] += 60
        answer[0] = False
        assert uiutil.linux_compositing() is False, \
            "the compositor was turned off and the app never noticed"
        assert len(calls) == 2
    finally:
        uiutil.linux_compositing.cache_clear()
