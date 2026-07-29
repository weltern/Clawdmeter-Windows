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
#
# These used to match the literal string "_fit_window_height()" in the source.
# That was defeated by the most natural way anyone would reintroduce the bug:
#   QTimer.singleShot(0, self._fit_window_height)
# has no "()" after the name, so a deferred refit slipped past every one of
# them and all three background triggers could come back with the suite green.
# It also counted the name inside docstrings, so merely mentioning the method
# in prose broke the caller count.
#
# AST instead of text: an ast.Attribute node named _fit_window_height catches
# the call, the bare bound-method reference, and line-wrapped forms, and never
# sees docstrings or comments at all.

def _fit_refs(obj):
    """Every reference to _fit_window_height in `obj`, however it is written."""
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr == "_fit_window_height"]


@pytest.mark.parametrize("name,fn", [
    ("the rate-limit badge appearing/clearing",
     dashboard.Dashboard._apply_status_badge),
    ("a session starting or ending in the shelf",
     dashboard.Dashboard._apply_session_view),
    ("returning to the Dashboard page",
     dashboard.Dashboard._show_page),
])
def test_background_events_no_longer_resize_the_window(name, fn):
    assert _fit_refs(fn) == [], \
        f"{name} resizes the window behind the user's back again"


def test_the_only_references_to_the_snap_are_deliberate():
    """First run and the title-bar double-click. A third means automatic
    resizing has crept back in -- including via a deferred QTimer call, which
    the old string match could not see."""
    refs = _fit_refs(dashboard)
    assert len(refs) == 2, (
        f"expected exactly 2 references (first-run snap, double-click reset), "
        f"found {len(refs)} at lines {[n.lineno for n in refs]}")


def test_first_run_snaps_but_a_restored_size_does_not():
    """`_size_restored` must be assigned exactly once, from the restore, and
    the gate must come after it.

    A presence check alone was defeated by inserting `self._size_restored =
    False` above the gate: both strings stayed present, the snap ran on every
    launch, and the user's height was discarded each time.
    """
    import inspect
    init = inspect.getsource(dashboard.Dashboard.__init__)
    assign = "self._size_restored = self._restore_window_size()"
    gate = "if not self._size_restored:"
    assert init.count("self._size_restored =") == 1, \
        "_size_restored is assigned more than once; a later one can defeat the gate"
    assert assign in init and gate in init
    assert init.index(assign) < init.index(gate), \
        "the snap is gated before the restore has run, so the gate reads stale state"


class _ResetWin:
    """reset_to_fit against a fake, so the guard is exercised rather than read.

    A source check could not tell `self._fit_window_height()` from the same
    line wrapped in `if False:`.
    """

    reset_to_fit = dashboard.Dashboard.reset_to_fit

    def __init__(self, *, maximized=False):
        self._maximized = maximized
        self.fitted = False
        self.restored = False

    def isMaximized(self):
        return self._maximized

    def showNormal(self):
        self.restored = True
        self._maximized = False

    def _fit_window_height(self):
        self.fitted = True


def test_double_click_to_fit_actually_fits():
    """Kept on purpose: with nothing resizing automatically, this is now the
    only way back to a snug height."""
    win = _ResetWin()
    win.reset_to_fit()
    assert win.fitted is True


def test_double_click_un_maximises_first():
    """Fitting a maximised window would be a no-op -- _fit_window_height bails
    on isMaximized()."""
    win = _ResetWin(maximized=True)
    win.reset_to_fit()
    assert win.restored is True, "a maximised window must be restored down first"
    assert win.fitted is True


# --- the save path is actually wired up -------------------------------------
#
# Every test above calls _save_window_size() directly, so all of them would
# pass with nothing in the app ever calling it. Three separate one-line breaks
# used to ship a build that never remembered its size, with the suite green:
# the resizeEvent gate forced false, the timer's timeout never connected, and
# the _real_quit() flush deleted. The gate is now a testable predicate, and
# the two end-to-end tests below drive a real Dashboard.

@pytest.mark.parametrize("armed,fitting,expected", [
    (True,  False, True),    # a settled user resize -- the only case that saves
    (False, False, False),   # before the first show settles
    (True,  True,  False),   # mid snap-animation; saved by _on_fit_anim_finished
    (False, True,  False),
])
def test_should_persist_size(armed, fitting, expected):
    assert dashboard._should_persist_size(armed, fitting) is expected


@pytest.fixture
def live_dashboard():
    """A real Dashboard in mock mode -- no poller threads, no file watchers,
    and UsageHistory(persist=False), so nothing touches real state."""
    d = dashboard.Dashboard(mock=True)
    d.show()
    _app.processEvents()
    d._fit_armed = True          # stand in for the post-show singleShot
    try:
        yield d
    finally:
        d._size_save_timer.stop()
        if getattr(d, "_mock_sample_timer", None) is not None:
            d._mock_sample_timer.stop()
        d._countdown.stop()
        d.close()
        d.deleteLater()
        _app.processEvents()


def _pump(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_resizing_schedules_and_then_writes_the_size(live_dashboard):
    """End to end: the debounce starts on resize and the value reaches disk
    without anyone calling _save_window_size()."""
    d = live_dashboard
    target_w = d.width() + 90
    target_h = d.height() + 70
    d.resize(target_w, target_h)
    _app.processEvents()
    assert d._size_save_timer.isActive(), \
        "resizing did not schedule a save -- the size will never be remembered"

    _pump(d._size_save_timer.interval() + 250)
    saved = app_settings.get_main_size()
    assert saved is not None, "the debounce fired but nothing was written"
    assert saved[0] == target_w


def test_quitting_flushes_a_pending_save(live_dashboard, monkeypatch):
    """Quitting inside the debounce window must not drop the last resize."""
    d = live_dashboard
    monkeypatch.setattr(dashboard.QGuiApplication, "quit", staticmethod(lambda: None))
    target_w = d.width() + 120
    d.resize(target_w, d.height())
    _app.processEvents()
    assert d._size_save_timer.isActive(), "precondition: a save is pending"

    d._real_quit()               # quit patched out; everything else real
    saved = app_settings.get_main_size()
    assert saved is not None and saved[0] == target_w, \
        "_real_quit did not flush the pending size"
