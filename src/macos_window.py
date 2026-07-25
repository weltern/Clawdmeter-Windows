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
_COLLECTION_FULLSCREEN_NONE = 1 << 9      # NSWindowCollectionBehaviorFullScreenNone


def is_supported() -> bool:
    return sys.platform == "darwin"


def round_window(widget, radius: float = 13.0) -> bool:
    """Give a HUD-style opaque frameless window (e.g. the mini view) native
    rounded corners + a drop shadow on macOS. The widget must stay OPAQUE (no
    WA_TranslucentBackground -- that makes Qt's panel render fully transparent on
    the frozen build). We round the content view's EXISTING layer (Qt 6 already
    layer-backs its views on macOS) rather than calling setWantsLayer, which would
    reset the layer and break rendering. The window itself is made non-opaque with
    a clear background so the corners outside the radius are transparent.
    No-op off macOS / no pyobjc."""
    if not is_supported():
        return False
    try:
        import objc
        from AppKit import NSColor
    except Exception:   # noqa: BLE001
        return False
    try:
        view = objc.objc_object(c_void_p=int(widget.winId()))
        win = view.window()
    except Exception:   # noqa: BLE001
        return False
    if win is None:
        return False
    win.setOpaque_(False)
    win.setBackgroundColor_(NSColor.clearColor())
    cv = win.contentView()
    if cv is not None:
        layer = cv.layer()   # already-backed on Qt 6 macOS; don't setWantsLayer
        if layer is not None:
            layer.setCornerRadius_(radius)
            layer.setMasksToBounds_(True)
    win.setHasShadow_(True)
    win.invalidateShadow()   # recompute the shadow to hug the rounded content
    return True


def style(widget, bg_hex: str | None = None) -> bool:
    """Turn ``widget``'s NSWindow into a transparent-titlebar, full-content window
    so our chrome can flow up around the native traffic-light buttons (Option B).
    Optionally paint the window background ``bg_hex`` so the titlebar region
    matches our title bar. Returns True on success; no-op off macOS / no pyobjc.
    """
    if not is_supported():
        return False
    try:
        import objc
        from AppKit import NSColor
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
    win.setStyleMask_(int(win.styleMask()) | _STYLE_FULL_SIZE_CONTENT_VIEW)
    # Disable native fullscreen (green button -> zoom instead). Fullscreen resets
    # the transparent-titlebar style and its exit event fires unreliably, so a
    # small utility window should just zoom -- which preserves the style.
    win.setCollectionBehavior_(_COLLECTION_FULLSCREEN_NONE)
    if bg_hex:
        r, g, b = (int(bg_hex[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        win.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0))
    # DIAGNOSTIC: is the content area reserving space for the title bar? If
    # contentLayoutRect is inset from the full contentView, Qt can't put our
    # chrome at y=0 (that's the "strip") and true Option B needs the lights moved.
    try:
        cv = win.contentView()
        print(f"[macwin] frame={win.frame().size.width:.0f}x{win.frame().size.height:.0f} "
              f"contentView={cv.frame().size.width:.0f}x{cv.frame().size.height:.0f} "
              f"contentLayoutRect_h={win.contentLayoutRect().size.height:.0f}",
              file=sys.stderr, flush=True)
    except Exception:   # noqa: BLE001 - diagnostic only
        pass
    return True
