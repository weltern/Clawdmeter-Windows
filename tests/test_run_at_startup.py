"""Tests for the run-at-startup (Windows Run key) helper.

The enable/disable round-trip runs against a throwaway HKCU subkey (patched in
via ``RUN_KEY``) so it never touches the user's real
``...\\CurrentVersion\\Run`` entry. The command-building tests are pure.

Run with `python -m pytest tests/ -q`, or directly:
`python tests/test_run_at_startup.py`.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import run_at_startup  # noqa: E402

winreg = run_at_startup.winreg
_TEST_KEY = r"Software\ClawdmeterTest\RunAtStartup"

requires_winreg = pytest.mark.skipif(
    winreg is None, reason="winreg unavailable (non-Windows)")


@pytest.fixture
def temp_run_key(monkeypatch):
    """Point the helper at a disposable HKCU subkey and clean it up after."""
    monkeypatch.setattr(run_at_startup, "RUN_KEY", _TEST_KEY)
    yield
    # Remove the leaf and the now-empty parent so no test key lingers in HKCU.
    for key in (_TEST_KEY, r"Software\ClawdmeterTest"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass


def test_launch_command_quotes_and_flags():
    cmd = run_at_startup.launch_command()
    assert run_at_startup.STARTUP_FLAG in cmd
    assert cmd.count('"') >= 2          # the executable path is quoted
    assert cmd.lstrip().startswith('"')  # ...even if it has spaces


def test_is_supported_true_on_supported_platforms():
    # Supported wherever there's an autostart mechanism: Windows (Run key),
    # Linux (XDG autostart), and macOS (LaunchAgent plist).
    expected = (
        sys.platform == "win32"
        or sys.platform.startswith("linux")
        or sys.platform == "darwin"
    )
    assert run_at_startup.is_supported() is expected


# --- macOS LaunchAgent branch (forced on via monkeypatch so it runs anywhere) ---

def _force_macos(monkeypatch, tmp_path):
    """Route the platform dispatch to the macOS branch and sandbox the plist."""
    monkeypatch.setattr(run_at_startup, "_is_macos", lambda: True)
    monkeypatch.setattr(run_at_startup, "_is_linux", lambda: False)
    monkeypatch.setattr(run_at_startup, "_launch_agents_dir", lambda: tmp_path)


def test_macos_is_supported_when_forced(monkeypatch, tmp_path):
    _force_macos(monkeypatch, tmp_path)
    assert run_at_startup.is_supported() is True


def test_macos_launch_agent_round_trip(monkeypatch, tmp_path):
    _force_macos(monkeypatch, tmp_path)
    plist = tmp_path / run_at_startup.PLIST_FILE_NAME

    assert run_at_startup.is_enabled() is False

    ok, msg = run_at_startup.enable()
    assert ok
    assert plist.exists()
    assert run_at_startup.STARTUP_FLAG in msg
    assert run_at_startup.is_enabled() is True

    ok, _ = run_at_startup.disable()
    assert ok
    assert not plist.exists()
    assert run_at_startup.is_enabled() is False

    # Disabling when already absent is a no-op success.
    ok, _ = run_at_startup.disable()
    assert ok


def test_macos_plist_is_well_formed_xml(monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET

    _force_macos(monkeypatch, tmp_path)
    run_at_startup.enable()
    text = (tmp_path / run_at_startup.PLIST_FILE_NAME).read_text(encoding="utf-8")

    # Parses cleanly (escaping in ProgramArguments is valid) and carries the
    # label, the --startup flag, and RunAtLoad.
    ET.fromstring(text)
    assert run_at_startup.LAUNCH_AGENT_LABEL in text
    assert f"<string>{run_at_startup.STARTUP_FLAG}</string>" in text
    assert "<key>RunAtLoad</key>" in text


# --- Linux XDG autostart branch (forced on via monkeypatch so it runs anywhere) ---

def _force_linux(monkeypatch, tmp_path):
    """Route the platform dispatch to the Linux branch and sandbox the .desktop."""
    monkeypatch.setattr(run_at_startup, "_is_linux", lambda: True)
    monkeypatch.setattr(run_at_startup, "_is_macos", lambda: False)
    monkeypatch.setattr(run_at_startup, "_autostart_dir", lambda: tmp_path)


def test_linux_is_supported_when_forced(monkeypatch, tmp_path):
    _force_linux(monkeypatch, tmp_path)
    assert run_at_startup.is_supported() is True


def test_linux_autostart_round_trip(monkeypatch, tmp_path):
    _force_linux(monkeypatch, tmp_path)
    desktop = tmp_path / run_at_startup.DESKTOP_FILE_NAME

    assert run_at_startup.is_enabled() is False

    ok, msg = run_at_startup.enable()
    assert ok
    assert desktop.exists()
    assert run_at_startup.STARTUP_FLAG in msg
    assert run_at_startup.is_enabled() is True

    ok, _ = run_at_startup.disable()
    assert ok
    assert not desktop.exists()
    assert run_at_startup.is_enabled() is False

    # Disabling when already absent is a no-op success.
    ok, _ = run_at_startup.disable()
    assert ok


def test_linux_desktop_entry_is_well_formed(monkeypatch, tmp_path):
    _force_linux(monkeypatch, tmp_path)
    run_at_startup.enable()
    text = (tmp_path / run_at_startup.DESKTOP_FILE_NAME).read_text(encoding="utf-8")

    assert text.startswith("[Desktop Entry]")
    assert "Type=Application" in text
    assert "X-GNOME-Autostart-enabled=true" in text
    exec_line = next(ln for ln in text.splitlines() if ln.startswith("Exec="))
    assert run_at_startup.STARTUP_FLAG in exec_line
    assert exec_line.count('"') >= 2          # the binary/script path is quoted


def test_linux_exec_escapes_special_chars(monkeypatch, tmp_path):
    # A binary path with a space, a $ and a % must be quoted + escaped so the
    # .desktop Exec= line can't be misparsed (freedesktop field-code rules).
    _force_linux(monkeypatch, tmp_path)
    monkeypatch.setattr(run_at_startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(run_at_startup.sys, "executable", "/opt/Clawd $x/100% app")
    monkeypatch.delenv("APPIMAGE", raising=False)
    exec_cmd = run_at_startup._linux_exec()
    assert "\\$" in exec_cmd                   # $ escaped
    assert "%%" in exec_cmd                    # literal % doubled
    assert exec_cmd.endswith(run_at_startup.STARTUP_FLAG)


@requires_winreg
def test_enable_is_enabled_disable_round_trip(temp_run_key):
    assert run_at_startup.is_enabled() is False

    ok, value = run_at_startup.enable()
    assert ok
    assert value == run_at_startup.launch_command()
    assert run_at_startup.is_enabled() is True

    # The value really landed under the patched key with the expected name.
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _TEST_KEY) as key:
        stored, _ = winreg.QueryValueEx(key, run_at_startup.VALUE_NAME)
    assert stored == run_at_startup.launch_command()

    ok, _ = run_at_startup.disable()
    assert ok
    assert run_at_startup.is_enabled() is False


@requires_winreg
def test_enable_is_idempotent(temp_run_key):
    run_at_startup.enable()
    ok, _ = run_at_startup.enable()  # second call must not error
    assert ok
    assert run_at_startup.is_enabled() is True
    run_at_startup.disable()


@requires_winreg
def test_disable_when_absent_is_success(temp_run_key):
    # No key/value exists yet; disabling should be a no-op success.
    ok, _ = run_at_startup.disable()
    assert ok
    assert run_at_startup.is_enabled() is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --- macOS SMAppService branch ---------------------------------------------
# The plist tests above still exercise the fallback: macos_login_item.available()
# is False off a frozen Mac, so _force_macos alone routes to the plist.

class _FakeLoginItem:
    """Stands in for the macos_login_item module."""

    def __init__(self, avail=True, register_ok=True):
        self._avail = avail
        self._register_ok = register_ok
        self.enabled = False
        self.registered = 0
        self.unregistered = 0

    def available(self):
        return self._avail

    def is_enabled(self):
        return self.enabled

    def register(self):
        self.registered += 1
        if not self._register_ok:
            return False, "denied"
        self.enabled = True
        return True, "registered as a login item"

    def unregister(self):
        self.unregistered += 1
        self.enabled = False
        return True, ""


def _force_macos_sm(monkeypatch, tmp_path, **kw):
    _force_macos(monkeypatch, tmp_path)
    fake = _FakeLoginItem(**kw)
    monkeypatch.setattr(run_at_startup, "macos_login_item", fake)
    monkeypatch.setattr(run_at_startup.sys, "frozen", True, raising=False)
    return fake


def test_macos_enable_prefers_smappservice_over_a_plist(monkeypatch, tmp_path):
    fake = _force_macos_sm(monkeypatch, tmp_path)
    ok, _ = run_at_startup.enable()
    assert ok and fake.registered == 1
    # No plist written: two live mechanisms would launch the app twice.
    assert not (tmp_path / run_at_startup.PLIST_FILE_NAME).exists()
    assert run_at_startup.is_enabled() is True


def test_macos_enable_clears_a_legacy_plist(monkeypatch, tmp_path):
    fake = _force_macos_sm(monkeypatch, tmp_path)
    plist = tmp_path / run_at_startup.PLIST_FILE_NAME
    plist.write_text("<plist/>", encoding="utf-8")

    ok, _ = run_at_startup.enable()
    assert ok and fake.registered == 1
    assert not plist.exists()


def test_macos_a_legacy_plist_still_reads_as_enabled(monkeypatch, tmp_path):
    """It launches the app at login, whatever SMAppService thinks."""
    _force_macos_sm(monkeypatch, tmp_path)
    (tmp_path / run_at_startup.PLIST_FILE_NAME).write_text("<plist/>",
                                                           encoding="utf-8")
    assert run_at_startup.is_enabled() is True


def test_macos_disable_clears_both_mechanisms(monkeypatch, tmp_path):
    fake = _force_macos_sm(monkeypatch, tmp_path)
    fake.enabled = True
    plist = tmp_path / run_at_startup.PLIST_FILE_NAME
    plist.write_text("<plist/>", encoding="utf-8")

    ok, _ = run_at_startup.disable()
    assert ok and fake.unregistered == 1
    assert not plist.exists()
    assert run_at_startup.is_enabled() is False


def test_macos_enable_failure_does_not_delete_the_plist(monkeypatch, tmp_path):
    """A refused registration must leave the working plist in place."""
    _force_macos_sm(monkeypatch, tmp_path, register_ok=False)
    plist = tmp_path / run_at_startup.PLIST_FILE_NAME
    plist.write_text("<plist/>", encoding="utf-8")

    ok, msg = run_at_startup.enable()
    assert ok is False and msg == "denied"
    assert plist.exists()


def test_macos_migration_converts_a_legacy_plist(monkeypatch, tmp_path):
    fake = _force_macos_sm(monkeypatch, tmp_path)
    plist = tmp_path / run_at_startup.PLIST_FILE_NAME
    plist.write_text("<plist/>", encoding="utf-8")

    run_at_startup.migrate_macos_login_item()
    assert fake.registered == 1
    assert not plist.exists()
    assert run_at_startup.is_enabled() is True


def test_macos_migration_is_a_no_op_without_a_plist(monkeypatch, tmp_path):
    fake = _force_macos_sm(monkeypatch, tmp_path)
    run_at_startup.migrate_macos_login_item()
    assert fake.registered == 0
    assert run_at_startup.is_enabled() is False


def test_macos_migration_keeps_the_plist_if_registering_fails(monkeypatch, tmp_path):
    fake = _force_macos_sm(monkeypatch, tmp_path, register_ok=False)
    plist = tmp_path / run_at_startup.PLIST_FILE_NAME
    plist.write_text("<plist/>", encoding="utf-8")

    run_at_startup.migrate_macos_login_item()
    assert fake.registered == 1
    assert plist.exists()          # run-at-login keeps working either way


def test_macos_sync_leaves_smappservice_alone(monkeypatch, tmp_path):
    """Re-pointing ProgramArguments is a plist problem; SMAppService tracks the
    bundle, so sync must not resurrect a plist for a registered app."""
    _force_macos_sm(monkeypatch, tmp_path)
    run_at_startup.sync_if_enabled()
    assert not (tmp_path / run_at_startup.PLIST_FILE_NAME).exists()


def test_macos_sync_still_repoints_the_fallback_plist(monkeypatch, tmp_path):
    _force_macos_sm(monkeypatch, tmp_path, avail=False)
    plist = tmp_path / run_at_startup.PLIST_FILE_NAME
    plist.write_text("stale", encoding="utf-8")

    run_at_startup.sync_if_enabled()
    assert "RunAtLoad" in plist.read_text(encoding="utf-8")


def test_macos_migration_is_a_no_op_off_macos(monkeypatch, tmp_path):
    fake = _force_macos_sm(monkeypatch, tmp_path)
    monkeypatch.setattr(run_at_startup, "_is_macos", lambda: False)
    (tmp_path / run_at_startup.PLIST_FILE_NAME).write_text("x", encoding="utf-8")

    run_at_startup.migrate_macos_login_item()
    assert fake.registered == 0
