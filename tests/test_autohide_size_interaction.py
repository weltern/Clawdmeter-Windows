"""The auto-hide title bar must not leak into the persisted window size.

Found by review after window-size persistence landed. The title bar's height is
transient in two ways -- it is 0 at rest when auto-hide is on, and animates to
TitleBar.HEIGHT whenever the cursor nears the top edge -- so the live window
height is not the height the user chose. Persisting it produced three separate
defects:

  1. The close button lives IN the title bar, so clicking it requires the
     cursor to be up there, which reveals the bar. _real_quit() then saved
     collapsed+48, which became the next launch's resting height, and the
     window grew another 48px every launch. Measured on the real windows
     platform: 447 -> 495 -> 543.
  2. _apply_auto_hide adjusted the window's own minimum by hand, during
     construction, before the layout had activated -- so minimumHeight() was
     still the explicit 0 and it set -48. Qt clamped that to 0 but kept the
     "explicitly set" flag, and the floor was never raised again: the window
     could be dragged to ~120px with the usage bars overlapping. Auto-fit used
     to heal it; nothing does now, so the broken height persisted forever.
  3. _target_window_height read title_bar.height() and treated a deliberately
     collapsed bar as "not laid out yet", adding 48px for a bar that is not
     there. window/main_size is a new key, so _size_restored is False for every
     existing user on the upgrade launch -- all of them got the phantom 48px
     once, which then seeded (1).

The fix is one idea: the stored height is normalised to "as if the title bar
were shown", and converted back on restore. Nothing else needs to know.
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
H = dashboard.TitleBar.HEIGHT


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    store = QSettings(str(tmp_path / "t.ini"), QSettings.IniFormat)
    monkeypatch.setattr(app_settings, "_settings", lambda: store)
    yield store


class _Win:
    """The slice of Dashboard the size normalisation touches."""

    _save_window_size = dashboard.Dashboard._save_window_size
    _restore_window_size = dashboard.Dashboard._restore_window_size
    _titlebar_height_now = dashboard.Dashboard._titlebar_height_now

    def __init__(self, *, auto_hide, height, bar_shown=False, collapsed=None):
        self._auto_hide_enabled = auto_hide
        self._h, self._w = height, 668
        self._collapsed_window_height = collapsed
        self._maximized = self._fullscreen = False
        self.resized_to = None

        class _Bar:
            def maximumHeight(_s):
                if not auto_hide:
                    return H
                return H if bar_shown else 0
        self.title_bar = _Bar()

    # -- geometry surface
    def width(self):
        return self._w

    def height(self):
        return self._h

    def size(self):
        return QSize(self._w, self._h)

    def isMaximized(self):
        return self._maximized

    def isFullScreen(self):
        return self._fullscreen

    def normalGeometry(self):
        return QRect(0, 0, 0, 0)

    def minimumWidth(self):
        return 566

    def minimumHeight(self):
        return 0

    def screen(self):
        class _S:
            def availableGeometry(_s):
                return QRect(0, 0, 3840, 2160)   # never the binding constraint
        return _S()

    def resize(self, w, h):
        self.resized_to = (w, h)
        self._w, self._h = w, h


# --- 1. the compounding growth -----------------------------------------------

def test_quitting_with_the_bar_revealed_saves_the_resting_height():
    """The exact 447 -> 495 -> 543 bug: the ✕ is in the bar, so quitting
    always happens with the bar revealed."""
    resting = 447
    win = _Win(auto_hide=True, height=resting + H, bar_shown=True,
               collapsed=resting)
    win._save_window_size()
    assert app_settings.get_main_size() == (668, resting + H), \
        "saved value must be the normalised (bar-shown) height, not collapsed+48+48"


def test_save_restore_is_a_fixed_point_under_auto_hide():
    """Three launches must not drift. This is the property that failed."""
    resting = 447
    for _ in range(3):
        win = _Win(auto_hide=True, height=resting + H, bar_shown=True,
                   collapsed=resting)
        win._save_window_size()
        nxt = _Win(auto_hide=True, height=0)
        nxt._restore_window_size()
        assert nxt.resized_to == (668, resting), \
            f"resting height drifted to {nxt.resized_to}"
        resting = nxt.resized_to[1]
    assert resting == 447


def test_a_save_landing_mid_reveal_still_records_the_resting_height():
    """The debounce can fire while the reveal animation is part-way."""
    win = _Win(auto_hide=True, height=447 + 20, bar_shown=True, collapsed=447)
    win._save_window_size()
    assert app_settings.get_main_size() == (668, 447 + H)


# --- 3. the phantom 48px on the upgrade launch -------------------------------

def test_the_stored_height_does_not_depend_on_auto_hide():
    """Saved with auto-hide on, restored with it off (or vice versa) must give
    the same visible content height -- that is what "normalised" buys."""
    win_on = _Win(auto_hide=True, height=400, bar_shown=False, collapsed=400)
    win_on._save_window_size()
    assert app_settings.get_main_size() == (668, 400 + H)

    off = _Win(auto_hide=False, height=0)
    off._restore_window_size()
    assert off.resized_to == (668, 400 + H), "with the bar shown it occupies 48px"

    on = _Win(auto_hide=True, height=0)
    on._restore_window_size()
    assert on.resized_to == (668, 400), "with the bar hidden those 48px are gone"


def test_auto_hide_off_round_trips_unchanged():
    win = _Win(auto_hide=False, height=610)
    win._save_window_size()
    assert app_settings.get_main_size() == (668, 610)
    back = _Win(auto_hide=False, height=0)
    back._restore_window_size()
    assert back.resized_to == (668, 610)


# --- 2. the missing height floor ---------------------------------------------

def test_apply_auto_hide_no_longer_adjusts_the_window_minimum():
    """Collapsing the bar drops the layout's minimumSizeHint by itself, so the
    hand-rolled arithmetic was redundant -- and destructive, because it ran
    before the layout had activated and pinned the floor at 0 permanently."""
    import inspect
    src = inspect.getsource(dashboard.Dashboard._apply_auto_hide)
    assert "self.setMinimumHeight(" not in src, (
        "_apply_auto_hide adjusts the window minimum again; during construction "
        "that pins it to 0 for the whole process and the window loses its floor")


def test_the_window_keeps_a_real_height_floor_with_auto_hide_on():
    """The behavioural half, on the real Qt platform: a collapsed title bar
    must still leave a floor that refuses to clip the usage bars."""
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

    # Collapse the bar the way _apply_auto_hide does -- and nothing else.
    bar.setMinimumHeight(0)
    bar.setMaximumHeight(0)
    host.layout().activate()
    _app.processEvents()
    without_bar = host.minimumSizeHint().height()

    assert without_bar >= 300, \
        "collapsing the bar must not drop the floor below the content"
    assert with_bar - without_bar == H, \
        "Qt already accounts for the bar; the manual adjustment was redundant"
    host.deleteLater()


class _FitWin:
    """Just enough Dashboard to compute a target height."""

    _target_window_height = dashboard.Dashboard._target_window_height

    def __init__(self, *, auto_hide, content_h=400):
        self._auto_hide_enabled = auto_hide
        self._shelf_active = False

        class _Bar:
            # What the REAL widget reports: a collapsed bar measures 0, which
            # the old `or TitleBar.HEIGHT` fallback misread as "not laid out".
            def height(_s):
                return 0 if auto_hide else H
        self.title_bar = _Bar()

        class _Content:
            def minimumSizeHint(_s):
                return QSize(0, content_h)
        self._content = _Content()


def test_the_first_run_snap_does_not_reserve_a_hidden_title_bar():
    """The phantom 48px every existing user gets on the upgrade launch.

    window/main_size is a new key, so _size_restored is False for everyone the
    first time they run this build -- meaning everyone with auto-hide on took
    the snap, and the snap added room for a bar that is collapsed to nothing.
    That extra 48px was then persisted and fed the compounding growth above.
    """
    assert _FitWin(auto_hide=True)._target_window_height() == 400, \
        "reserved space for a title bar that is collapsed to 0"
    assert _FitWin(auto_hide=False)._target_window_height() == 400 + H, \
        "a shown title bar does occupy its full height"


# --- 5. the snap must persist ------------------------------------------------

def test_the_fit_animation_schedules_a_save_when_it_finishes():
    """resizeEvent cannot: the animation's final QResizeEvent arrives while
    _fitting is still True, so the debounce never started and double-click --
    now the only manual fix -- was the change most likely to be lost."""
    import inspect
    src = inspect.getsource(dashboard.Dashboard._on_fit_anim_finished)
    assert "_size_save_timer.start()" in src, \
        "a snapped height is never scheduled for saving"
