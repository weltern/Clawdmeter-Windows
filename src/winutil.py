"""Cross-platform window helpers: move, edge-resize, and always-on-top.

A frameless top-level needs OS cooperation to move, resize, and stay on top.
On Windows we drive the native loops directly via Win32 message posting — it's
DPI-aware and flicker-free, and it's why the mini window stopped ballooning
across a higher-DPI monitor (issue #7). On other platforms we delegate to Qt's
cross-platform ``QWindow.startSystemMove`` / ``startSystemResize``, which hand
off to the compositor and work under both X11 and Wayland (where ``window.move``
is silently ignored). The Windows code paths below are unchanged from the
Windows-only version — the non-Windows branches are purely additive.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

from PySide6.QtCore import Qt, QTimer

import macos_window   # no-ops off macOS; imports nothing heavy


# WM_NCHITTEST result codes.
HTCLIENT = 1
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1

# SetWindowPos.
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

# Pixel-thick resize border on each window edge.
RESIZE_BORDER_PX = 6


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", wt.WPARAM),
        ("lParam", wt.LPARAM),
        ("time", wt.DWORD),
        ("pt", wt.POINT),
    ]


def is_windows() -> bool:
    return sys.platform == "win32"


def set_topmost(widget, on: bool) -> None:
    """Toggle always-on-top for a top-level widget.

    Windows: flip WS_EX_TOPMOST via SetWindowPos with no move/size/activate, so
    there's no flicker and focus isn't stolen (unchanged behavior). Elsewhere:
    toggle Qt's WindowStaysOnTopHint and re-show to apply it — best-effort, since
    Wayland compositors may ignore a client's stay-on-top request.
    """
    if is_windows():
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        insert_after = HWND_TOPMOST if on else HWND_NOTOPMOST
        ctypes.windll.user32.SetWindowPos(
            wt.HWND(int(widget.winId())), wt.HWND(insert_after), 0, 0, 0, 0, flags
        )
        return
    # macOS: set the NSWindow's level in place. Toggling the Qt flag would make
    # Qt rebuild the native window, which silently discards the transparent
    # titlebar / full-size content view that macos_window.style() applied — the
    # window came back as stock chrome for the rest of the session. Setting the
    # level is the AppKit-native equivalent of the SetWindowPos path above.
    if macos_window.set_level(widget, on):
        return

    # Everything else (Linux, or macOS without pyobjc): toggling
    # WindowStaysOnTopHint recreates the native window, which hides a frameless
    # Tool window and a bare show() doesn't bring it back (it vanishes until
    # re-summoned from the tray). Preserve geometry and force it visible +
    # frontmost, both immediately and deferred one event-loop tick so the flag
    # change has settled before the re-show.
    was_visible = widget.isVisible()
    geo = widget.geometry()
    widget.setWindowFlag(Qt.WindowStaysOnTopHint, on)
    if not was_visible:
        return

    def _reshow():
        widget.setGeometry(geo)
        widget.show()
        widget.raise_()
        widget.activateWindow()

    _reshow()
    QTimer.singleShot(0, _reshow)


def start_move(widget) -> None:
    """Hand an in-progress drag to the OS's own window-move loop.

    Windows: releasing the mouse capture and posting WM_NCLBUTTONDOWN/HTCAPTION
    makes Windows move the window itself — DPI-aware, and without the per-step Qt
    geometry recompute that ballooned the frameless mini window when it was
    dragged onto a higher-DPI monitor (issue #7). Other platforms: delegate to
    QWindow.startSystemMove, which the compositor handles (X11 and Wayland alike).
    """
    if is_windows():
        user32 = ctypes.windll.user32
        user32.ReleaseCapture()
        user32.SendMessageW(wt.HWND(int(widget.winId())), WM_NCLBUTTONDOWN, HTCAPTION, 0)
        return
    handle = widget.windowHandle()
    if handle is not None:
        handle.startSystemMove()


def start_resize(widget, edges) -> None:
    """Begin an OS-driven edge/corner resize (non-Windows).

    Windows drives resize through the WM_NCHITTEST path in the window's
    ``nativeEvent``, so this is only invoked off Windows, delegating to the
    compositor via ``QWindow.startSystemResize``. ``edges`` is a ``Qt.Edges``.
    """
    handle = widget.windowHandle()
    if handle is not None:
        handle.startSystemResize(edges)


def edges_for_hit(hit: int):
    """Map a ``hit_test`` HT* code to the ``Qt.Edge`` flags startSystemResize wants."""
    return {
        HTLEFT: Qt.LeftEdge,
        HTRIGHT: Qt.RightEdge,
        HTTOP: Qt.TopEdge,
        HTBOTTOM: Qt.BottomEdge,
        HTTOPLEFT: Qt.TopEdge | Qt.LeftEdge,
        HTTOPRIGHT: Qt.TopEdge | Qt.RightEdge,
        HTBOTTOMLEFT: Qt.BottomEdge | Qt.LeftEdge,
        HTBOTTOMRIGHT: Qt.BottomEdge | Qt.RightEdge,
    }.get(hit, Qt.Edges())


def parse_msg(message_ptr) -> _MSG:
    """Materialize the MSG struct from Qt's nativeEvent message pointer."""
    return _MSG.from_address(int(message_ptr))


def screen_xy_from_lparam(lparam: int) -> tuple[int, int]:
    """Unpack WM_NCHITTEST lParam (low word = x, high word = y, signed)."""
    lp = lparam & 0xFFFFFFFF
    x = ctypes.c_int16(lp & 0xFFFF).value
    y = ctypes.c_int16((lp >> 16) & 0xFFFF).value
    return x, y


def hit_test(local_x: int, local_y: int, width: int, height: int) -> int:
    """Return the WM_NCHITTEST code for a point in window-local coords.

    Returns HTCLIENT for the interior so Qt handles input normally; returns
    HT* edge codes when the point is inside the resize border. Pure geometry —
    reused off Windows (via ``edges_for_hit``) to drive ``startSystemResize``.
    """
    # Reject out-of-bounds points so a DPI/multi-monitor coordinate mismatch
    # can't be misread as a resize-border hit (issue #7).
    if local_x < 0 or local_y < 0 or local_x >= width or local_y >= height:
        return HTCLIENT

    b = RESIZE_BORDER_PX
    left = local_x < b
    right = local_x >= width - b
    top = local_y < b
    bottom = local_y >= height - b

    if top and left:
        return HTTOPLEFT
    if top and right:
        return HTTOPRIGHT
    if bottom and left:
        return HTBOTTOMLEFT
    if bottom and right:
        return HTBOTTOMRIGHT
    if left:
        return HTLEFT
    if right:
        return HTRIGHT
    if top:
        return HTTOP
    if bottom:
        return HTBOTTOM
    return HTCLIENT
