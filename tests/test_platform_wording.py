"""Settings copy must not tell Mac and Linux users this is a Windows app.

Found 2026-07-27 on a real Ubuntu box: the STARTUP paragraph said "when you
sign in to Windows" and the UPDATES paragraph said "ships as a single .exe",
on Linux. The checkbox four lines below the first one already branched on
winutil.is_windows(), so the control was right and the sentence explaining it
was wrong -- which is the drift these tests exist to catch.

The panel is built directly rather than through a full Dashboard: constructing
one here leaks global state into other test modules (learned the hard way when
it broke test_dashboard_repaints_native_window_bg_on_theme_switch).
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

_app = QApplication.instance() or QApplication([])
app_settings.set_theme = lambda name: None


def _hints(monkeypatch, platform):
    """Text the Settings panel actually SHOWS a user on ``platform``.

    Explicitly-hidden widgets are excluded, so a section that is correctly
    gated off a platform cannot trip these assertions. The START MENU section
    is the live example: it is Windows-only wording, but it is hidden off
    Windows (dashboard.py, `if start_menu.is_supported()`), so it is not a
    wording bug and must not be reported as one.
    """
    monkeypatch.setattr(dashboard.sys, "platform", platform)
    monkeypatch.setattr(dashboard.winutil, "is_windows",
                        lambda: platform == "win32")
    host = QWidget()
    panel = dashboard.SettingsPanel(host, lambda *_a: None, lambda *_a: None)
    try:
        from PySide6.QtWidgets import QCheckBox, QLabel
        widgets = panel.findChildren(QLabel) + panel.findChildren(QCheckBox)
        return [w.text() for w in widgets if not w.isHidden()]
    finally:
        panel.deleteLater()
        host.deleteLater()


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_no_windows_only_wording_off_windows(monkeypatch, platform):
    joined = "\n".join(_hints(monkeypatch, platform))
    # ".exe" is simply false off Windows (.dmg / .tar.gz), and telling a Mac
    # user to sign in to Windows reads as "this app is not for you".
    assert ".exe" not in joined, f"{platform}: Settings mentions a .exe"
    assert "sign in to Windows" not in joined, \
        f"{platform}: Settings says 'sign in to Windows'"


def test_macos_says_menu_bar_not_system_tray(monkeypatch):
    joined = "\n".join(_hints(monkeypatch, "darwin"))
    # macOS has no "system tray" -- the affordance is the menu bar, and this
    # text is what tells a new user where to look for a windowless app.
    assert "menu bar" in joined, "macOS Settings never mentions the menu bar"
    assert "system tray" not in joined, \
        "macOS Settings says 'system tray', which does not exist there"


def test_windows_wording_is_unchanged(monkeypatch):
    joined = "\n".join(_hints(monkeypatch, "win32"))
    assert "system tray" in joined
    assert "menu bar" not in joined, "Windows Settings should not say menu bar"


def test_about_uses_the_product_name_not_the_repo_name(monkeypatch):
    joined = "\n".join(_hints(monkeypatch, "darwin"))
    assert "Clawdmeter-Windows  v" not in joined, \
        "About shows the repo name as the product name"
    # The repo URL is a different thing and MUST keep matching the real
    # repository: update_check rejects any release URL that doesn't start with
    # https://github.com/<REPO>/, so renaming it here before the repo is
    # actually renamed would silently break update checking.
    assert "github.com/weltern/Clawdmeter-Windows" in joined, \
        "the About link must keep pointing at the real repository"
