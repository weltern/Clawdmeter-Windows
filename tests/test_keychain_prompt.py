"""The macOS Keychain read has to survive a human answering a dialog.

The first read on a machine pops a Keychain authorization prompt and `security`
blocks until it is answered. A 10-second timeout killed that process mid-prompt,
so SecurityAgent had no live client to deliver the approval to — it discarded
the grant and re-presented. Observed on real hardware 2026-07-26: four "Allow"
and two "Always Allow" clicks produced zero ACL grants and the dashboard never
received a token.

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import macos_keychain as mk  # noqa: E402


def _fake_run(delay=0.0, returncode=0, stdout="blob"):
    def run(*args, **kwargs):
        if delay:
            time.sleep(delay)
        timeout = kwargs.get("timeout")
        if timeout is not None and delay > timeout:
            raise subprocess.TimeoutExpired(args, timeout)
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")
    return run


def test_there_is_no_timeout_on_the_interactive_read_by_default():
    # The regression, measured twice on hardware: killing `security` while the
    # Keychain dialog is open leaves SecurityAgent with no client to hand the
    # approval to, so the grant is discarded and the user can NEVER authorise
    # the app. A 10s limit failed (6 clicks, 0 grants); raising it to 120s and
    # answering at 177s failed identically. Any fixed number is a guess about
    # how fast a human reads a dialog, so there must be no default fuse at all.
    assert mk._SECURITY_TIMEOUT_SECONDS is None, (
        "a fixed timeout on an interactive prompt kills the request mid-dialog "
        "and discards the user's grant"
    )


def test_timeout_is_overridable_for_testing(monkeypatch):
    monkeypatch.setenv("CLAWD_KEYCHAIN_TIMEOUT", "5")
    import importlib
    reloaded = importlib.reload(mk)
    try:
        assert reloaded._SECURITY_TIMEOUT_SECONDS == 5
    finally:
        monkeypatch.delenv("CLAWD_KEYCHAIN_TIMEOUT", raising=False)
        importlib.reload(mk)


def test_the_timeout_is_actually_passed_to_subprocess(monkeypatch):
    seen = {}

    def run(*args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args, 0, stdout="blob", stderr="")

    monkeypatch.setattr(mk, "is_macos", lambda: True)
    monkeypatch.setattr(mk.subprocess, "run", run)
    mk.read_credentials()
    assert seen["timeout"] == mk._SECURITY_TIMEOUT_SECONDS


def test_only_one_security_process_in_flight(monkeypatch):
    # A poll every 60s must not stack a second authorization request behind the
    # dialog the user is still reading.
    monkeypatch.setattr(mk, "is_macos", lambda: True)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def run(*args, **kwargs):
        calls.append(1)
        started.set()
        release.wait(5)
        return subprocess.CompletedProcess(args, 0, stdout="blob", stderr="")

    monkeypatch.setattr(mk.subprocess, "run", run)

    t = threading.Thread(target=mk.read_credentials, daemon=True)
    t.start()
    assert started.wait(5), "first read never started"

    # Second concurrent read must bail immediately rather than spawn another.
    assert mk.read_credentials() is None
    assert len(calls) == 1, "a second `security` was spawned while one was pending"

    release.set()
    t.join(5)

    # ...and the lock must be released, so later reads still work.
    monkeypatch.setattr(mk.subprocess, "run", _fake_run(returncode=0, stdout="blob"))
    assert mk.read_credentials() == "blob"


def test_lock_is_released_when_security_raises(monkeypatch):
    monkeypatch.setattr(mk, "is_macos", lambda: True)
    monkeypatch.setattr(mk.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert mk.read_credentials() is None
    # A raised error must not leave the single-flight lock held forever.
    monkeypatch.setattr(mk.subprocess, "run", _fake_run(returncode=0, stdout="blob"))
    assert mk.read_credentials() == "blob"


def test_interaction_not_allowed_code_is_documented():
    # 36 == errSecInteractionNotAllowed (-25308 & 0xFF): the OS had to prompt but
    # had no GUI session. Confirmed over SSH on hardware.
    assert mk.RC_INTERACTION_NOT_ALLOWED == 36
    assert (-25308 & 0xFF) == mk.RC_INTERACTION_NOT_ALLOWED


def test_nonzero_returncode_still_reads_as_no_credentials(monkeypatch):
    monkeypatch.setattr(mk, "is_macos", lambda: True)
    for rc in (36, 44, 45):
        monkeypatch.setattr(mk.subprocess, "run", _fake_run(returncode=rc, stdout=""))
        assert mk.read_credentials() is None


# --- Security-framework path (macOS) ----------------------------------------

def test_framework_path_is_preferred_over_the_cli(monkeypatch):
    # The CLI must not run when the framework can answer: the dialog would name
    # "security" instead of Clawdmeter, and an Always Allow grant would attach
    # to that shared binary rather than to us.
    monkeypatch.setattr(mk, "is_macos", lambda: True)
    monkeypatch.setattr(mk, "_read_via_framework", lambda: "from-framework")
    monkeypatch.setattr(mk.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("CLI ran despite the framework working")))
    assert mk.read_credentials() == "from-framework"


def test_falls_back_to_the_cli_only_when_the_binding_is_absent(monkeypatch):
    # _read_via_framework returns the sentinel False for "binding not bundled".
    monkeypatch.setattr(mk, "is_macos", lambda: True)
    monkeypatch.setattr(mk, "_read_via_framework", lambda: False)
    monkeypatch.setattr(mk.subprocess, "run", _fake_run(returncode=0, stdout="from-cli"))
    assert mk.read_credentials() == "from-cli"


def test_framework_none_is_not_treated_as_missing_binding(monkeypatch):
    # None means "no credentials" and must NOT trigger the CLI fallback —
    # otherwise a denied read would pop a second prompt naming "security".
    monkeypatch.setattr(mk, "is_macos", lambda: True)
    monkeypatch.setattr(mk, "_read_via_framework", lambda: None)
    monkeypatch.setattr(mk.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("CLI ran after a definitive None")))
    assert mk.read_credentials() is None


def test_single_flight_covers_the_framework_path_too(monkeypatch):
    monkeypatch.setattr(mk, "is_macos", lambda: True)
    started, release, calls = threading.Event(), threading.Event(), []

    def slow_framework():
        calls.append(1)
        started.set()
        release.wait(5)
        return "blob"

    monkeypatch.setattr(mk, "_read_via_framework", slow_framework)
    t = threading.Thread(target=mk.read_credentials, daemon=True)
    t.start()
    assert started.wait(5)
    assert mk.read_credentials() is None      # second read bails immediately
    assert len(calls) == 1
    release.set()
    t.join(5)


def test_lock_is_released_if_the_framework_path_raises(monkeypatch):
    monkeypatch.setattr(mk, "is_macos", lambda: True)
    monkeypatch.setattr(mk, "_read_via_framework",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        mk.read_credentials()
    except RuntimeError:
        pass
    monkeypatch.setattr(mk, "_read_via_framework", lambda: "ok")
    assert mk.read_credentials() == "ok", "lock left held after an exception"
