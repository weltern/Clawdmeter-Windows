"""SMAppService login-item registration (macOS run-at-login).

Everything here runs against a fake ServiceManagement module injected into
sys.modules, so the real Apple framework is only needed on a Mac. The live
behaviour these fakes stand in for was verified on macOS 15.6.1 with an ad-hoc
signed build: NotFound -> Enabled on register, NotRegistered after unregister.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import macos_login_item as mli  # noqa: E402


class _FakeError:
    def __init__(self, desc):
        self._d = desc

    def localizedDescription(self):
        return self._d


class _FakeService:
    """Stands in for SMAppService.mainAppService()."""

    def __init__(self, status=mli.STATUS_NOT_REGISTERED, register_ok=True,
                 after_register=mli.STATUS_ENABLED, unregister_ok=True):
        self._status = status
        self._register_ok = register_ok
        self._after_register = after_register
        self._unregister_ok = unregister_ok
        self.registered = 0
        self.unregistered = 0

    def status(self):
        return self._status

    def registerAndReturnError_(self, _):
        self.registered += 1
        if not self._register_ok:
            return False, _FakeError("boom")
        self._status = self._after_register
        return True, None

    def unregisterAndReturnError_(self, _):
        self.unregistered += 1
        if not self._unregister_ok:
            return False, _FakeError("nope")
        self._status = mli.STATUS_NOT_REGISTERED
        return True, None


@pytest.fixture
def fake_sm(monkeypatch):
    """Install a fake ServiceManagement framework and pose as a frozen Mac app."""
    svc = _FakeService()
    opened = []

    class _SMAppService:
        @staticmethod
        def mainAppService():
            return svc

        @staticmethod
        def openSystemSettingsLoginItems():
            opened.append(True)

    mod = types.ModuleType("ServiceManagement")
    mod.SMAppService = _SMAppService
    monkeypatch.setitem(sys.modules, "ServiceManagement", mod)
    monkeypatch.setattr(mli.sys, "platform", "darwin")
    monkeypatch.setattr(mli.sys, "frozen", True, raising=False)
    svc.opened_settings = opened
    return svc


def test_available_only_when_frozen_on_macos(fake_sm, monkeypatch):
    assert mli.available() is True

    # A dev checkout has no .app around the interpreter, so mainAppService would
    # resolve to Python's own framework bundle -- registering *that* at login.
    monkeypatch.setattr(mli.sys, "frozen", False, raising=False)
    assert mli.available() is False
    assert mli.status() is None
    assert mli.is_enabled() is False


def test_available_false_off_macos(fake_sm, monkeypatch):
    monkeypatch.setattr(mli.sys, "platform", "win32")
    assert mli.available() is False


def test_available_false_without_the_binding(monkeypatch):
    """No pyobjc ServiceManagement (or macOS 12) -> caller falls back to a plist."""
    monkeypatch.setattr(mli.sys, "platform", "darwin")
    monkeypatch.setattr(mli.sys, "frozen", True, raising=False)
    monkeypatch.setitem(sys.modules, "ServiceManagement", None)
    assert mli.available() is False


def test_register_round_trip(fake_sm):
    assert mli.is_enabled() is False
    ok, _ = mli.register()
    assert ok and mli.is_enabled() is True

    ok, _ = mli.unregister()
    assert ok and mli.is_enabled() is False


def test_register_is_skipped_when_already_enabled(fake_sm):
    mli.register()
    assert fake_sm.registered == 1
    ok, _ = mli.register()
    assert ok and fake_sm.registered == 1     # not re-registered


def test_requires_approval_is_not_reported_as_enabled(fake_sm):
    """The user switched us off in System Settings; macOS won't silently re-arm."""
    fake_sm._after_register = mli.STATUS_REQUIRES_APPROVAL
    ok, msg = mli.register()
    assert ok is False
    assert "System Settings" in msg
    assert fake_sm.opened_settings == [True]  # sent them to the right pane
    assert mli.is_enabled() is False


def test_register_failure_surfaces_the_reason(fake_sm):
    fake_sm._register_ok = False
    ok, msg = mli.register()
    assert ok is False and "boom" in msg


def test_unregister_when_never_registered_is_success(fake_sm):
    fake_sm._unregister_ok = False            # framework reports failure...
    fake_sm._status = mli.STATUS_NOT_FOUND    # ...but we're already in the
    ok, _ = mli.unregister()                  #    state the caller asked for
    assert ok is True


def test_status_survives_a_throwing_framework(fake_sm, monkeypatch):
    def _boom():
        raise RuntimeError("framework exploded")
    monkeypatch.setattr(fake_sm, "status", _boom)
    assert mli.status() is None               # never crashes the Settings panel
    assert mli.is_enabled() is False
