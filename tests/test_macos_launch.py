"""Sign-in-launch detection on macOS.

The Apple Event shapes asserted here are the ones measured on macOS 15.6.1
inside a real PySide6 app:

    login:   eventID='oapp'  keyAEPropData='lgit'
    normal:  eventID='oapp'  keyAEPropData absent
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import macos_launch  # noqa: E402


class _Desc:
    def __init__(self, code):
        self._code = code

    def enumCodeValue(self):
        return self._code


class _Event:
    """Stands in for an NSAppleEventDescriptor."""

    def __init__(self, event_id, prop=None):
        self._id = event_id
        self._prop = prop

    def eventID(self):
        return self._id

    def paramDescriptorForKeyword_(self, kw):
        if kw == macos_launch.KEY_AE_PROP_DATA:
            return self._prop
        return None


def test_login_launch_event_is_recognised():
    evt = _Event(macos_launch.KAE_OPEN_APPLICATION,
                 _Desc(macos_launch.KEY_AE_LAUNCHED_AS_LOGIN_ITEM))
    assert macos_launch.is_login_launch_event(evt) is True


def test_a_normal_launch_carries_no_prop_data():
    evt = _Event(macos_launch.KAE_OPEN_APPLICATION, None)
    assert macos_launch.is_login_launch_event(evt) is False


def test_a_different_prop_code_is_not_a_login_launch():
    evt = _Event(macos_launch.KAE_OPEN_APPLICATION, _Desc(0x12345678))
    assert macos_launch.is_login_launch_event(evt) is False


def test_a_different_event_is_not_a_login_launch():
    """Only the open-application event says anything about the launch source."""
    evt = _Event(int.from_bytes(b"odoc", "big"),
                 _Desc(macos_launch.KEY_AE_LAUNCHED_AS_LOGIN_ITEM))
    assert macos_launch.is_login_launch_event(evt) is False


def test_no_event_is_not_a_login_launch():
    assert macos_launch.is_login_launch_event(None) is False


def test_a_malformed_event_does_not_raise():
    class _Broken:
        def eventID(self):
            raise RuntimeError("nope")

    assert macos_launch.is_login_launch_event(_Broken()) is False


def test_the_four_char_codes_match_apple_s():
    # Wrong constants would silently mean "never a login launch", which looks
    # exactly like the bug this module fixes.
    assert macos_launch.KAE_OPEN_APPLICATION == 0x6F617070          # 'oapp'
    assert macos_launch.KEY_AE_PROP_DATA == 0x70726474              # 'prdt'
    assert macos_launch.KEY_AE_LAUNCHED_AS_LOGIN_ITEM == 0x6C676974  # 'lgit'


def test_the_verdict_is_delivered_once():
    """The notification and the fallback timer race by design."""
    seen = []
    d = macos_launch._Decision(seen.append)
    d.settle(True)
    d.settle(False)      # fallback arriving late must not overrule it
    d.settle(True)
    assert seen == [True]
    assert d.settled is True


def test_detect_answers_immediately_off_macos(monkeypatch):
    monkeypatch.setattr(macos_launch.sys, "platform", "win32")
    seen = []
    macos_launch.detect(seen.append)
    assert seen == [False]      # show the window, same as before


def test_detect_falls_back_when_cocoa_is_missing(monkeypatch):
    """No pyobjc bundled: answer False rather than leaving the app windowless."""
    monkeypatch.setattr(macos_launch.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Foundation", None)
    seen = []
    macos_launch.detect(seen.append)
    assert seen == [False]
