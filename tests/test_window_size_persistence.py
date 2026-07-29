"""The main window keeps the size the user gave it, and nothing else moves it.

Two changes are guarded here, and they are two halves of one behaviour.

The window used to resize itself to hug its content: it followed the mascot
shelf as sessions came and went, grew when the rate-limit badge appeared, and
re-snapped whenever you returned to the Dashboard page. That is a normal
pattern for a menu-bar/tray utility, but the triggers were background events,
so the window moved while the user was doing something else -- and it got
worse as the mascots gained more range to expand and shrink. All three
triggers are gone. Only two things size the window now: a one-time snap on
first run, and the user double-clicking the title bar (reset_to_fit).

With nothing computing the height any more, a saved height is simply the
user's height, so both dimensions are restored unconditionally. An earlier
version of this file had an elaborate "restore the height only if they took
manual control" rule; that existed solely because the window used to re-fit
itself, and it went away with the auto-fit.

These tests drive the real methods rather than asserting on the settings
functions alone, because the bug worth guarding lives in the wiring: a
first-run snap that also runs after a restore would throw the user's height
away on every single launch.
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
    """The parts of Dashboard that the size methods touch.

    Calling the real methods unbound against this is deliberate: it exercises
    the shipped code, but without constructing a Dashboard, which starts
    pollers and file watchers and leaks global state into other test modules.
    """

    _restore_window_size = dashboard.Dashboard._restore_window_size
    _save_window_size = dashboard.Dashboard._save_window_size
    _titlebar_height_now = dashboard.Dashboard._titlebar_height_now

    # Derived from the real class rather than copied: an earlier version of this
    # fake hard-coded 668, which was stale by NavRail.COLLAPSED and drifting.
    REAL_MIN_W = 520 + dashboard.NavRail.COLLAPSED       # dashboard.py setMinimumSize

    def __init__(self, *, avail=QRect(0, 0, 1920, 1080), min_w=REAL_MIN_W,
                 min_h=0, auto_hide=False):
        self._avail, self._min_w, self._min_h = avail, min_w, min_h
        self.resized_to: tuple[int, int] | None = None
        self._h, self._w = 520, self.REAL_MIN_W
        self._maximized = self._fullscreen = False
        self._normal = QSize(0, 0)
        # Auto-hide state: the size methods normalise the title bar out of the
        # stored height. Default off, which is the shipped default.
        self._auto_hide_enabled = auto_hide
        self._collapsed_window_height = None

        class _Bar:
            def maximumHeight(_s):
                return 0 if auto_hide else dashboard.TitleBar.HEIGHT
        self.title_bar = _Bar()

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


def test_both_dimensions_are_restored():
    """Nothing computes the height any more, so a saved height is the user's."""
    app_settings.set_main_size(1100, 900)
    win = _FakeWindow()
    assert win._restore_window_size() is True
    assert win.resized_to == (1100, 900)


def test_restore_reports_whether_it_applied_a_size():
    """The return value gates the first-run content snap. If it ever lies by
    returning False after restoring, the snap runs on top and the user's
    height is discarded on every launch."""
    win = _FakeWindow()
    assert win._restore_window_size() is False, "nothing saved -- must report so"
    assert win.resized_to is None

    app_settings.set_main_size(900, 700)
    assert _FakeWindow()._restore_window_size() is True


def test_a_size_from_a_bigger_monitor_is_clamped_to_this_one():
    """The saved size can outlive the display it was made on."""
    app_settings.set_main_size(3400, 1900)          # a 4K panel
    win = _FakeWindow(avail=QRect(0, 0, 1366, 768))  # ...restored on a laptop
    win._restore_window_size()
    assert win.resized_to == (1366, 768), \
        "a window taller than the screen puts its resize edge out of reach"


def test_the_minimum_size_still_wins():
    app_settings.set_main_size(100, 50)
    win = _FakeWindow(min_w=668, min_h=300)
    win._restore_window_size()
    assert win.resized_to == (668, 300)


def test_saving_records_the_current_size():
    win = _FakeWindow()
    win._w, win._h = 900, 700
    win._save_window_size()
    assert app_settings.get_main_size() == (900, 700)


def test_a_maximised_window_saves_the_size_it_restores_down_to():
    """Otherwise the next launch opens at the maximised size and un-maximising
    does nothing visible."""
    win = _FakeWindow()
    win._w, win._h = 1920, 1080      # currently filling the screen
    win._normal = QSize(820, 610)    # what the green/restore button returns to
    win._maximized = True
    win._save_window_size()
    assert app_settings.get_main_size() == (820, 610)


def test_a_maximised_window_with_no_normal_geometry_saves_nothing():
    """Rather than persisting a 0x0 that would restore to the minimum."""
    app_settings.set_main_size(880, 640)
    win = _FakeWindow()
    win._maximized = True
    win._normal = QSize(0, 0)
    win._save_window_size()
    assert app_settings.get_main_size() == (880, 640), "the good value survived"


# --- nothing resizes the window on its own ----------------------------------

def _src(fn):
    import inspect
    return inspect.getsource(fn)


def test_background_events_no_longer_resize_the_window():
    """The three triggers that made the window move while unattended.

    Source inspection: each of these is reached from a poll or a timer, so a
    behavioural test would have to stand up the whole polling stack. What
    matters is simply that none of them calls the snap any more.
    """
    for name, fn in (
        ("the rate-limit badge appearing/clearing",
         dashboard.Dashboard._apply_status_badge),
        ("a session starting or ending in the shelf",
         dashboard.Dashboard._apply_session_view),
    ):
        assert "_fit_window_height()" not in _src(fn), \
            f"{name} resizes the window behind the user's back again"


def test_switching_pages_no_longer_resizes():
    """Returning to the Dashboard used to re-snap the height, which threw away
    a height the user had chosen before wandering off to Settings."""
    assert "_fit_window_height()" not in _src(dashboard.Dashboard._show_page)


def test_the_only_callers_of_the_snap_are_deliberate():
    """First run, and the user double-clicking the title bar. If a third
    caller appears, automatic resizing has crept back in."""
    import inspect
    src = inspect.getsource(dashboard)
    callers = [ln.strip() for ln in src.splitlines()
               if "_fit_window_height()" in ln and not ln.strip().startswith("#")]
    assert len(callers) == 2, (
        f"expected exactly 2 deliberate callers (first-run snap, double-click "
        f"reset), found {len(callers)}: {callers}")


def test_first_run_snaps_but_a_restored_size_does_not():
    init = _src(dashboard.Dashboard.__init__)
    assert "if not self._size_restored:" in init, \
        "the first-run snap is no longer gated -- it will overwrite a restored size"
    assert "self._size_restored = self._restore_window_size()" in init


def test_double_click_to_fit_survives():
    """Kept on purpose: with nothing resizing the window automatically, this is
    now the only way back to a snug height."""
    assert "_fit_window_height()" in _src(dashboard.Dashboard.reset_to_fit)
    assert "reset_to_fit()" in _src(dashboard.TitleBar.mouseDoubleClickEvent)
