"""The UI must never read the macOS Keychain directly.

read_credentials() can block for as long as a user takes to answer a macOS
authorisation dialog. SettingsPanel used to call through to it while
constructing itself during Dashboard startup, so on any macOS update -- a new
code signature invalidates the Keychain ACL and forces a re-prompt -- the app
hung before creating its window or menu-bar icon. All the user saw was an
unexplained password dialog from an app with no Dock icon. Measured on the
Intel macOS VM: `sample` showed 2321 of 2321 samples on com.apple.main-thread
inside SecItemCopyMatching.

The old code carried a comment asserting it "runs on a QThread worker (never
the UI thread)". Nothing enforced that, which is how it quietly stopped being
true, so these tests enforce it instead of a comment.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pathlib import Path  # noqa: E402

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import app_settings  # noqa: E402
import dashboard  # noqa: E402
import macos_keychain  # noqa: E402
import token_refresh  # noqa: E402

_app = QApplication.instance() or QApplication([])
app_settings.set_theme = lambda name: None


def test_building_settings_never_touches_the_keychain(monkeypatch):
    """The regression itself: constructing the panel must not read it.

    This is the check that would have caught the shipped bug -- it fails if
    anything on the construction path calls read_credentials().
    """
    monkeypatch.setattr(macos_keychain, "is_macos", lambda: True)
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)

    def _boom():
        raise AssertionError(
            "the UI called read_credentials(); on macOS this blocks on an "
            "authorisation dialog and hangs startup -- use cached_credentials()")
    monkeypatch.setattr(macos_keychain, "read_credentials", _boom)
    monkeypatch.setattr(macos_keychain, "cached_credentials", lambda: None)

    host = QWidget()
    p = dashboard.SettingsPanel(host, lambda *_a: None, lambda *_a: None)
    p.deleteLater()
    host.deleteLater()


def test_the_non_blocking_read_uses_the_cache(monkeypatch):
    monkeypatch.setattr(macos_keychain, "is_macos", lambda: True)
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)
    monkeypatch.setattr(macos_keychain, "cached_credentials", lambda: '{"a": 1}')
    monkeypatch.setattr(macos_keychain, "read_credentials",
                        lambda: (_ for _ in ()).throw(AssertionError("blocked!")))
    assert token_refresh._read_credentials_raw(
        Path("/nonexistent"), blocking=False) == '{"a": 1}'


def test_blocking_is_the_default_so_workers_are_unaffected(monkeypatch):
    """The poller must keep getting the real, authoritative read."""
    monkeypatch.setattr(macos_keychain, "is_macos", lambda: True)
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)
    monkeypatch.setattr(macos_keychain, "read_credentials", lambda: '{"real": 1}')
    monkeypatch.setattr(macos_keychain, "cached_credentials", lambda: '{"stale": 1}')
    assert token_refresh._read_credentials_raw(Path("/nonexistent")) == '{"real": 1}'


def test_a_successful_read_populates_the_cache(monkeypatch):
    """Without this the UI's cache would never fill and the expiry line would
    stay blank forever on macOS."""
    monkeypatch.setattr(macos_keychain, "is_macos", lambda: True)
    monkeypatch.setattr(macos_keychain, "_read_via_framework", lambda: '{"tok": 1}')
    macos_keychain._cached_blob = None
    try:
        assert macos_keychain.read_credentials() == '{"tok": 1}'
        assert macos_keychain.cached_credentials() == '{"tok": 1}'
    finally:
        macos_keychain._cached_blob = None


def test_a_failed_read_does_not_poison_the_cache(monkeypatch):
    monkeypatch.setattr(macos_keychain, "is_macos", lambda: True)
    macos_keychain._cached_blob = '{"good": 1}'
    try:
        monkeypatch.setattr(macos_keychain, "_read_via_framework", lambda: None)
        assert macos_keychain.read_credentials() is None
        assert macos_keychain.cached_credentials() == '{"good": 1}', \
            "a denied or absent read must not wipe a blob we already had"
    finally:
        macos_keychain._cached_blob = None
