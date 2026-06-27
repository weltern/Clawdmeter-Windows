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


def test_is_supported_is_true_on_windows():
    assert run_at_startup.is_supported() is (winreg is not None)


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
