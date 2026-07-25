"""Native macOS window chrome for Clawdmeter's custom-title-bar windows.

A borderless (``Qt.FramelessWindowHint``) window on macOS has square corners and
a hard edge -- the thin dark "seam" you see around a frameless window. That's not
how macOS apps do custom chrome. VS Code, Slack, Spotify, etc. keep a NORMAL
window (native rounded corners + shadow + resize) and simply make its title bar
transparent and let content fill it, drawing their own chrome on top. This module
applies that treatment to a Qt widget's underlying ``NSWindow`` via pyobjc.

Off macOS every function is a no-op, so callers can invoke them unconditionally.
"""

from __future__ import annotations

import sys

# AppKit constants (kept as literals so we don't depend on their pyobjc names).
_STYLE_FULL_SIZE_CONTENT_VIEW = 1 << 15   # NSWindowStyleMaskFullSizeContentView
_TITLE_HIDDEN = 1                         # NSWindowTitleHidden


def is_supported() -> bool:
    return sys.platform == "darwin"


def style(widget) -> bool:
    """Turn ``widget``'s NSWindow into a transparent-titlebar, full-content window
    with the traffic-light buttons hidden (the app draws its own close/minimize).

    Returns True on success. No-op (False) off macOS, if pyobjc is unavailable,
    or if the native window handle isn't ready yet -- so call it after show().
    """
    if not is_supported():
        return False
    try:
        import objc
    except Exception:   # noqa: BLE001 - pyobjc missing from this build
        return False
    try:
        view = objc.objc_object(c_void_p=int(widget.winId()))
        win = view.window()
    except Exception:   # noqa: BLE001 - no native handle yet
        return False
    if win is None:
        return False
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(_TITLE_HIDDEN)
    win.setStyleMask_(win.styleMask() | _STYLE_FULL_SIZE_CONTENT_VIEW)
    for kind in (0, 1, 2):   # close / miniaturize / zoom
        btn = win.standardWindowButton_(kind)
        if btn is not None:
            btn.setHidden_(True)
    return True
