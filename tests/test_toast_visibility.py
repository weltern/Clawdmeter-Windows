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

from PySide6.QtCore import QEvent, Qt  # noqa: E402
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


def test_clicking_the_body_activates_the_app():
    # The convention on both platforms: clicking a notification is the user
    # asking to see it, so it opens the app rather than just dismissing.
    t = dashboard.ResetToast()
    opened = []
    t.clicked.connect(lambda: opened.append(True))
    try:
        t.show_message("Session (5h)", "at 90% of your limit")
        _click(t)
        assert opened == [True]
    finally:
        t.deleteLater()


def test_the_dismiss_button_does_not_open_the_app():
    # ...and there must be a way to say "not now" that does NOT hand you a
    # window. Without it the whole toast is one "open the app" target.
    t = dashboard.ResetToast()
    opened = []
    t.clicked.connect(lambda: opened.append(True))
    try:
        t.show_message("Session (5h)", "at 90% of your limit")
        t._close_btn.click()
        assert opened == [], "dismissing must not activate the app"
    finally:
        t.deleteLater()


def test_dismiss_button_is_hover_revealed_and_never_reflows_the_text():
    t = dashboard.ResetToast()
    try:
        t.show_message("Session (5h)", "at 90% of your limit")
        assert not t._close_btn.isVisible(), "hidden until hovered"
        before = t.body.geometry()
        t.enterEvent(_enter_event())
        assert t._close_btn.isVisible()
        # It is positioned by hand, not in the layout, so revealing it must not
        # move the text it sits beside.
        assert t.body.geometry() == before
        t.leaveEvent(QEvent(QEvent.Type.Leave))
        assert not t._close_btn.isVisible()
    finally:
        t.deleteLater()


def test_dismiss_button_follows_a_theme_switch():
    # Styled via the app stylesheet (inherited from the toast card), not inline,
    # so a live theme change restyles it like everything else.
    import theme
    t = dashboard.ResetToast()
    try:
        assert "QToolButton#toastClose" in theme.build_qss(theme.get("Nord"))
        assert "QToolButton#toastClose" in theme.build_qss(theme.get("Daybreak"))
        assert not t._close_btn.styleSheet(), "must not carry an inline sheet"
    finally:
        t.deleteLater()


def test_alert_click_goes_to_the_dashboard_not_the_last_page():
    # Platform guidance: clicking a notification opens a view RELATED TO ITS
    # CONTENT. Landing on the Settings page someone happened to leave open is
    # the opposite. The view MODE stays their choice; only the page is forced.
    d = dashboard.Dashboard.__new__(dashboard.Dashboard)
    seen = {}

    class _Rail:
        def select(self, page):
            seen["rail"] = page

    d.nav_rail = _Rail()
    d._show_page = lambda idx: seen.__setitem__("page", idx)
    d._restore_view = lambda: seen.__setitem__("restored", True)

    dashboard.Dashboard._show_window_from_alert(d)
    assert seen["page"] == 0, "must land on the Dashboard page"
    assert seen["rail"] == 0, "nav rail highlight must follow the page"
    assert seen["restored"] is True, "view mode is still the user's choice"


def test_tray_click_still_leaves_you_where_you_were():
    # The distinction that makes the above correct: a tray click is "show me the
    # app", not "show me this alert", so it must NOT hijack the page.
    d = dashboard.Dashboard.__new__(dashboard.Dashboard)
    seen = {}
    d._show_page = lambda idx: seen.__setitem__("page", idx)
    d._restore_view = lambda: seen.__setitem__("restored", True)

    dashboard.Dashboard._show_window(d)
    assert "page" not in seen, "tray click must not change the page"
    assert seen["restored"] is True


def test_toast_rounds_its_corners_on_macos(monkeypatch):
    # The toast was the last square window once main/mini/compact/dialog were
    # all rounded. The QSS radius must match the native mask or the card's 1px
    # border is drawn square and clipped at the corners.
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    t = dashboard.ResetToast()
    try:
        radius = f"border-radius:{dashboard.ResetToast.MACOS_RADIUS}px"
        assert radius in t._card.styleSheet()
    finally:
        t.deleteLater()


def test_toast_card_stays_square_off_macos(monkeypatch):
    # Off macOS the window is opaque and square, so a rounded card would just
    # expose dark square corners behind it.
    monkeypatch.setattr(dashboard.sys, "platform", "win32")
    t = dashboard.ResetToast()
    try:
        # NB: the base QSS legitimately contains border-radius for buttons etc.,
        # so check for the toastRoot append specifically, not the substring.
        assert "QWidget#toastRoot{border-radius" not in t._card.styleSheet()
        assert t._card.styleSheet() == dashboard.STYLESHEET
    finally:
        t.deleteLater()


def test_toast_card_still_follows_a_theme_switch(monkeypatch):
    # The macOS radius append breaks apply_theme's exact-match swap — the same
    # trap that silently stopped the mini/compact views re-theming. The toast
    # gets the apply_theme_style() hook so the broadcast reaches it.
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    import theme
    t = dashboard.ResetToast()
    try:
        dashboard.apply_theme("Nord")
        t.apply_theme_style()
        assert theme.get("Nord").bg.lower() in t._card.styleSheet().lower()
        dashboard.apply_theme("Daybreak")
        t.apply_theme_style()
        assert theme.get("Daybreak").bg.lower() in t._card.styleSheet().lower()
        assert hasattr(t, "apply_theme_style")
    finally:
        dashboard.apply_theme(theme.DEFAULT_NAME)
        t.deleteLater()


def _enter_event():
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent
    p = QPointF(5, 5)
    return QEnterEvent(p, p, p)


def _click(widget):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QMouseEvent
    widget.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPointF(5, 5),
        widget.mapToGlobal(QPoint(5, 5)),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


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
