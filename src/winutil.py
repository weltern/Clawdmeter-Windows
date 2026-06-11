"""Win32 helpers: zero-flicker topmost toggle + WM_NCHITTEST border resize."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys


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


def set_topmost(hwnd: int, on: bool) -> None:
    """Toggle WS_EX_TOPMOST without recreating the window (no flicker)."""
    if not is_windows():
        return
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    insert_after = HWND_TOPMOST if on else HWND_NOTOPMOST
    ctypes.windll.user32.SetWindowPos(
        wt.HWND(hwnd), wt.HWND(insert_after), 0, 0, 0, 0, flags
    )


def start_native_move(hwnd: int) -> None:
    """Hand an in-progress drag to Windows' own move loop.

    Releasing the mouse capture and posting WM_NCLBUTTONDOWN/HTCAPTION makes
    Windows move the window itself — DPI-aware, and without the per-step Qt
    geometry recompute that ballooned the frameless compact window when it was
    dragged onto a higher-DPI monitor.
    """
    if not is_windows():
        return
    user32 = ctypes.windll.user32
    user32.ReleaseCapture()
    user32.SendMessageW(wt.HWND(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, 0)


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
    HT* edge codes when the point is inside the resize border.
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
