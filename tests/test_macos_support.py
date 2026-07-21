"""Tests for the macOS credential path (Keychain read) and the macOS-aware
token-refresh guard.

The real ``sys.platform == 'darwin'`` branches can't run on the Windows/Linux
CI box, so these force macOS on via monkeypatching the platform-detection seam
(``macos_keychain.is_macos`` and the ``security`` subprocess) — exercising the
darwin logic on any host. No network, no real Keychain touched.

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import macos_keychain  # noqa: E402
import poller  # noqa: E402
import token_refresh as tr  # noqa: E402


def _fake_run(returncode=0, stdout=""):
    """A subprocess.run stand-in returning a canned CompletedProcess."""
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")
    return run


# --- macos_keychain ---------------------------------------------------------

def test_read_credentials_none_off_macos(monkeypatch):
    monkeypatch.setattr(macos_keychain, "is_macos", lambda: False)
    assert macos_keychain.read_credentials() is None


def test_service_name_env_override(monkeypatch):
    monkeypatch.setenv("CLAUDE_KEYCHAIN_SERVICE", "Custom-Service")
    assert macos_keychain.service_name() == "Custom-Service"
    monkeypatch.delenv("CLAUDE_KEYCHAIN_SERVICE", raising=False)
    assert macos_keychain.service_name() == macos_keychain.DEFAULT_SERVICE_NAME


def test_read_credentials_returns_blob(monkeypatch):
    blob = json.dumps({"claudeAiOauth": {"accessToken": "sk-mac"}})
    monkeypatch.setattr(macos_keychain, "is_macos", lambda: True)
    monkeypatch.setattr(macos_keychain.subprocess, "run",
                        _fake_run(0, stdout=blob + "\n"))
    assert macos_keychain.read_credentials() == blob


def test_read_credentials_none_on_nonzero(monkeypatch):
    # returncode 44 == item not found.
    monkeypatch.setattr(macos_keychain, "is_macos", lambda: True)
    monkeypatch.setattr(macos_keychain.subprocess, "run", _fake_run(44, stdout=""))
    assert macos_keychain.read_credentials() is None


def test_read_credentials_none_when_security_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("security not found")
    monkeypatch.setattr(macos_keychain, "is_macos", lambda: True)
    monkeypatch.setattr(macos_keychain.subprocess, "run", boom)
    assert macos_keychain.read_credentials() is None


def test_read_credentials_none_on_timeout(monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="security", timeout=10)
    monkeypatch.setattr(macos_keychain, "is_macos", lambda: True)
    monkeypatch.setattr(macos_keychain.subprocess, "run", timeout)
    assert macos_keychain.read_credentials() is None


# --- poller: token read comes from the Keychain on macOS --------------------

def test_read_token_prefers_keychain_on_macos(monkeypatch):
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)
    monkeypatch.setattr(poller.macos_keychain, "is_macos", lambda: True)
    monkeypatch.setattr(
        poller.macos_keychain, "read_credentials",
        lambda: json.dumps({"claudeAiOauth": {"accessToken": "sk-from-keychain"}}),
    )
    assert poller.read_token() == "sk-from-keychain"


def test_read_token_override_beats_keychain_on_macos(monkeypatch, tmp_path):
    # An explicit CLAUDE_CREDENTIALS_PATH file wins even on macOS.
    cred = tmp_path / ".credentials.json"
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "sk-from-file"}}),
                    encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CREDENTIALS_PATH", str(cred))
    monkeypatch.setattr(poller.macos_keychain, "is_macos", lambda: True)
    monkeypatch.setattr(poller.macos_keychain, "read_credentials",
                        lambda: '{"claudeAiOauth": {"accessToken": "sk-from-keychain"}}')
    assert poller.read_token() == "sk-from-file"


def test_token_source_description_macos(monkeypatch):
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)
    monkeypatch.setattr(poller.macos_keychain, "is_macos", lambda: True)
    monkeypatch.setattr(poller.macos_keychain, "service_name",
                        lambda: "Claude Code-credentials")
    desc = poller.token_source_description()
    assert "Keychain" in desc and "Claude Code-credentials" in desc


# --- token_refresh: macOS is read-only for now ------------------------------

def test_refresh_macos_returns_reauth_message(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)
    monkeypatch.setattr(tr.macos_keychain, "is_macos", lambda: True)
    res = tr.refresh(tmp_path / "does-not-exist.json")
    assert res.ok is False
    assert "macOS" in res.status


def test_token_expiry_ms_reads_keychain_on_macos(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CREDENTIALS_PATH", raising=False)
    monkeypatch.setattr(tr.macos_keychain, "is_macos", lambda: True)
    monkeypatch.setattr(
        tr.macos_keychain, "read_credentials",
        lambda: json.dumps({"claudeAiOauth": {"accessToken": "a", "expiresAt": 987654}}),
    )
    # The path argument doesn't exist — value must come from the Keychain.
    assert tr.token_expiry_ms(tmp_path / "nope.json") == 987654


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]

    class _MP:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def setenv(self, k, v):
            self._undo.append((os.environ, k, os.environ.get(k)))
            os.environ[k] = v

        def delenv(self, k, raising=True):
            self._undo.append((os.environ, k, os.environ.get(k)))
            os.environ.pop(k, None)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                if obj is os.environ:
                    if old is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = old
                else:
                    setattr(obj, name, old)

    import inspect
    passed = 0
    for name, fn in fns:
        params = inspect.signature(fn).parameters
        mp = _MP()
        with tempfile.TemporaryDirectory() as d:
            kwargs = {}
            if "monkeypatch" in params:
                kwargs["monkeypatch"] = mp
            if "tmp_path" in params:
                kwargs["tmp_path"] = Path(d)
            try:
                fn(**kwargs)
            finally:
                mp.undo()
        print(f"ok  {name}")
        passed += 1
    print(f"\n{passed} passed")
