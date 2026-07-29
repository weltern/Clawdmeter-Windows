"""The main window reopens at the size it was left at.

Every other window already remembered something -- the mini and compact views
persist their position -- but the main window hard-coded resize(520+rail, 520)
on every launch, so a user who widened it to see more shelf tiles got it back
at 520 the next morning.

The interesting part is not the persistence, it is the interaction with
_fit_window_height(). The window normally hugs its content and only stops once
the user drags the height themselves (_auto_fit_height releases in
resizeEvent). So the height is only worth restoring in that released state:
restoring it unconditionally would freeze a height captured while the shelf
held four mascots onto a launch that has none. Width has no such tension --
nothing computes it -- so width is always restored.

These tests drive SettingsPanel's owner, Dashboard, through its real resize
plumbing rather than asserting on the settings functions alone, because the
bug this guards against lives in the ordering: restore has to release the fit
BEFORE it resizes, or construction's own _fit_window_height() snaps the
restored height straight back.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from PySide6.QtCore import QRect, QSize  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import app_settings  # noqa: E402
import dashboard  # noqa: E402

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    """A real QSettings backed by a throwaway ini, so these tests neither read
    nor scribble on the developer's own saved window size."""
    from PySide6.QtCore import QSettings
    store = QSettings(str(tmp_path / "t.ini"), QSettings.IniFormat)
    monkeypatch.setattr(app_settings, "_settings", lambda: store)
    yield store


def test_nothing_saved_means_no_restore():
    assert app_settings.get_main_size() is None


def test_a_saved_size_round_trips():
    app_settings.set_main_size(880, 640)
    assert app_settings.get_main_size() == (880, 640)


@pytest.mark.parametrize("junk", ["", "not,ints", "900", "0,0", "-5,-5"])
def test_corrupt_or_absent_values_are_ignored(junk, _isolated_settings):
    """A bad value must read as "never saved", not crash the app at startup."""
    _isolated_settings.setValue(app_settings.KEY_MAIN_SIZE, junk)
    assert app_settings.get_main_size() is None


class _FakeWindow:
    """The parts of Dashboard that _restore_window_size touches.

    Calling the real method unbound against this is deliberate: it exercises
    the shipped code, but without constructing a Dashboard, which starts
    pollers and file watchers and leaks global state into other test modules.
    """

    _restore_window_size = dashboard.Dashboard._restore_window_size
    _save_window_size = dashboard.Dashboard._save_window_size

    def __init__(self, *, avail=QRect(0, 0, 1920, 1080), min_w=668, min_h=0):
        self._avail, self._min_w, self._min_h = avail, min_w, min_h
        self._auto_fit_height = True
        self.resized_to: tuple[int, int] | None = None
        self._h, self._w = 520, 668
        self._maximized = self._fullscreen = False
        self._normal = QSize(0, 0)

    def screen(self):
        outer = self

        class _S:
            def availableGeometry(self):
                return outer._avail
        return _S()

    def minimumWidth(self):
        return self._min_w

    def minimumHeight(self):
        return self._min_h

    def height(self):
        return self._h

    def width(self):
        return self._w

    def size(self):
        return QSize(self._w, self._h)

    def isMaximized(self):
        return self._maximized

    def isFullScreen(self):
        return self._fullscreen

    def normalGeometry(self):
        return QRect(0, 0, self._normal.width(), self._normal.height())

    def resize(self, w, h):
        self.resized_to = (w, h)
        self._w, self._h = w, h


def test_width_is_restored_even_when_the_height_is_still_auto_fitting():
    app_settings.set_main_size(1100, 900)
    app_settings.set_main_height_manual(False)
    win = _FakeWindow()
    win._restore_window_size()
    assert win.resized_to == (1100, 520), \
        "width must be restored, and the height left to the content fit"
    assert win._auto_fit_height is True, "the fit must not be released"


def test_height_is_restored_once_the_user_has_taken_it_over():
    app_settings.set_main_size(1100, 900)
    app_settings.set_main_height_manual(True)
    win = _FakeWindow()
    win._restore_window_size()
    assert win.resized_to == (1100, 900)
    assert win._auto_fit_height is False, (
        "the fit has to be released BEFORE the resize, or construction's own "
        "_fit_window_height() snaps the restored height straight back")


def test_a_size_from_a_bigger_monitor_is_clamped_to_this_one():
    """The saved size can outlive the display it was made on."""
    app_settings.set_main_size(3400, 1900)          # a 4K panel
    app_settings.set_main_height_manual(True)
    win = _FakeWindow(avail=QRect(0, 0, 1366, 768))  # ...restored on a laptop
    win._restore_window_size()
    assert win.resized_to == (1366, 768), \
        "a window taller than the screen puts its resize edge out of reach"


def test_the_minimum_size_still_wins():
    app_settings.set_main_size(100, 50)
    app_settings.set_main_height_manual(True)
    win = _FakeWindow(min_w=668, min_h=300)
    win._restore_window_size()
    assert win.resized_to == (668, 300)


def test_saving_records_whether_the_height_was_manual():
    win = _FakeWindow()
    win._w, win._h = 900, 700
    win._auto_fit_height = False
    win._save_window_size()
    assert app_settings.get_main_size() == (900, 700)
    assert app_settings.get_main_height_manual() is True

    win._auto_fit_height = True
    win._save_window_size()
    assert app_settings.get_main_height_manual() is False


def test_a_maximised_window_saves_the_size_it_restores_down_to():
    """Otherwise the next launch opens at the maximised size and un-maximising
    does nothing visible."""
    win = _FakeWindow()
    win._w, win._h = 1920, 1080      # currently filling the screen
    win._normal = QSize(820, 610)    # what the green/restore button returns to
    win._maximized = True
    win._save_window_size()
    assert app_settings.get_main_size() == (820, 610)


def test_the_plumbing_is_actually_wired_up():
    """The tests above call the methods directly, so every one of them would
    still pass if nothing in Dashboard ever reached them. This is the check
    that they are called at all -- source inspection rather than behaviour,
    because constructing a real Dashboard starts pollers and file watchers and
    leaks global state into other test modules.
    """
    import inspect
    init = inspect.getsource(dashboard.Dashboard.__init__)
    assert "self._restore_window_size()" in init, \
        "nothing restores the saved size at startup"
    assert "self._size_save_timer" in init, "the debounce timer is not created"

    resize = inspect.getsource(dashboard.Dashboard.resizeEvent)
    assert "_size_save_timer.start()" in resize, \
        "resizing no longer schedules a save, so only a clean quit persists"

    quit_src = inspect.getsource(dashboard.Dashboard._real_quit)
    assert "self._save_window_size()" in quit_src, \
        "quitting inside the debounce window would drop the last resize"


def test_a_maximised_window_with_no_normal_geometry_saves_nothing():
    """Rather than persisting a 0x0 that would restore to the minimum."""
    app_settings.set_main_size(880, 640)
    win = _FakeWindow()
    win._maximized = True
    win._normal = QSize(0, 0)
    win._save_window_size()
    assert app_settings.get_main_size() == (880, 640), "the good value survived"
