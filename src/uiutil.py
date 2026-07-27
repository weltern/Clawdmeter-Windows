"""Tiny shared UI formatting helpers.

Lives in its own module so both dashboard.py and session_shelf.py can use them
without an import cycle (dashboard imports session_shelf, so session_shelf can't
import back from dashboard).
"""

from __future__ import annotations

import sys

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


from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

import macos_window


class ThemedPopup(QWidget):
    """A small drop-down list that renders SOLID on macOS.

    QMenu cannot be made opaque there. The macOS style paints a menu's panel
    with an NSVisualEffectView vibrancy material, and four separate attempts
    failed on hardware: a stylesheet background, WA_TranslucentBackground /
    WA_OpaquePaintEvent, a Fusion style instance, painting the popup's NSWindow
    opaque via pyobjc, and a QProxyStyle intercepting PE_PanelMenu. The
    instrumentation was unambiguous — the native window WAS opaque
    (paint_window=True, translucent=False) and Qt drew transparency over it.

    So this is not a menu. It is built exactly like the alert toast, which is
    verified to render solid on this hardware: a frameless top-level window
    holding a card that carries the app stylesheet. Same construction, same
    result, and it themes for free.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Qt.Popup, not Qt.Window: a popup grabs the mouse, so clicking ANYWHERE
        # outside dismisses it (and Esc closes it) without us tracking focus,
        # and it cannot be resized by the user. The transparency this class
        # exists to avoid came from QMenu's style painting its panel, not from
        # the window type, so a popup is safe here.
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._card = QWidget(objectName="popupRoot")
        self._card.setAttribute(Qt.WA_StyledBackground, True)
        outer.addWidget(self._card)
        self._col = QVBoxLayout(self._card)
        self._col.setContentsMargins(5, 5, 5, 5)
        self._col.setSpacing(2)
        self._buttons: list[QPushButton] = []
        self.apply_theme_style()

    def apply_theme_style(self) -> None:
        """Re-apply the (theme-updated) stylesheet. On macOS the card carries a
        radius append to match round_window, which breaks apply_theme's
        exact-match swap — hence the hook, same as the toast."""
        import theme
        qss = theme.build_qss(theme.active())
        if sys.platform == "darwin":
            qss += "\nQWidget#popupRoot{border-radius:8px}"
        self._card.setStyleSheet(qss)

    def set_items(self, items, icon_size=None) -> None:
        """items: list of ``(label, callback)`` or ``(label, callback, icon)``.

        The icon form carries the theme-preset swatches through: the combo this
        replaces on macOS put them on its items with addItem(icon, name), and a
        text-only list silently dropped them.
        """
        for b in self._buttons:
            self._col.removeWidget(b)
            b.deleteLater()
        self._buttons.clear()
        for item in items:
            label, cb = item[0], item[1]
            icon = item[2] if len(item) > 2 else None
            b = QPushButton(label, objectName="popupItem")
            b.setCursor(Qt.PointingHandCursor)
            b.setFlat(True)
            if icon is not None and not icon.isNull():
                b.setIcon(icon)
                if icon_size is not None:
                    b.setIconSize(icon_size)
            b.clicked.connect(lambda _c=False, fn=cb: self._pick(fn))
            self._col.addWidget(b)
            self._buttons.append(b)
        self.adjustSize()

    def _pick(self, fn) -> None:
        self.hide()
        fn()

    def popup_at(self, global_pos) -> None:
        """Show at a global point — for a right-click context menu."""
        self.adjustSize()
        self.setFixedSize(self.sizeHint())
        self.move(global_pos)
        self._show()

    def popup_under(self, widget) -> None:
        """Show anchored below ``widget``, like a drop-down would."""
        self.adjustSize()
        self.setFixedSize(self.sizeHint())   # a drop-down does not resize
        pos = widget.mapToGlobal(widget.rect().bottomLeft())
        self.move(pos)
        self._show()

    def _show(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        macos_window.round_window(self, 8)
        macos_window.make_overlay(self)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)


class _MenuPopup:
    """QMenu behind the same tiny API as ThemedPopup.

    Used on Windows and Linux, where the native menu renders correctly and
    brings things a hand-rolled button list does not: arrow-key navigation,
    Enter to select, and the platform's accessibility support. Only macOS needs
    the replacement, so only macOS pays for it.
    """

    def __init__(self, parent=None) -> None:
        from PySide6.QtWidgets import QMenu
        self._menu = QMenu(parent)
        self._actions = []

    def set_items(self, items, icon_size=None) -> None:
        """items: ``(label, callback)`` or ``(label, callback, icon)``.

        Accepts the icon form even though no current caller uses it here, so
        the two popup kinds are actually interchangeable. They were not: this
        took two-tuples only and no icon_size, so the combo's three-tuple call
        would have raised the moment that substitution reached a platform using
        a QMenu.
        """
        from PySide6.QtGui import QAction
        self._menu.clear()
        self._actions = []          # keep the QActions alive
        for item in items:
            label, cb = item[0], item[1]
            icon = item[2] if len(item) > 2 else None
            act = QAction(label, self._menu)
            if icon is not None and not icon.isNull():
                act.setIcon(icon)
            act.triggered.connect(lambda _c=False, fn=cb: fn())
            self._menu.addAction(act)
            self._actions.append(act)
        # QMenu has no setIconSize; it sizes icons from the style. Accepting
        # and ignoring the argument is what makes the two kinds interchangeable.

    def _restyle(self) -> None:
        """Re-apply the current theme just before showing.

        A QMenu inherits its look from an ancestor's stylesheet, and Qt does not
        repolish a popup when that ancestor's sheet is swapped — so a menu built
        under a dark theme kept a dark panel after switching to a light one,
        with only the text (redrawn from the palette) following. The combo
        drop-down never had this because it restyles its view on every
        showPopup; this does the same.
        """
        import theme
        self._menu.setStyleSheet(theme.build_qss(theme.active()))

    def popup_at(self, global_pos) -> None:
        self._restyle()
        self._menu.exec(global_pos)

    def popup_under(self, widget) -> None:
        self._restyle()
        self._menu.exec(widget.mapToGlobal(widget.rect().bottomLeft()))

    def hide(self) -> None:
        self._menu.hide()


def make_popup(parent=None):
    """A drop-down / context list: ThemedPopup on macOS, a real QMenu elsewhere.

    macOS paints a menu's panel with a vibrancy material that ignores the
    stylesheet, and it cannot be overridden — a stylesheet background,
    WA_TranslucentBackground, a Fusion style, painting the popup's NSWindow via
    pyobjc, and a QProxyStyle on PE_PanelMenu were each confirmed useless on
    real hardware. ThemedPopup sidesteps it by not being a menu at all.

    Windows has no such problem, so it keeps the native widget — swapping it
    there would trade working arrow-key navigation and accessibility for
    nothing. Linux keeps it too for context menus, where it renders fine; only
    the combo drop-down needs replacing there (see _ThemedCombo.showPopup).
    """
    return ThemedPopup(parent) if sys.platform == "darwin" else _MenuPopup(parent)


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
