"""Entry point for Clawdmeter-Windows."""

from __future__ import annotations

import os
import sys

from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

import app_settings
import single_instance
from dashboard import Dashboard
from sprite_player import assets_root


def main() -> int:
    mock = "--mock" in sys.argv
    app = QApplication(sys.argv)
    app.setApplicationName("Clawdmeter")
    app.setOrganizationName(app_settings.ORG)
    app.setQuitOnLastWindowClosed(False)  # tray keeps app alive

    # Single instance: if a copy is already running, surface its window and
    # exit instead of starting a duplicate process that lingers in the tray.
    if single_instance.activate_running_instance():
        return 0

    # Apply persisted credentials override before the poller starts.
    cred = app_settings.get_credentials_override()
    if cred:
        os.environ["CLAUDE_CREDENTIALS_PATH"] = cred

    icon_path = assets_root() / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Register bundled icon fonts (Font Awesome 6 Free Solid) before any window
    # builds, so glyph-as-text labels resolve. Family: "Font Awesome 6 Free".
    fa_path = assets_root() / "fonts" / "fa-solid-900.ttf"
    if fa_path.exists():
        QFontDatabase.addApplicationFont(str(fa_path))

    win = Dashboard(mock=mock)
    win.show_initial()   # launch directly into the last-used view mode

    # Listen for later launches so they surface this window instead of
    # spawning a duplicate. Kept on `app` so it isn't garbage-collected.
    app._instance_server = single_instance.InstanceServer(on_show=win._show_window)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
