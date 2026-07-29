"""Only a settled window size is ever remembered.

The window height is transient in several states -- the auto-hide title bar is
0 at rest and 48 while revealed, a maximised window is temporarily screen-sized,
a hidden window never had a real geometry at all. Three successive attempts to
NORMALISE those back to a resting height each fixed one case and broke another:

  1. saving the raw height grew the window 48px per launch (447 -> 495 -> 543),
     because the close button lives IN the title bar, so quitting always
     happens with the bar revealed;
  2. rebuilding from the collapsed baseline instead then clobbered the saved
     height on any launch that never showed the window -- a run-at-login start,
     or a session spent in compact/mini -- because Qt delivers no resizeEvent
     to a hidden widget, so the baseline was still the construction
     placeholder (measured: a good 748 overwritten with 520);
  3. and leaving normalGeometry() un-normalised walked the height 48px DOWN
     per launch when maximising with Win+Up (652 -> 604 -> 556 -> 508).

So the arithmetic is gone. _size_is_settled() refuses to look at the size in
any of those states, _remember_settled_size() snapshots it only when settled,
and the disk write just replays that snapshot. One normalisation survives --
storing the height as if the bar were shown -- so the value still means the
same thing if the user toggles auto-hide between sessions.

The snapshot and the write are deliberately separate. The last resize before
quitting is made at rest, but the quit itself happens with the bar revealed:
reading live geometry at quit time would either record the bar or, if it
refused outright, lose that final resize.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from PySide6.QtCore import QAbstractAnimation, QRect, QSize  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import app_settings  # noqa: E402
import dashboard  # noqa: E402

_app = QApplication.instance() or QApplication([])
H = dashboard.TitleBar.HEIGHT


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    store = QSettings(str(tmp_path / "t.ini"), QSettings.IniFormat)
    monkeypatch.setattr(app_settings, "_settings", lambda: store)
    yield store


class _Win:
    """The slice of Dashboard the size snapshot touches."""

    _size_is_settled = dashboard.Dashboard._size_is_settled
    _remember_settled_size = dashboard.Dashboard._remember_settled_size
    _save_window_size = dashboard.Dashboard._save_window_size
    _restore_window_size = dashboard.Dashboard._restore_window_size

    def __init__(self, *, auto_hide=False, height=400, bar_shown=False,
                 visible=True, maximized=False, fullscreen=False,
                 animating=False):
        self._auto_hide_enabled = auto_hide
        self._h, self._w = height, 668
        self._visible, self._maximized, self._fullscreen = (
            visible, maximized, fullscreen)
        self._last_settled_size = None
        self._collapsed_window_height = None
        self.resized_to = None

        class _Bar:
            def maximumHeight(_s):
                if not auto_hide:
                    return H
                return H if bar_shown else 0
        self.title_bar = _Bar()

        state = (QAbstractAnimation.Running if animating
                 else QAbstractAnimation.Stopped)

        class _Group:
            def state(_s):
                return state
        self._titlebar_anim_group = _Group()

    def isVisible(self):
        return self._visible

    def isMaximized(self):
        return self._maximized

    def isFullScreen(self):
        return self._fullscreen

    def height(self):
        return self._h

    def width(self):
        return self._w

    def minimumWidth(self):
        return 566

    def minimumHeight(self):
        return 0

    def screen(self):
        class _S:
            def availableGeometry(_s):
                return QRect(0, 0, 3840, 2160)
        return _S()

    def resize(self, w, h):
        self.resized_to = (w, h)
        self._w, self._h = w, h


# --- what counts as settled --------------------------------------------------

@pytest.mark.parametrize("kwargs,why", [
    (dict(visible=False), "never shown -- run-at-login, or a compact/mini session"),
    (dict(maximized=True), "maximised is temporary"),
    (dict(fullscreen=True), "full-screen is temporary"),
    (dict(auto_hide=True, animating=True), "mid reveal/hide animation"),
    (dict(auto_hide=True, bar_shown=True), "bar revealed -- height includes 48px of bar"),
])
def test_unsettled_states_are_not_remembered(kwargs, why):
    win = _Win(**kwargs)
    win._remember_settled_size()
    assert win._last_settled_size is None, f"recorded a size while {why}"


def test_a_resting_window_is_remembered():
    win = _Win(auto_hide=False, height=610)
    win._remember_settled_size()
    assert win._last_settled_size == (668, 610)


# --- 1. the compounding growth (447 -> 495 -> 543) ---------------------------

def test_quitting_with_the_bar_revealed_writes_the_resting_height():
    """The ✕ is inside the title bar, so quitting always reveals it."""
    win = _Win(auto_hide=True, height=419, bar_shown=False)
    win._remember_settled_size()             # the user's last resize, at rest

    win._h, win.title_bar = 419 + H, _Win(auto_hide=True, bar_shown=True).title_bar
    win._remember_settled_size()             # cursor moves to the ✕: ignored
    win._save_window_size()

    assert app_settings.get_main_size() == (668, 419 + H), \
        "the revealed bar leaked into the saved height"


def test_save_restore_is_a_fixed_point_under_auto_hide():
    resting = 419
    for _ in range(3):
        win = _Win(auto_hide=True, height=resting, bar_shown=False)
        win._remember_settled_size()
        win._save_window_size()
        nxt = _Win(auto_hide=True, height=0, visible=False)
        nxt._restore_window_size()
        assert nxt.resized_to == (668, resting), f"drifted to {nxt.resized_to}"
        resting = nxt.resized_to[1]
    assert resting == 419


# --- 2. the never-shown launch clobbering a good height ----------------------

def test_a_launch_that_never_shows_the_window_keeps_the_saved_size():
    """Run-at-login, or a session spent in compact/mini, then quit from the
    tray. Measured before the fix: a saved 748 was overwritten with 520."""
    app_settings.set_main_size(668, 748)
    win = _Win(auto_hide=True, height=472, visible=False)
    win._restore_window_size()
    win._remember_settled_size()             # window was never shown
    win._save_window_size()
    assert app_settings.get_main_size() == (668, 748), \
        "a hidden window overwrote a size the user actually chose"


def test_restoring_updates_the_collapsed_baseline():
    """Qt delivers no resizeEvent to an unshown window, so the baseline would
    otherwise stay at the construction placeholder and drive a phantom reveal
    to the wrong height."""
    app_settings.set_main_size(668, 748)
    win = _Win(auto_hide=True, height=472, visible=False)
    win._collapsed_window_height = 472
    win._restore_window_size()
    assert win._collapsed_window_height == 748 - H


# --- 3. maximising walking the height downhill -------------------------------

def test_maximising_does_not_drift_the_saved_height():
    """Win+Up with the bar collapsed. Measured before the fix, four launches:
    652 -> 604 -> 556 -> 508, running down to the layout floor."""
    height = 652
    for _ in range(4):
        win = _Win(auto_hide=True, height=height, bar_shown=False)
        win._remember_settled_size()          # settled, pre-maximise
        win._save_window_size()
        win._maximized = True                 # Win+Up
        win._h = 1392
        win._remember_settled_size()          # must be ignored
        win._save_window_size()

        nxt = _Win(auto_hide=True, height=0, visible=False)
        nxt._restore_window_size()
        assert nxt.resized_to == (668, height), f"drifted to {nxt.resized_to}"
        height = nxt.resized_to[1]
    assert height == 652


# --- the surviving normalisation ---------------------------------------------

def test_the_stored_height_does_not_depend_on_auto_hide():
    """Saved with auto-hide on, restored with it off must give the same visible
    content -- the user can toggle it in Settings between sessions."""
    win = _Win(auto_hide=True, height=400, bar_shown=False)
    win._remember_settled_size()
    win._save_window_size()
    assert app_settings.get_main_size() == (668, 400 + H)

    off = _Win(auto_hide=False, height=0, visible=False)
    off._restore_window_size()
    assert off.resized_to == (668, 400 + H), "the shown bar occupies 48px"

    on = _Win(auto_hide=True, height=0, visible=False)
    on._restore_window_size()
    assert on.resized_to == (668, 400), "the hidden bar occupies none"


def test_auto_hide_off_round_trips_unchanged():
    win = _Win(auto_hide=False, height=610)
    win._remember_settled_size()
    win._save_window_size()
    back = _Win(auto_hide=False, height=0, visible=False)
    back._restore_window_size()
    assert back.resized_to == (668, 610)


# --- the missing height floor ------------------------------------------------

def test_apply_auto_hide_no_longer_adjusts_the_window_minimum():
    """Collapsing the bar drops the layout's minimumSizeHint by itself. Doing
    it by hand ran before the layout had activated and pinned the floor at 0
    for the whole process, so the window could be dragged to ~120px."""
    import inspect
    src = inspect.getsource(dashboard.Dashboard._apply_auto_hide)
    assert "self.setMinimumHeight(" not in src, \
        "_apply_auto_hide adjusts the window minimum again; that removes the floor"


def test_the_window_keeps_a_real_height_floor_with_auto_hide_on():
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    bar = QWidget()
    bar.setMinimumHeight(H)
    bar.setMaximumHeight(H)
    body = QLabel("x")
    body.setMinimumHeight(300)
    lay.addWidget(bar)
    lay.addWidget(body)
    host.setMinimumSize(566, 0)
    host.show()
    _app.processEvents()
    with_bar = host.minimumSizeHint().height()

    bar.setMinimumHeight(0)
    bar.setMaximumHeight(0)
    host.layout().activate()
    _app.processEvents()
    without_bar = host.minimumSizeHint().height()

    assert without_bar >= 300, "the floor must not drop below the content"
    assert with_bar - without_bar == H, "Qt already accounts for the bar"
    host.deleteLater()


# --- the first-run snap ------------------------------------------------------

class _FitWin:
    _target_window_height = dashboard.Dashboard._target_window_height

    def __init__(self, *, auto_hide, content_h=400):
        self._auto_hide_enabled = auto_hide
        self._shelf_active = False

        class _Bar:
            def height(_s):
                return 0 if auto_hide else H
        self.title_bar = _Bar()

        class _Content:
            def minimumSizeHint(_s):
                return QSize(0, content_h)
        self._content = _Content()


def test_the_first_run_snap_does_not_reserve_a_hidden_title_bar():
    """window/main_size is a new key, so every existing user takes the snap
    once on the upgrade launch -- and it used to add 48px for a bar collapsed
    to nothing, which then seeded the compounding growth."""
    assert _FitWin(auto_hide=True)._target_window_height() == 400, \
        "reserved space for a title bar that is collapsed to 0"
    assert _FitWin(auto_hide=False)._target_window_height() == 400 + H


def test_the_fit_animation_snapshots_and_schedules_a_save():
    """resizeEvent cannot: the animation's final QResizeEvent arrives while
    _fitting is still True."""
    import inspect
    src = inspect.getsource(dashboard.Dashboard._on_fit_anim_finished)
    assert "_remember_settled_size()" in src, "the snapped height is never captured"
    assert "_size_save_timer.start()" in src, "the snapped height is never scheduled"
