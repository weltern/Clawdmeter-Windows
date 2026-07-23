"""Entry point for Clawdmeter-Windows."""

from __future__ import annotations

import os
import sys

from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

import app_settings
import pricing_refresh
import run_at_startup
import single_instance
import theme
from dashboard import Dashboard, apply_theme
from sprite_player import assets_root


def main() -> int:
    mock = "--mock" in sys.argv
    startup = run_at_startup.STARTUP_FLAG in sys.argv  # launched at sign-in
    app = QApplication(sys.argv)
    # The OS colour-scheme hint (for native / un-QSS'd surfaces — notably macOS
    # native text) is set to match the active theme by apply_theme() below, so a
    # light theme renders correctly on every platform.
    app.setApplicationName("Clawdmeter")
    app.setOrganizationName(app_settings.ORG)
    # Ties the app to packaging/clawdmeter.desktop so Wayland uses its icon
    # (the app_id must match the .desktop basename). No-op on Windows.
    app.setDesktopFileName("clawdmeter")
    app.setQuitOnLastWindowClosed(False)  # tray keeps app alive

    # Single instance: if a copy is already running, surface its window and
    # exit instead of starting a duplicate process that lingers in the tray.
    if single_instance.activate_running_instance():
        return 0

    # Apply persisted credentials override before the poller starts.
    cred = app_settings.get_credentials_override()
    if cred:
        os.environ["CLAUDE_CREDENTIALS_PATH"] = cred

    # Prefer a prior session's live pricing refresh over the build-time bundled
    # map, before Dashboard (and Stats) ever reads pricing.
    pricing_refresh.apply_cached_override()

    icon_path = assets_root() / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Register bundled icon fonts (Font Awesome 6 Free Solid) before any window
    # builds, so glyph-as-text labels resolve. Family: "Font Awesome 6 Free".
    fa_path = assets_root() / "fonts" / "fa-solid-900.ttf"
    if fa_path.exists():
        QFontDatabase.addApplicationFont(str(fa_path))

    # Apply the saved appearance theme before building the window so it comes up
    # already themed (no flash). With no widgets yet, apply_theme just sets the
    # active palette and refreshes the module colour caches. Load the custom
    # theme's base colours first so a saved "Custom" selection resolves.
    saved_custom = app_settings.get_custom_base()
    if saved_custom:
        theme.set_custom_base(saved_custom)
    apply_theme(app_settings.get_theme())

    win = Dashboard(mock=mock)
    if startup:
        # Sign-in launch: stay in the tray (don't pop the window). The poller,
        # update checker and transcript watcher already start in __init__.
        run_at_startup.sync_if_enabled()  # keep the entry pointed at this .exe
        # ...unless there's no system tray to stay in (some Linux DEs): a hidden
        # tray-less launch would be invisible and unrecoverable, so show the
        # window instead. Gated off Windows so the Windows sign-in path is
        # provably unchanged (it always has a tray regardless).
        if sys.platform != "win32" and not getattr(win, "tray_available", True):
            win.show_initial()
    else:
        win.show_initial()   # launch directly into the last-used view mode

    # Listen for later launches so they surface this window instead of
    # spawning a duplicate. Kept on `app` so it isn't garbage-collected.
    app._instance_server = single_instance.InstanceServer(on_show=win._show_window)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
