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
    # "tray menu" is a THIRD spelling, and the assertions above are blind to it:
    # "menu bar" still matches the STARTUP hint, and "tray menu" is not the
    # substring "system tray". So the UPDATES hint could silently revert to
    # "the tray menu shows an Update available item" with the suite green --
    # which is precisely what happened, and is why this line exists.
    assert "tray menu" not in joined, \
        "macOS Settings says 'tray menu'; on macOS the affordance is the menu bar"


def test_windows_wording_is_unchanged(monkeypatch):
    joined = "\n".join(_hints(monkeypatch, "win32"))
    assert "system tray" in joined
    assert "menu bar" not in joined, "Windows Settings should not say menu bar"
    assert "tray menu" in joined, \
        "Windows lost the UPDATES hint's pointer to the tray menu"


def test_about_uses_the_product_name_not_the_repo_name(monkeypatch):
    joined = "\n".join(_hints(monkeypatch, "darwin"))
    assert "Clawdmeter-Windows  v" not in joined, \
        "About shows the repo name as the product name"
    # The link is a different thing from the product name and must keep naming
    # the real repository, or it 404s. Nothing reads this string, so changing
    # it cannot break update checking -- that risk belongs to update_check.REPO,
    # which builds the releases API URL and gates which release URLs are
    # trusted. Both move in the same commit as the rename, for different reasons.
    assert "github.com/weltern/Clawdmeter-Windows" in joined, \
        "the About link must keep pointing at the real repository"


def _token_line(monkeypatch, *, keychain: bool, secs_left: float) -> str:
    """The token-validity line as a user on that platform would read it."""
    import time as _time

    import token_refresh
    monkeypatch.setattr(token_refresh, "_macos_keychain_active", lambda: keychain)
    monkeypatch.setattr(token_refresh, "token_expiry_ms",
                        lambda _p, **_kw: (_time.time() + secs_left) * 1000)
    monkeypatch.setattr(token_refresh, "is_expired", lambda _p: secs_left <= 0)
    host = QWidget()
    panel = dashboard.SettingsPanel(host, lambda *_a: None, lambda *_a: None)
    try:
        return panel.token_status.text()
    finally:
        panel.deleteLater()
        host.deleteLater()


@pytest.mark.parametrize("secs_left", [4 * 3600, -60])
def test_macos_never_promises_an_auto_refresh_it_cannot_do(monkeypatch, secs_left):
    """The line sits directly beneath two controls this panel just greyed out.

    refresh_token_status() disables the refresh button ("Managed by the macOS
    Keychain") and force-unchecks auto-refresh, because token_refresh.refresh()
    returns "not supported" on macOS unconditionally. The shared tail still
    said "refreshes automatically" / "wait for auto-refresh", which left the
    user waiting on something that never arrives. Renewal there is Claude
    Code's job.
    """
    line = _token_line(monkeypatch, keychain=True, secs_left=secs_left)
    lowered = line.lower()
    assert "automatic" not in lowered, \
        f"macOS token line promises an auto-refresh it cannot perform: {line!r}"
    assert "auto-refresh" not in lowered, \
        f"macOS token line points at auto-refresh, which is disabled: {line!r}"
    assert "claude" in lowered, \
        f"macOS token line must name what actually renews the token: {line!r}"


@pytest.mark.parametrize("secs_left", [4 * 3600, -60])
def test_windows_token_wording_is_unchanged(monkeypatch, secs_left):
    """The macOS branch must not have rerouted the platform that has users."""
    line = _token_line(monkeypatch, keychain=False, secs_left=secs_left)
    assert "auto-refresh" in line.lower() or "automatically" in line.lower(), \
        f"Windows lost its auto-refresh wording: {line!r}"


def test_the_hint_and_the_checkbox_use_the_same_verb(monkeypatch):
    """They sit four lines apart on screen, so a mismatch is glaring.

    Caught in review: the hint branched per platform and the checkbox did not,
    so Linux read "Launch Clawdmeter automatically when you log in" directly
    above "Start when I sign in".
    """
    for platform, verb, wrong in (("win32", "sign in", "log in"),
                                  ("darwin", "log in", "sign in"),
                                  ("linux", "log in", "sign in")):
        texts = _hints(monkeypatch, platform)
        startup = [t for t in texts if "Launch Clawdmeter automatically" in t
                   or t.startswith("Start when I")]
        assert len(startup) == 2, f"{platform}: expected the hint and the checkbox"
        for t in startup:
            assert verb in t, f"{platform}: {t!r} should say {verb!r}"
            assert wrong not in t, f"{platform}: {t!r} should not say {wrong!r}"
