"""Tiny shared UI formatting helpers.

Lives in its own module so both dashboard.py and session_shelf.py can use them
without an import cycle (dashboard imports session_shelf, so session_shelf can't
import back from dashboard).
"""

from __future__ import annotations

import sys
import time

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


def is_wayland() -> bool:
    """True when Qt is actually speaking Wayland.

    Asks Qt which platform plugin it loaded rather than reading
    WAYLAND_DISPLAY. A session can advertise itself as Wayland while Qt
    connects through XWayland/xcb, and what matters to callers is which
    protocol our windows really speak -- an X11 window in a Wayland session
    can still raise and position itself, a native Wayland one cannot.

    Not cached: it is cheap, and caching it before the QGuiApplication exists
    would freeze in a wrong answer.
    """
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance()
    return bool(app) and app.platformName().lower().startswith("wayland")


# How long a cached compositing answer stays good. Compositing genuinely
# toggles at runtime -- a user turns their compositor off for a game, or KWin
# restarts -- and a result cached for the whole process would leave the popup
# corners wrong until the app was restarted. Popups are opened by hand, so
# re-asking every few seconds costs nothing measurable.
_COMPOSITING_TTL_S = 5.0
# Ceiling on how long the X11 probe may hold the GUI thread. XOpenDisplay is a
# synchronous connect plus auth handshake against whatever $DISPLAY names; it
# is normally instant against a local socket, but nothing bounds it if $DISPLAY
# points somewhere unreachable (a stale SSH-forwarded display, say). The UI
# must not be able to freeze on a cosmetic question.
_COMPOSITING_PROBE_TIMEOUT_S = 1.5

_compositing_cache: "tuple[float, bool] | None" = None


def _probe_x11_compositing() -> bool:
    """Ask X whether a compositing manager owns _NET_WM_CM_Sn. Blocking."""
    import ctypes
    import ctypes.util
    libname = ctypes.util.find_library("X11")
    if not libname:
        return True
    x11 = ctypes.CDLL(libname)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return True
    try:
        x11.XDefaultScreen.restype = ctypes.c_int
        x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XGetSelectionOwner.restype = ctypes.c_ulong
        x11.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        atom = x11.XInternAtom(
            dpy, f"_NET_WM_CM_S{x11.XDefaultScreen(dpy)}".encode(), 0)
        return bool(x11.XGetSelectionOwner(dpy, atom))
    finally:
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay(dpy)


def _x11_compositing_bounded() -> bool:
    """_probe_x11_compositing() with a wall-clock ceiling.

    A blocked C call cannot be cancelled, so the worker is a daemon thread we
    simply stop waiting on -- it either finishes into the void or dies with the
    process. That leaks at most one thread, once per TTL window, in the failure
    case that should never happen. A frozen UI is much worse.
    """
    import threading
    out: list[bool] = []

    def _run():
        try:
            out.append(_probe_x11_compositing())
        except Exception:
            pass

    t = threading.Thread(target=_run, name="clawd-x11-composite-probe",
                         daemon=True)
    t.start()
    t.join(_COMPOSITING_PROBE_TIMEOUT_S)
    # No answer in time -> assume composited, the better-looking branch.
    return out[0] if out else True


def linux_compositing() -> bool:
    """Can this desktop composite a translucent top-level window?

    It matters because ThemedPopup rounds its card's corners, and the pixels
    outside that radius show whatever the popup's own window holds. With a
    compositor those pixels are transparent and you see the desktop; without
    one they come out solid black, so the corners read as black notches
    instead of rounded -- glaring over a light panel, invisible over a dark
    one, which is exactly how the bug presented (native X capture on Mint:
    (0,0,0) outside the arc against a (238,241,245) backdrop).

    Wayland always composites. On X11 it depends on a compositing manager
    owning the _NET_WM_CM_Sn selection, which is what this asks. Anything
    unexpected answers True, because every mainstream desktop composites and
    that is the better-looking branch.

    The answer is cached for _COMPOSITING_TTL_S rather than for the process
    lifetime: compositing really does get toggled while an app is running.
    """
    global _compositing_cache
    if not sys.platform.startswith("linux"):
        return True
    # Wayland always composites. Ask Qt which platform it is on rather than
    # reading WAYLAND_DISPLAY: that variable stays set while Qt draws to an X
    # display through XWayland, and in that case it is the X display's
    # compositing state we need, not the Wayland session's. Same reasoning as
    # is_wayland(), which this now shares so the two cannot answer differently.
    #
    # Deliberately NOT cached: it is a cheap attribute read, and caching it
    # would mean a call made before the QGuiApplication exists could freeze in
    # a wrong answer for the rest of the run.
    if is_wayland():
        return True
    now = time.monotonic()
    if _compositing_cache is not None and now - _compositing_cache[0] < _COMPOSITING_TTL_S:
        return _compositing_cache[1]
    try:
        value = _x11_compositing_bounded()
    except Exception:
        # Not cached: a transient failure should not pin the answer for the
        # rest of the TTL window.
        return True
    _compositing_cache = (now, value)
    return value


def _clear_compositing_cache() -> None:
    """Drop the cached answer. For tests; also safe to call at runtime."""
    global _compositing_cache
    _compositing_cache = None


# Kept as an attribute so callers and tests written against the previous
# lru_cache implementation keep working unchanged.
linux_compositing.cache_clear = _clear_compositing_cache


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
        # On macOS round_window() makes the native window non-opaque, so the
        # pixels outside the card's radius are transparent there. That call is
        # a no-op off macOS and nothing else cleared this window, so on Linux
        # it stayed opaque black and the radius framed the corners in black.
        # Qt only honours this if it is set before the window is first shown.
        #
        # Latched, not re-probed. Qt cannot change WA_TranslucentBackground
        # after the first show, so this answer is final for the life of the
        # popup -- and _ThemedCombo.showPopup caches one instance per combo for
        # the whole process. apply_theme_style() therefore has to square the
        # corners off the SAME latched value: it runs again on every theme
        # switch, and asking linux_compositing() afresh there let a compositor
        # started mid-session restore the radius on a window that never got
        # translucency, i.e. the black corner notches this whole branch exists
        # to remove.
        self._linux_translucent = (
            sys.platform.startswith("linux") and linux_compositing())
        if self._linux_translucent:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
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
        elif sys.platform.startswith("linux") and not self._linux_translucent:
            # Nothing can make the corners transparent here, so square them off
            # rather than leave black notches: a square drop-down reads as
            # deliberate, a rounded one framed in black reads as broken.
            qss += "\nQWidget#popupRoot{border-radius:0}"
        self._card.setStyleSheet(qss)
        if sys.platform.startswith("linux"):
            # The card must paint its own background rather than inherit one.
            # With a compositor the window behind it is translucent, so without
            # this the whole popup goes see-through; without a compositor the
            # window is opaque but the card still needs to own its fill for the
            # squared-off corners above to look deliberate. Unconditional
            # because it is required either way.
            self._card.setAttribute(Qt.WA_StyledBackground, True)

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
