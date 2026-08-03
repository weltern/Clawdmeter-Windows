"""F8 — a sign-in launch must not pop a window just because the tray host is slow.

`Dashboard.__init__` snapshots `QSystemTrayIcon.isSystemTrayAvailable()`, and the
sign-in path used that snapshot to decide whether to show the window. At login
the app can beat the panel's tray host to the socket, so on a desktop that is
about to have a tray the snapshot is a false negative and the window pops every
time. `Dashboard.closeEvent` already re-queries for exactly this reason
(dashboard.py, "a tray host can register after startup"); the startup decision
did not.

These drive the real QTimer, so they measure the wait rather than assert its
shape. Headless via QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _Win:
    """The slice of Dashboard the startup decision touches."""

    def __init__(self, tray_available=False):
        self.tray_available = tray_available
        self.shown = 0

    def show_initial(self):
        self.shown += 1


def _pump(ms):
    done = [False]
    QTimer.singleShot(ms, lambda: done.__setitem__(0, True))
    while not done[0]:
        _app.processEvents()


def _appears_after(n_calls):
    """A tray that registers on the n-th poll — the login race, deterministically."""
    state = {"calls": 0}

    def available():
        state["calls"] += 1
        return state["calls"] > n_calls

    available.state = state
    return available


def test_a_tray_that_registers_late_does_not_pop_the_window():
    """THE regression. Un-fixed, the stale snapshot showed the window at once."""
    win = _Win(tray_available=False)
    main.show_unless_tray_appears(
        win, grace_ms=2000, step_ms=100, available=_appears_after(3))
    _pump(1200)

    assert win.shown == 0, "popped a window on a desktop that does have a tray"
    assert win.tray_available is True, "the stale snapshot was never corrected"


def test_a_desktop_with_no_tray_at_all_still_gets_its_window():
    """The behaviour the original code existed to provide must survive."""
    win = _Win(tray_available=False)
    main.show_unless_tray_appears(
        win, grace_ms=600, step_ms=100, available=lambda: False)
    _pump(1400)

    assert win.shown == 1, "a tray-less DE was left with no window — unrecoverable"


def test_a_tray_already_up_is_taken_immediately():
    """Snapshot stale, host already registered: no wait, no window."""
    win = _Win(tray_available=False)
    main.show_unless_tray_appears(
        win, grace_ms=5000, step_ms=100, available=lambda: True)

    assert win.shown == 0
    assert win.tray_available is True
    assert not hasattr(win, "_tray_wait_timer"), "started a needless poll"


def test_the_window_is_shown_only_once():
    """The timer must stop; a repeating poll would show the window every tick."""
    win = _Win(tray_available=False)
    main.show_unless_tray_appears(
        win, grace_ms=300, step_ms=100, available=lambda: False)
    _pump(1500)

    assert win.shown == 1, f"show_initial() called {win.shown}x — the timer never stopped"


def test_the_startup_path_calls_the_waiter_not_show_initial():
    """Wiring: main's sign-in branch must go through the wait.

    Matched on the AST so a mention in a comment or docstring cannot satisfy it.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(main.main)))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "show_unless_tray_appears" in called, (
        "the sign-in branch still decides straight from the __init__ snapshot")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
